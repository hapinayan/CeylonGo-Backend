from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import case, literal, null, select, true, union_all, update
from sqlalchemy.orm import Session

from app.core.audit import sanitize_audit_details
from app.core.listing_sync import sync_homestay_listing_reference
from app.models.homestay import Homestay
from app.models.provider_profile import ProviderProfile
from app.models.rental import RentalLocation
from app.models.shared import AdminAuditLog
from app.models.tour_guide import TourGuide
from app.schemas.interactive_map import (
    LocationCorrection,
    LocationReview,
    LocationReviewPage,
)
from app.services.map_catalog import SOURCES


def review_query(module):
    source = SOURCES[module]
    model = source.model
    if module == "rental":
        name, active = model.location_name, model.is_active
        status = case((active == true(), "approved"), else_="inactive")
    elif module == "eco_experience":
        name, status, active = model.title, model.listing_status, model.is_published
    elif module == "tour_guide":
        name, status, active = (
            ProviderProfile.business_name,
            model.approval_status,
            model.is_active,
        )
    else:
        name = model.name if module == "homestay" else model.title
        status, active = model.approval_status, model.is_active
    query = select(
        literal(module).label("module_type"),
        model.id.label("listing_id"),
        name.label("name"),
        getattr(model, source.latitude).label("latitude"),
        getattr(model, source.longitude).label("longitude"),
        status.label("approval_status"),
        case((active == true(), 1), else_=0).label("active"),
        (
            null()
            if module == "tour_guide"
            else (model.meeting_point if module == "activity" else model.address)
        ).label("address"),
        (model.base_city if module == "tour_guide" else model.city).label("city"),
        (model.base_district if module == "tour_guide" else model.district).label(
            "district"
        ),
    ).select_from(model)
    if module == "tour_guide":
        query = query.join(
            ProviderProfile, model.provider_profile_id == ProviderProfile.id
        )
    return query


def review_locations(db: Session, module, page, page_size) -> LocationReviewPage:
    modules = (module,) if module else SOURCES
    source = union_all(*(review_query(name) for name in modules)).subquery()
    query = (
        select(source)
        .order_by(source.c.module_type, source.c.listing_id)
        .offset((page - 1) * page_size)
        .limit(page_size + 1)
    )
    rows = db.execute(query).mappings().all()
    return LocationReviewPage(
        items=[LocationReview(**row) for row in rows[:page_size]],
        page=page,
        page_size=page_size,
        has_more=len(rows) > page_size,
    )


def record_location_correction(db, admin_id, entity, listing_id, before, after, reason):
    db.add(
        AdminAuditLog(
            admin_user_id=admin_id,
            action="map_location_corrected",
            entity_name=entity,
            entity_id=listing_id,
            details=sanitize_audit_details(
                {
                    "before": [
                        str(value) if value is not None else None for value in before
                    ],
                    "after": [
                        str(value) if value is not None else None for value in after
                    ],
                    "reason": reason,
                }
            ),
        )
    )


def correct_location(
    db: Session,
    module,
    listing_id: int,
    payload: LocationCorrection,
    admin_id: int,
) -> LocationReview:
    source = SOURCES[module]
    model = source.model
    lat, lon = getattr(model, source.latitude), getattr(model, source.longitude)
    snapshot = review_query(module).where(model.id == listing_id)
    previous = db.execute(snapshot).mappings().one_or_none()
    if previous is None:
        raise HTTPException(404, detail={"code": "map_location_not_found"})
    before = (previous["latitude"], previous["longitude"])
    after = (payload.latitude, payload.longitude)
    if before == after:
        return LocationReview(**previous)
    try:
        result = db.execute(
            update(model)
            .where(model.id == listing_id, lat == before[0], lon == before[1])
            .values({lat: after[0], lon: after[1]}),
        )
        if result.rowcount != 1:
            raise HTTPException(409, detail={"code": "map_location_changed"})
        if model is Homestay:
            sync_homestay_listing_reference(db, db.get(Homestay, listing_id))
        record_location_correction(
            db,
            admin_id,
            model.__tablename__,
            listing_id,
            before,
            after,
            payload.reason,
        )
        corrected = LocationReview(
            **{**previous, "latitude": after[0], "longitude": after[1]}
        )
        db.commit()
        return corrected
    except Exception:
        db.rollback()
        raise


def decide_location(db, module, listing_id, payload, admin_id):
    source = SOURCES[module]
    model = source.model
    snapshot = review_query(module).where(model.id == listing_id)
    previous = db.execute(snapshot).mappings().one_or_none()
    if previous is None:
        raise HTTPException(404, detail={"code": "map_location_not_found"})
    if model is TourGuide and previous["approval_status"] != "pending":
        raise HTTPException(409, detail={"code": "map_location_not_pending"})
    lat, lon = previous["latitude"], previous["longitude"]
    valid = (
        lat is not None and lon is not None and -90 <= lat <= 90 and -180 <= lon <= 180
    )
    if payload.decision == "approved" and not valid:
        raise HTTPException(422, detail={"code": "map_coordinates_missing"})
    active = payload.decision == "approved"
    checks = [
        model.id == listing_id,
        getattr(model, source.latitude) == lat,
        getattr(model, source.longitude) == lon,
    ]
    changes = {"is_active": active}
    if model is TourGuide:
        checks.extend(
            (
                TourGuide.approval_status == "pending",
                TourGuide.base_city == previous["city"],
                TourGuide.base_district == previous["district"],
            )
        )
        changes.update(
            approval_status=payload.decision,
            reviewed_by=admin_id,
            reviewed_at=datetime.now(UTC).replace(tzinfo=None),
            rejection_reason=None if active else payload.reason,
        )
    if model is RentalLocation:
        checks.extend(
            (
                model.is_active == bool(previous["active"]),
                model.city == previous["city"],
                model.district == previous["district"],
                model.address == previous["address"],
                model.location_name == previous["name"],
            )
        )
    try:
        updated = db.execute(update(model).where(*checks).values(**changes))
        if updated.rowcount != 1:
            raise HTTPException(409, detail={"code": "map_location_changed"})
        db.add(
            AdminAuditLog(
                admin_user_id=admin_id,
                action=f"map_location_{payload.decision}",
                entity_name=model.__tablename__,
                entity_id=listing_id,
                details=sanitize_audit_details({"reason": payload.reason}),
            )
        )
        result = LocationReview(
            **{
                **previous,
                "active": active,
                "approval_status": (
                    "inactive"
                    if model is RentalLocation and not active
                    else payload.decision
                ),
            }
        )
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise
