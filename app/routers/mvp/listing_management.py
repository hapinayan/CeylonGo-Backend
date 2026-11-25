import json
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.mvp_security import require_mvp_role
from app.database.mvp_session import get_mvp_db
from app.models.mvp import (
    MVPAdminAuditLog, MVPAvailabilitySlot, MVPBooking, MVPInventoryUnit, MVPListing,
    MVPUser,
)
from app.schemas.mvp import (
    AvailabilitySlotCreate, AvailabilitySlotResponse, ListingCreateRequest, ListingMediaResponse,
    ServiceCategory,
)
from app.schemas.mvp_homestay import HomestayBookingDetails
from app.schemas.mvp_listing_verification import (
    AdminReviewListing, EvidenceFileResponse, EvidenceUpdate, FileKind, ListingDecisionHistory,
    ListingDraftUpdate, ReviewListing, RoomDetails, RoomUpdate,
)
from app.services import mvp_homestay, mvp_listing_verification as service
from app.services.mvp_services import MVPListingService

router = APIRouter(tags=["MVP Listing Management and Verification"])
DB = Annotated[Session, Depends(get_mvp_db)]
Provider = Annotated[MVPUser, Depends(require_mvp_role("provider"))]
Admin = Annotated[MVPUser, Depends(require_mvp_role("admin"))]


def _owned(db, provider, listing_id, *, lock=True):
    service.require_schema(db)
    return service.owned(db, provider, listing_id, lock=lock)


def _review(db, listing_id):
    service.require_schema(db)
    listing = db.get(MVPListing, listing_id)
    if not listing:
        raise HTTPException(404, "Listing not found")
    return listing


@router.post("/provider/listings", response_model=ReviewListing, status_code=201)
def create_listing(payload: ListingCreateRequest, db: DB, provider: Provider):
    service.require_schema(db)
    return service.review(db, MVPListingService.create_listing(db, provider, payload))


@router.get("/provider/listings", response_model=list[ReviewListing])
def provider_listings(db: DB, provider: Provider, limit: int = Query(25, ge=1, le=100),
                      offset: int = Query(0, ge=0)):
    service.require_schema(db)
    items = db.scalars(select(MVPListing).where(
        MVPListing.provider_id == provider.provider_profile.id
    ).order_by(MVPListing.id.desc()).offset(offset).limit(limit))
    return [service.review(db, item) for item in items]


@router.get("/provider/listings/{listing_id}", response_model=ReviewListing)
def provider_listing(listing_id: int, db: DB, provider: Provider):
    return service.review(db, _owned(db, provider, listing_id, lock=False))


@router.patch("/provider/listings/{listing_id}", response_model=ReviewListing)
def update_draft(listing_id: int, payload: ListingDraftUpdate, db: DB, provider: Provider):
    listing = _owned(db, provider, listing_id)
    service.update_draft(db, listing, payload)
    return service.review(db, listing)


@router.post("/provider/listings/{listing_id}/submit", response_model=ReviewListing)
def submit(listing_id: int, db: DB, provider: Provider):
    listing = _owned(db, provider, listing_id)
    if listing.approval_status not in {"draft", "rejected", "evidence"}:
        raise HTTPException(409, "This listing cannot be submitted in its current state")
    service.validate_submission(db, listing)
    listing.approval_status, listing.is_active = "pending", False
    listing.submitted_at = service.now()
    service.commit(db)
    return service.review(db, listing)


@router.post("/provider/listings/{listing_id}/unpublish", response_model=ReviewListing)
def unpublish(listing_id: int, db: DB, provider: Provider):
    listing = _owned(db, provider, listing_id)
    listing.is_active = False
    service.commit(db)
    return service.review(db, listing)


@router.post("/provider/listings/{listing_id}/publish", response_model=ReviewListing)
def publish(listing_id: int, db: DB, provider: Provider):
    listing = _owned(db, provider, listing_id)
    if listing.approval_status != "approved":
        raise HTTPException(409, "Only approved listings may be published")
    listing.is_active = True
    service.commit(db)
    return service.review(db, listing)


@router.post("/provider/listings/{listing_id}/photos", response_model=ListingMediaResponse, status_code=201)
def upload_photo(listing_id: int, db: DB, provider: Provider, file: UploadFile = File(...),
                 alt_text: str | None = Form(None, max_length=160)):
    return service.add_photo(db, _owned(db, provider, listing_id), file, alt_text)


def _photo(listing, photo_id):
    photo = next((item for item in service.visible_media(listing) if item.id == photo_id), None)
    if not photo:
        raise HTTPException(404, "Photo not found")
    return photo


@router.put("/provider/listings/{listing_id}/photos/{photo_id}/cover", response_model=ReviewListing)
def cover(listing_id: int, photo_id: int, db: DB, provider: Provider):
    listing = _owned(db, provider, listing_id)
    service.require_editable(listing)
    _photo(listing, photo_id)
    data = service.details(db, listing)
    data["cover_photo_id"] = photo_id
    if listing.category == "homestay":
        listing.homestay_metadata = mvp_homestay.metadata(listing).model_copy(
            update={"cover_photo_id": photo_id}
        ).model_dump_json()
    service.save_details(db, listing, data)
    service.commit(db)
    return service.review(db, listing)


@router.delete("/provider/listings/{listing_id}/photos/{photo_id}", status_code=204)
def archive_photo(listing_id: int, photo_id: int, db: DB, provider: Provider):
    listing = _owned(db, provider, listing_id)
    service.require_editable(listing)
    _photo(listing, photo_id).is_visible = False
    data = service.details(db, listing)
    if data.get("cover_photo_id") == photo_id:
        data.pop("cover_photo_id")
        service.save_details(db, listing, data)
    if listing.category == "homestay":
        metadata = mvp_homestay.metadata(listing)
        if metadata.cover_photo_id == photo_id:
            listing.homestay_metadata = metadata.model_copy(
                update={"cover_photo_id": None}
            ).model_dump_json()
        for room in listing.units:
            if room.room_metadata:
                fields = json.loads(room.room_metadata)
                fields["photo_ids"] = [item for item in fields.get("photo_ids", []) if item != photo_id]
                room.room_metadata = json.dumps(fields)
    service.invalidate_review(listing)
    service.commit(db)
    return Response(status_code=204)


def _photo_response(listing, photo_id, *, private=False):
    photo = _photo(listing, photo_id)
    if not photo.storage_key.startswith(service.PHOTO_PREFIX):
        raise HTTPException(404, "Hosted photo not found")
    headers = {"X-Content-Type-Options": "nosniff"}
    if private:
        headers["Cache-Control"] = "no-store"
    return FileResponse(service.path_for(photo.storage_key.removeprefix(service.PHOTO_PREFIX)),
                        headers=headers)


@router.get("/listings/{listing_id}/photos/{photo_id}")
def public_photo(listing_id: int, photo_id: int, db: DB):
    listing = _review(db, listing_id)
    if not listing.is_active or listing.approval_status != "approved" or not service.provider_eligible(listing):
        raise HTTPException(404, "Published listing not found")
    return _photo_response(listing, photo_id)


@router.get("/provider/listings/{listing_id}/photos/{photo_id}")
def provider_photo(listing_id: int, photo_id: int, db: DB, provider: Provider):
    return _photo_response(_owned(db, provider, listing_id, lock=False), photo_id, private=True)


@router.put("/provider/listings/{listing_id}/evidence", response_model=ReviewListing)
def save_evidence(listing_id: int, payload: EvidenceUpdate, db: DB, provider: Provider):
    listing = _owned(db, provider, listing_id)
    service.require_editable(listing)
    data = service.details(db, listing)
    data.update(payload.model_dump(mode="json"))
    service.save_details(db, listing, data)
    service.invalidate_review(listing)
    service.commit(db)
    return service.review(db, listing)


@router.post("/provider/listings/{listing_id}/evidence/files",
             response_model=EvidenceFileResponse, status_code=201)
def evidence_file(listing_id: int, db: DB, provider: Provider, file: UploadFile = File(...),
                  kind: FileKind = Form(...), redacted: bool = Form(False)):
    return service.upload_evidence(db, _owned(db, provider, listing_id), file, kind, redacted)


def _evidence(db, listing, file_id):
    item = next((item for item in service.details(db, listing).get("files", [])
                 if item["id"] == file_id and not item.get("removed")), None)
    if not item:
        raise HTTPException(404, "Evidence file not found")
    return item


def _evidence_response(db, listing, file_id):
    item = _evidence(db, listing, file_id)
    path = service.path_for(item["key"])
    return FileResponse(path, filename=f"{item['kind']}{path.suffix}",
                        media_type=None if item["media_type"] == "image" else item["media_type"],
                        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


@router.get("/provider/listings/{listing_id}/evidence/files/{file_id}")
def provider_evidence(listing_id: int, file_id: str, db: DB, provider: Provider):
    return _evidence_response(db, _owned(db, provider, listing_id, lock=False), file_id)


@router.delete("/provider/listings/{listing_id}/evidence/files/{file_id}", status_code=204)
def archive_evidence(listing_id: int, file_id: str, db: DB, provider: Provider):
    listing = _owned(db, provider, listing_id)
    service.require_editable(listing)
    data = service.details(db, listing)
    _evidence(db, listing, file_id)
    next(item for item in data["files"] if item["id"] == file_id)["removed"] = True
    service.save_details(db, listing, data)
    service.invalidate_review(listing)
    service.commit(db)
    return Response(status_code=204)


def _save_room(db, listing, payload, room=None):
    service.require_editable(listing)
    if listing.category != "homestay":
        raise HTTPException(422, "Room management is available for homestays")
    fields = payload.model_dump(exclude_unset=True, mode="json")
    if room is None:
        if not payload.label or not payload.label.strip() or payload.capacity is None:
            raise HTTPException(422, "Room label and capacity are required")
        room = MVPInventoryUnit(listing_id=listing.id, provider_id=listing.provider_id,
                                label=payload.label.strip(), capacity=payload.capacity)
        db.add(room)
    if payload.is_active is False and service.room_has_reservation(db, room):
        raise HTTPException(409, "Room has pending or confirmed reservations")
    if payload.capacity is not None and payload.capacity < room.capacity and service.room_has_reservation(
        db, room, capacity=payload.capacity
    ):
        raise HTTPException(409, "Capacity is below the guest count of a reservation")
    current = service.room_details(room).model_dump(mode="json", include={
        "room_type", "description", "nightly_price", "photo_ids",
    }) if room.id else {}
    for key in ("label", "capacity", "is_active"):
        if key in fields:
            value = fields.pop(key)
            if value is None or (key == "label" and not value.strip()):
                raise HTTPException(422, f"Invalid {key}")
            setattr(room, key, value.strip() if key == "label" else value)
    if "photo_ids" in fields:
        fields["photo_ids"] = list(dict.fromkeys(fields["photo_ids"] or []))
        if not set(fields["photo_ids"]).issubset(photo.id for photo in service.visible_media(listing)):
            raise HTTPException(422, "Room photos must belong to this gallery")
    current.update(fields)
    if listing.approval_status == "approved" and room.is_active is not False and not (
        current.get("nightly_price") or listing.price
    ):
        raise HTTPException(422, "An active room requires a positive nightly price")
    room.room_metadata = json.dumps(current)
    service.commit(db)
    return service.room_details(room)


@router.post("/provider/listings/{listing_id}/rooms", response_model=RoomDetails, status_code=201)
def add_room(listing_id: int, payload: RoomUpdate, db: DB, provider: Provider):
    return _save_room(db, _owned(db, provider, listing_id), payload)


@router.patch("/provider/listings/{listing_id}/rooms/{room_id}", response_model=RoomDetails)
def update_room(listing_id: int, room_id: int, payload: RoomUpdate, db: DB, provider: Provider):
    listing = _owned(db, provider, listing_id)
    room = db.get(MVPInventoryUnit, room_id)
    if not room or room.listing_id != listing_id:
        raise HTTPException(404, "Room not found")
    return _save_room(db, listing, payload, room)


@router.get("/provider/listings/{listing_id}/availability", response_model=list[AvailabilitySlotResponse])
def calendar(listing_id: int, db: DB, provider: Provider, limit: int = Query(50, ge=1, le=100),
             offset: int = Query(0, ge=0)):
    _owned(db, provider, listing_id, lock=False)
    return list(db.scalars(select(MVPAvailabilitySlot).where(
        MVPAvailabilitySlot.listing_id == listing_id
    ).order_by(MVPAvailabilitySlot.starts_at, MVPAvailabilitySlot.id).offset(offset).limit(limit)))


@router.post("/provider/listings/{listing_id}/availability",
             response_model=AvailabilitySlotResponse, status_code=201)
def create_slot(listing_id: int, payload: AvailabilitySlotCreate, db: DB, provider: Provider):
    service.require_schema(db)
    return MVPListingService.add_availability_slot(db, listing_id, payload, provider)


@router.put("/provider/listings/{listing_id}/availability/{slot_id}", response_model=AvailabilitySlotResponse)
def edit_slot(listing_id: int, slot_id: int, payload: AvailabilitySlotCreate, db: DB, provider: Provider):
    listing = _owned(db, provider, listing_id)
    service.require_editable(listing)
    slot = db.get(MVPAvailabilitySlot, slot_id)
    if not slot or slot.listing_id != listing_id:
        raise HTTPException(404, "Availability slot not found")
    start, end = map(mvp_homestay.utc_naive, (payload.starts_at, payload.ends_at))
    if payload.unit_id != slot.unit_id or end <= start or payload.capacity > slot.unit.capacity:
        raise HTTPException(422, "Invalid calendar dates, room or capacity")
    if ((start, end) != (slot.starts_at, slot.ends_at) or not payload.is_open or payload.capacity < slot.capacity) and (
        service.room_has_reservation(db, slot.unit, start=slot.starts_at, end=slot.ends_at)
        or (not payload.is_open and service.room_has_reservation(db, slot.unit, start=start, end=end))
    ):
        raise HTTPException(409, "The calendar change conflicts with a reservation")
    slot.starts_at, slot.ends_at, slot.capacity, slot.is_open = start, end, payload.capacity, payload.is_open
    service.commit(db)
    return slot


@router.get("/provider/bookings", response_model=list[HomestayBookingDetails])
def provider_bookings(db: DB, provider: Provider, status: Literal[
    "pending", "confirmed", "completed", "cancelled", "rejected"
] | None = None, limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0)):
    query = select(MVPBooking).join(MVPListing).where(MVPListing.provider_id == provider.provider_profile.id)
    if status:
        query = query.where(MVPBooking.status == status)
    return [mvp_homestay.booking_details(item) for item in db.scalars(
        query.order_by(MVPBooking.id.desc()).offset(offset).limit(limit)
    )]


@router.get("/provider/bookings/{booking_id}", response_model=HomestayBookingDetails)
def provider_booking(booking_id: int, db: DB, provider: Provider):
    booking = db.scalar(select(MVPBooking).join(MVPListing).where(
        MVPBooking.id == booking_id, MVPListing.provider_id == provider.provider_profile.id
    ))
    if not booking:
        raise HTTPException(404, "Booking not found")
    return mvp_homestay.booking_details(booking)


@router.get("/admin/listings", response_model=list[ReviewListing])
def admin_listings(db: DB, admin: Admin, category: ServiceCategory | None = None,
                   status: Literal["draft", "pending", "more_evidence_required",
                                   "approved", "rejected", "suspended"] = "pending",
                   limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0)):
    service.require_schema(db)
    query = select(MVPListing).where(
        MVPListing.approval_status == ("evidence" if status == "more_evidence_required" else status)
    )
    if category:
        query = query.where(MVPListing.category == category)
    return [service.review(db, listing) for listing in db.scalars(
        query.order_by(MVPListing.id.desc()).offset(offset).limit(limit)
    )]


@router.get("/admin/listings/{listing_id}", response_model=AdminReviewListing)
def admin_listing(listing_id: int, db: DB, admin: Admin):
    listing = _review(db, listing_id)
    return {
        **service.review(db, listing).model_dump(mode="json"),
        "provider": {"id": listing.provider.id, "business_name": listing.provider.business_name,
                     "email": listing.provider.user.email, "phone": listing.provider.phone,
                     "verification_status": listing.provider.verification_status},
    }


@router.get("/admin/listings/{listing_id}/photos/{photo_id}")
def admin_photo(listing_id: int, photo_id: int, db: DB, admin: Admin):
    return _photo_response(_review(db, listing_id), photo_id, private=True)


@router.get("/admin/listings/{listing_id}/evidence/files/{file_id}")
def admin_evidence(listing_id: int, file_id: str, db: DB, admin: Admin):
    return _evidence_response(db, _review(db, listing_id), file_id)


@router.get("/admin/listings/{listing_id}/history", response_model=list[ListingDecisionHistory])
def history(listing_id: int, db: DB, admin: Admin, limit: int = Query(25, ge=1, le=100),
            offset: int = Query(0, ge=0)):
    _review(db, listing_id)
    rows = db.scalars(select(MVPAdminAuditLog).where(
        MVPAdminAuditLog.subject_listing_id == listing_id,
        MVPAdminAuditLog.action == "listing_decision",
    ).order_by(MVPAdminAuditLog.id.desc()).offset(offset).limit(limit))
    entries = []
    for row in rows:
        decision, _, reason = row.reason.partition(": ")
        known = decision in {"approved", "rejected", "suspended", "more_evidence_required"}
        entries.append({"actor_id": row.actor_id, "reason": reason if known else row.reason,
                        "occurred_at": row.occurred_at, "decision": decision if known else None})
    return entries
