"""Live Admin directories for the unified Academic MVP tables."""
import json
from datetime import date, datetime, time, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select, true
from sqlalchemy.orm import Session, selectinload

from app.core.mvp_security import require_mvp_role
from app.database.mvp_session import get_mvp_db
from app.models.mvp import (
    MVPAdminAuditLog, MVPBooking, MVPBookingPayment, MVPListing, MVPListingVerification,
    MVPProviderProfile, MVPUser,
)
from app.schemas.admin_travelers import TravelerStatusUpdate
from app.services import mvp_listing_verification as verification
from app.services.mvp_account import lock_user
from app.services.mvp_wallet import totals

router = APIRouter(prefix="/admin", tags=["MVP Admin Directories"])
DB = Annotated[Session, Depends(get_mvp_db)]
Admin = Annotated[MVPUser, Depends(require_mvp_role("admin"))]
Page = Annotated[int, Query(ge=1, le=100000)]
Size = Annotated[int, Query(ge=1, le=100)]
CATEGORIES = {
    "homestay": ("homestay",), "rental": ("bike", "bicycle"),
    "tour_guide": ("guide",), "activity": ("adventure",), "artisan": ("artisan",),
}
PUBLIC_CATEGORY = {item: key for key, values in CATEGORIES.items() for item in values}
PAYMENT_STATUS = {
    "demo_succeeded": "captured", "demo_failed": "failed",
    "demo_pending": "pending", "unpaid": "cash_on_delivery",
}


def _page(db, query, page, size, transform):
    total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
    rows = db.scalars(query.offset((page - 1) * size).limit(size)).all()
    return {"items": [transform(row) for row in rows], "total": total, "page": page,
            "page_size": size, "limit": size, "total_pages": (total + size - 1) // size}


def _traveler(user):
    return {key: getattr(user, key) for key in ("id", "full_name", "email", "is_active", "created_at")}


@router.get("/travelers")
def travelers(db: DB, admin: Admin, page: Page = 1, page_size: Size = 20,
              is_active: bool | None = None, search: str = ""):
    query = select(MVPUser).where(MVPUser.role == "traveler")
    if is_active is not None:
        query = query.where(MVPUser.is_active == is_active)
    if search.strip():
        term = f"%{search.strip()}%"
        query = query.where(or_(MVPUser.full_name.ilike(term), MVPUser.email.ilike(term)))
    return _page(db, query.order_by(MVPUser.id.desc()), page, page_size, _traveler)


@router.get("/travelers/{user_id}")
def traveler(user_id: int, db: DB, admin: Admin):
    user = db.get(MVPUser, user_id)
    if not user or user.role != "traveler":
        raise HTTPException(404, "Traveler not found")
    return _traveler(user)


@router.get("/travelers/{user_id}/history")
def traveler_history(user_id: int, db: DB, admin: Admin):
    traveler(user_id, db, admin)
    events = db.scalars(select(MVPAdminAuditLog).where(
        MVPAdminAuditLog.subject_user_id == user_id, MVPAdminAuditLog.action == "user_status",
    ).order_by(MVPAdminAuditLog.id.desc())).all()
    result = []
    for event in events:
        try:
            details = json.loads(event.reason)
            if not isinstance(details, dict) or "previous_is_active" not in details:
                continue
        except ValueError:
            continue
        result.append({"admin_user_id": event.actor_id, "created_at": event.occurred_at, **details})
    return result


@router.patch("/travelers/{user_id}/status")
def traveler_status(
    user_id: int, payload: TravelerStatusUpdate, db: DB,
    admin: Annotated[MVPUser, Depends(require_mvp_role("super_admin"))],
):
    user = lock_user(db, user_id)
    if not user or user.role != "traveler":
        raise HTTPException(404, "Traveler not found")
    if user.is_active != payload.is_active:
        details = {"previous_is_active": user.is_active, **payload.model_dump()}
        reason = json.dumps(details, ensure_ascii=False)
        if len(reason.encode("utf-16-le")) // 2 > 500:
            raise HTTPException(422, "Status note is too long for the audit log")
        db.add(MVPAdminAuditLog(actor_id=admin.id, action="user_status",
                               subject_user_id=user_id, reason=reason))
        user.is_active = payload.is_active
        user.auth_version += 1
        user.credential_version += 1
        db.commit()
    return _traveler(user)


@router.get("/listing-directory")
def listings(db: DB, admin: Admin, category: str, page: Page = 1, page_size: Size = 20,
             status: str = "", district: str = "", search: str = "", vehicle_type: str = "",
             provider_id: int | None = None, verification_status: str = "",
             province: str = "", activity_category: str = "", craft_category: str = "",
             is_active: bool | None = None):
    if category not in CATEGORIES:
        raise HTTPException(422, "Unsupported listing category")
    verification.require_schema(db)
    base = select(MVPListing).where(MVPListing.category.in_(CATEGORIES[category]))
    counts = dict(db.execute(select(MVPListing.approval_status, func.count()).where(
        MVPListing.category.in_(CATEGORIES[category])
    ).group_by(MVPListing.approval_status)).all())
    if status and status != "all":
        base = base.where(MVPListing.approval_status == (
            "evidence" if status == "more_evidence_required" else status
        ))
    if district and district != "all":
        base = base.where(MVPListing.district == district)
    if category == "rental" and vehicle_type and vehicle_type != "all":
        base = base.where(MVPListing.category == vehicle_type)
    if provider_id is not None:
        base = base.where(MVPListing.provider_id == provider_id)
    if verification_status and verification_status != "all":
        state = {"verified": "approved", "unverified": "draft"}.get(
            verification_status, verification_status
        )
        base = base.where(MVPListing.approval_status == state)
    if is_active is not None:
        base = base.where(MVPListing.is_active == is_active)
    if province or activity_category or craft_category:
        json_field = (func.json_value if db.get_bind().dialect.name == "mssql"
                      else func.json_extract)
        base = base.outerjoin(MVPListingVerification,
                              MVPListingVerification.listing_id == MVPListing.id)
        if province:
            base = base.where(json_field(MVPListingVerification.details, "$.province") == province)
        if activity_category:
            base = base.where(json_field(MVPListingVerification.details, "$.activity.category")
                              == activity_category)
        if craft_category:
            base = base.where(json_field(MVPListingVerification.details, "$.artisan.craft_category")
                              == craft_category)
    if search.strip():
        term = f"%{search.strip()}%"
        base = base.where(or_(MVPListing.title.ilike(term), MVPListing.locality.ilike(term)))
    result = _page(db, base.order_by(MVPListing.id.desc()), page, page_size, lambda row: {
        **verification.review(db, row).model_dump(mode="json"),
        "provider": {"id": row.provider_id, "business_name": row.provider.business_name,
                     "email": row.provider.user.email, "phone": row.provider.phone,
                     "verification_status": row.provider.verification_status},
    })
    result["counts"] = counts
    result["active"] = db.scalar(select(func.count()).select_from(MVPListing).where(
        MVPListing.category.in_(CATEGORIES[category]), MVPListing.is_active == true()
    ))
    result["providers"] = db.scalar(select(func.count(func.distinct(MVPListing.provider_id))).where(
        MVPListing.category.in_(CATEGORIES[category])
    ))
    return result


def _filter(query, category, search, date_from, date_to, timestamp):
    if category and category != "all":
        query = query.where(MVPListing.category.in_(CATEGORIES.get(category, ())))
    if search.strip():
        needle = search.strip()
        term = f"%{needle}%"
        terms = [MVPUser.full_name.ilike(term), MVPProviderProfile.business_name.ilike(term),
                 MVPListing.title.ilike(term)]
        suffix = needle.rsplit("-", 1)[-1]
        if suffix.isascii() and suffix.isdigit() and len(suffix) <= 18:
            terms.append(MVPBooking.id == int(suffix))
        query = query.where(or_(*terms))
    if date_from:
        query = query.where(timestamp >= datetime.combine(date_from, time.min))
    if date_to and date_to < date.max:
        query = query.where(timestamp < datetime.combine(date_to + timedelta(days=1), time.min))
    return query


def _booking_query(model):
    query = select(model)
    if model is MVPBookingPayment:
        query = query.join(MVPBookingPayment.booking)
    return query.join(MVPBooking.listing).join(MVPListing.provider).join(MVPBooking.traveler)


def _booking(row):
    listing, traveler = row.listing, row.traveler
    provider = listing.provider
    latest = max(row.payments, key=lambda payment: payment.attempt_no, default=None)
    return {
        "id": row.id, "booking_reference": row.booking_reference or f"BK-{row.id}",
        "category": PUBLIC_CATEGORY[listing.category], "traveler_id": traveler.id,
        "traveler_name": traveler.full_name, "traveler_email": traveler.email,
        "traveler_phone": traveler.phone, "provider_id": provider.id,
        "provider_name": provider.business_name, "service_title": listing.title,
        "start_date": row.starts_at, "end_date": row.ends_at, "amount": row.total_amount,
        "currency": row.currency, "booking_status": row.status, "created_at": row.created_at,
        "payment_status": PAYMENT_STATUS[latest.status] if latest else "unpaid",
        "payment_method": latest.method if latest else None,
    }


@router.get("/bookings")
def bookings(db: DB, admin: Admin, page: Page = 1, page_size: Size = 20,
             category: str = "", booking_status: str = "", search: str = "",
             date_from: date | None = None, date_to: date | None = None):
    query = _filter(_booking_query(MVPBooking), category, search, date_from, date_to,
                    MVPBooking.created_at)
    if booking_status and booking_status != "all":
        query = query.where(MVPBooking.status == booking_status)
    query = query.options(selectinload(MVPBooking.payments)).order_by(MVPBooking.id.desc())
    return _page(db, query, page, page_size, _booking)


@router.get("/bookings/summary")
def booking_summary(db: DB, admin: Admin):
    counts = dict(db.execute(select(MVPBooking.status, func.count())
                             .group_by(MVPBooking.status)).all())
    return {"total_bookings": sum(counts.values()), "confirmed_bookings": counts.get("confirmed", 0),
            "pending_bookings": counts.get("pending", 0), "completed_bookings": counts.get("completed", 0),
            "cancelled_or_disputed": counts.get("cancelled", 0)}


@router.get("/bookings/{booking_id}")
def booking(booking_id: int, db: DB, admin: Admin):
    row = db.get(MVPBooking, booking_id)
    if not row:
        raise HTTPException(404, "Booking not found")
    return {**_booking(row), "number_of_guests": row.quantity, "district": row.listing.district,
            "location_name": row.listing.locality,
            "audit_history": [{"status": event.to_status, "changed_at": event.occurred_at,
                               "notes": event.reason} for event in
                              sorted(row.status_history, key=lambda item: item.id)]}


@router.get("/payments")
def payments(db: DB, admin: Admin, page: Page = 1, page_size: Size = 20,
             category: str = "", status: str = "", search: str = "",
             date_from: date | None = None, date_to: date | None = None):
    query = _filter(_booking_query(MVPBookingPayment), category, search, date_from, date_to,
                    MVPBookingPayment.created_at)
    if status and status != "all":
        query = query.where(MVPBookingPayment.status.in_(
            [raw for raw, public in PAYMENT_STATUS.items() if status == public or status == raw]
        ))
    def item(payment):
        row = payment.booking
        listing, traveler = row.listing, row.traveler
        return {"id": payment.id, "transaction_reference": f"TX-{payment.id}",
                "booking_reference": row.booking_reference or f"BK-{row.id}",
                "category": PUBLIC_CATEGORY[listing.category], "traveler_id": traveler.id,
                "traveler_name": traveler.full_name, "provider_id": listing.provider_id,
                "provider_name": listing.provider.business_name, "amount": payment.display_amount,
                "currency": payment.currency, "payment_method": payment.method,
                "payment_status": PAYMENT_STATUS[payment.status], "refund_status": None,
                "created_at": payment.created_at, "updated_at": payment.resolved_at}
    return _page(db, query.order_by(MVPBookingPayment.id.desc()), page, page_size, item)


@router.get("/payments/summary")
def payment_summary(db: DB, admin: Admin):
    captured = db.scalar(select(func.coalesce(func.sum(MVPBookingPayment.display_amount), 0))
                         .where(MVPBookingPayment.status == "demo_succeeded"))
    wallet = totals(db)
    return {"total_gross_booking_revenue": captured, "currency": "LKR",
            "total_ceylongo_commission": wallet["total_commission_deducted"]
            - wallet["total_commission_refunded"]}

@router.get("/audit-log-directory")
def audit_logs(
    db: DB, admin: Annotated[MVPUser, Depends(require_mvp_role("super_admin"))],
    page: Page = 1, page_size: Size = 20, action_type: str = "", entity_type: str = "",
    search: str = "", date_from: date | None = None, date_to: date | None = None,
    admin_user_id: int | None = None,
):
    query = select(MVPAdminAuditLog)
    subjects = {
        "user": MVPAdminAuditLog.subject_user_id, "provider": MVPAdminAuditLog.subject_provider_id,
        "listing": MVPAdminAuditLog.subject_listing_id, "post": MVPAdminAuditLog.subject_post_id,
    }
    if action_type and action_type != "all":
        query = query.where(MVPAdminAuditLog.action == action_type)
    if entity_type in subjects:
        query = query.where(subjects[entity_type].is_not(None))
    if search.strip():
        query = query.where(MVPAdminAuditLog.reason.ilike(f"%{search.strip()}%"))
    if admin_user_id:
        query = query.where(MVPAdminAuditLog.actor_id == admin_user_id)
    if date_from:
        query = query.where(MVPAdminAuditLog.occurred_at >= datetime.combine(date_from, time.min))
    if date_to and date_to < date.max:
        query = query.where(MVPAdminAuditLog.occurred_at <
                            datetime.combine(date_to + timedelta(days=1), time.min))

    def item(row):
        subject, identity = next((
            (name, getattr(row, column.key)) for name, column in subjects.items()
            if getattr(row, column.key) is not None
        ), ("", None))
        return {"id": row.id, "admin_user_id": row.actor_id, "action_type": row.action,
                "entity_type": subject, "entity_id": identity, "details": {"reason": row.reason},
                "created_at": row.occurred_at}

    return _page(db, query.order_by(MVPAdminAuditLog.id.desc()), page, page_size, item)
