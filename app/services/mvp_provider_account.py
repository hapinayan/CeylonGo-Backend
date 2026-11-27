"""Provider profile, reviewed changes and dashboard backed by the MVP schema."""

import json
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import case, func, select, true
from sqlalchemy.orm import selectinload

from app.models.mvp import MVPAdminAuditLog, MVPBooking, MVPListing, MVPProviderProfile
from app.schemas.mvp_provider_account import (
    ProviderAccountProfile,
    ProviderDashboard,
    ProviderSettings,
)
from app.services import mvp_account, mvp_homestay, mvp_wallet as wallet
from app.services import mvp_provider_registration as registration

MATERIAL = {
    "full_name",
    "personal_address",
    "personal_district",
    "business_name",
    "business_category",
    "business_address",
    "business_district",
}


def own_profile(db, user, *, write=False):
    profile = db.scalar(
        select(MVPProviderProfile).where(
            MVPProviderProfile.user_id == user.id,
        )
    )
    if profile is None:
        raise HTTPException(404, "No provider profile registered.")
    if write:
        profile = wallet.lock_profile(db, profile.id)
        wallet.require_eligible(profile, user)
    return profile


def details(db, profile):
    registration.require_draft_storage(db)
    return registration.draft_data(profile)


def save(db, profile, data):
    profile.registration_details = json.dumps(data, ensure_ascii=False)
    profile.updated_at = mvp_account.now()
    db.commit()


def profile_response(db, user, profile):
    data = details(db, profile)
    personal = data.get("personal", {})
    business = data.get("business", {})
    avatar = data.get("account", {}).get("avatar")
    return ProviderAccountProfile(
        **{
            name: getattr(profile, name)
            for name in (
                "id",
                "user_id",
                "business_name",
                "phone",
                "district",
                "verification_status",
                "submitted_at",
                "decided_at",
                "decision_reason",
                "created_at",
            )
        },
        email=user.email,
        full_name=user.full_name,
        is_active=user.is_active,
        personal_phone=user.phone,
        personal_address=personal.get("permanent_landmark"),
        personal_district=personal.get("permanent_district"),
        business_category=business.get("provider_type"),
        business_description=business.get("description"),
        business_phone=profile.phone,
        business_address=(
            personal.get("permanent_landmark")
            if business.get("same_as_permanent")
            else business.get("business_landmark")
        ),
        business_district=profile.district,
        website_url=business.get("website_url"),
        social_links=business.get("social_links", []),
        approval_status=profile.verification_status,
        avatar_url=f"/api/v1/providers/me/avatar?v={avatar[:-4]}" if avatar else None,
        profile_change=data.get("profile_change"),
    )


def _apply_fields(user, profile, data, changes):
    personal = data.setdefault("personal", {})
    business = data.setdefault("business", {})
    if "full_name" in changes:
        user.full_name = personal["full_name"] = changes["full_name"]
    if "personal_phone" in changes:
        user.phone = personal["phone"] = changes["personal_phone"]
    if "business_name" in changes:
        profile.business_name = business["business_name"] = changes["business_name"]
    if "business_phone" in changes:
        profile.phone = business["secondary_phone"] = changes["business_phone"]
    for name, key in (
        ("personal_address", "permanent_landmark"),
        ("personal_district", "permanent_district"),
    ):
        if name in changes:
            personal[key] = changes[name]
    for name, key in (
        ("business_category", "provider_type"),
        ("business_address", "business_landmark"),
        ("business_district", "business_district"),
        ("business_description", "description"),
        ("website_url", "website_url"),
        ("social_links", "social_links"),
    ):
        if name in changes:
            business[key] = changes[name]
    if {"business_address", "business_district"} & changes.keys():
        if business.get("same_as_permanent"):
            for field in ("province", "city", "postal_code"):
                business[f"business_{field}"] = personal.get(f"permanent_{field}")
            business["business_district"] = changes.get(
                "business_district", profile.district
            )
            if "business_address" not in changes:
                business["business_landmark"] = personal.get("permanent_landmark")
        business["same_as_permanent"] = False
    if "business_district" in changes:
        profile.district = changes["business_district"]
    elif business.get("same_as_permanent") and changes.get("personal_district"):
        profile.district = changes["personal_district"]
    if {"full_name", "personal_phone"} & changes.keys():
        user.updated_at = mvp_account.now()


def update_profile(db, user, payload):
    profile = own_profile(db, user, write=True)
    data = details(db, profile)
    changes = payload.model_dump(exclude_unset=True)
    current = profile_response(db, user, profile)
    material = {
        key: value
        for key, value in changes.items()
        if key in MATERIAL and value != getattr(current, key)
    }
    if material:
        if data.get("profile_change", {}).get("status") == "pending":
            raise HTTPException(
                409, "A profile change is already awaiting admin review"
            )
        data["profile_change"] = {
            "request_id": str(uuid4()),
            "status": "pending",
            "changes": material,
            "requested_at": mvp_account.now().isoformat(),
        }
    routine = {key: value for key, value in changes.items() if key not in MATERIAL}
    if routine:
        _apply_fields(user, profile, data, routine)
    if material or routine:
        save(db, profile, data)
    return profile_response(db, user, profile)


def review_change(db, admin, provider_id, decision):
    profile = wallet.lock_profile(db, provider_id)
    if profile is None:
        raise HTTPException(404, "Provider profile not found")
    if profile.verification_status != "approved" or not profile.user.is_active:
        raise HTTPException(
            409, "Only active approved providers can have changes reviewed"
        )
    data = details(db, profile)
    change = data.get("profile_change")
    if (
        not change
        or change["status"] != "pending"
        or change["request_id"] != str(decision.profile_change_request_id)
    ):
        raise HTTPException(409, "Profile change request is missing or has changed")
    if decision.verification_status == "approved":
        _apply_fields(profile.user, profile, data, change["changes"])
    stamp = mvp_account.now()
    change.update(
        status=decision.verification_status,
        decided_at=stamp.isoformat(),
        decision_reason=decision.decision_reason,
    )
    db.add(
        MVPAdminAuditLog(
            actor_id=admin.id,
            action="provider_decision",
            subject_provider_id=profile.id,
            reason=f"Profile change {change['request_id']} {change['status']}",
            occurred_at=stamp,
        )
    )
    save(db, profile, data)
    return profile


def settings(data):
    values = data.get("account", {}).get("settings", {})
    return ProviderSettings.model_validate(values)


def dashboard(db, user):
    profile = own_profile(db, user)
    wallet.require_eligible(profile, user)
    limit = settings(details(db, profile)).dashboard_booking_limit
    listing = MVPListing
    booking = MVPBooking
    service_counts = db.execute(
        select(
            func.count(listing.id),
            func.count(
                case(
                    (
                        (listing.approval_status == "approved")
                        & (listing.is_active == true()),
                        1,
                    )
                )
            ),
            func.count(case((listing.approval_status.in_(("pending", "evidence")), 1))),
            func.count(case((listing.approval_status == "rejected", 1))),
        ).where(listing.provider_id == profile.id)
    ).one()
    states = dict(
        db.execute(
            select(booking.status, func.count(booking.id))
            .join(listing)
            .where(listing.provider_id == profile.id)
            .group_by(booking.status)
        ).all()
    )
    owned = (
        select(booking)
        .join(listing)
        .where(listing.provider_id == profile.id)
        .options(
            selectinload(booking.listing),
            selectinload(booking.payments),
            selectinload(booking.allocations),
        )
    )
    recent = db.scalars(
        owned.order_by(booking.created_at.desc(), booking.id.desc()).limit(limit)
    ).all()
    upcoming = db.scalars(
        owned.where(
            booking.status == "confirmed",
            booking.starts_at >= mvp_account.now(),
        )
        .order_by(booking.starts_at, booking.id)
        .limit(limit)
    ).all()
    row = wallet.get_wallet(db, profile.id)
    if row is not None:
        summary = wallet.summary(row, wallet.totals(db, [row.id]).get(row.id, {}))
    else:
        summary = {
            "balance": wallet.ZERO,
            "net_commission_paid": wallet.ZERO,
            "free_bookings_remaining": wallet.FREE_LIMIT
            - wallet.historical_usage(db, profile.id),
        }
    return ProviderDashboard(
        total_services=service_counts[0],
        published_services=service_counts[1],
        pending_verification_services=service_counts[2],
        rejected_services=service_counts[3],
        total_bookings=sum(states.values()),
        pending_bookings=states.get("pending", 0),
        confirmed_bookings=states.get("confirmed", 0),
        completed_bookings=states.get("completed", 0),
        cancelled_bookings=states.get("cancelled", 0),
        recent_bookings=[mvp_homestay.booking_details(item) for item in recent],
        upcoming_bookings=[mvp_homestay.booking_details(item) for item in upcoming],
        wallet_balance=summary["balance"],
        free_bookings_remaining=summary["free_bookings_remaining"],
        net_commission_paid=summary["net_commission_paid"],
    )
