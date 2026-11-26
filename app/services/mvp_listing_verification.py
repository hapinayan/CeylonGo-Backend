import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError

from app.models.mvp import (
    MVPAvailabilitySlot, MVPBooking, MVPBookingAllocation, MVPInventoryUnit, MVPListing,
    MVPListingMedia,
    MVPListingVerification,
)
from app.routers.provider_registration import get_upload_directory, validate_and_save_image
from app.schemas.mvp import ListingResponse
from app.schemas.mvp_activity import ActivityMetadata
from app.schemas.mvp_guide import GuidePackageDetails
from app.schemas.mvp_homestay import HomestayMetadata
from app.schemas.mvp_listing_verification import ReviewListing, RoomDetails

PHOTO_PREFIX = "mvp-photo/"
EVIDENCE_REQUIRED = {
    "adventure": {"safety"}, "guide": {"credentials", "license"},
    "bike": {"ownership", "authorization"}, "bicycle": {"ownership", "authorization"},
    "artisan": {"workshop", "business_certificate"},
}


def now():
    return datetime.now(UTC).replace(tzinfo=None)


def installed(db):
    return db.get_bind().dialect.name != "mssql" or db.scalar(text(
        "SELECT OBJECT_ID(N'dbo.listing_verification', N'U')"
    )) is not None


def require_schema(db):
    if not installed(db):
        raise HTTPException(503, "Listing management requires MVP schema 1.0.4")


def locked_listing(db, listing_id):
    if db.get_bind().dialect.name == "sqlite":
        # SQLite has no row-lock hint. Acquire the write lock before reading capacity.
        db.execute(update(MVPListing).where(MVPListing.id == listing_id).values(
            updated_at=MVPListing.updated_at
        ))
    listing = db.scalar(select(MVPListing).where(MVPListing.id == listing_id).with_hint(
        MVPListing, "WITH (UPDLOCK, HOLDLOCK)", dialect_name="mssql"
    ))
    if listing is None:
        raise HTTPException(404, "Listing not found")
    return listing


def owned(db, user, listing_id, *, lock=False):
    listing = locked_listing(db, listing_id) if lock else db.get(MVPListing, listing_id)
    if listing is None:
        raise HTTPException(404, "Listing not found")
    if (user is None or user.role != "provider" or not user.provider_profile
            or user.provider_profile.verification_status != "approved"
            or listing.provider_id != user.provider_profile.id):
        raise HTTPException(403, "This listing belongs to another provider")
    return listing


def provider_eligible(listing):
    return (
        listing.provider.verification_status == "approved"
        and listing.provider.user.is_active and listing.provider.user.role == "provider"
    )


def require_editable(listing):
    if listing.approval_status == "suspended":
        raise HTTPException(409, "A suspended listing requires administrator review")


def invalidate_review(listing):
    if listing.approval_status in {"approved", "pending"}:
        listing.approval_status = "draft"
        listing.is_active = False
        listing.submitted_at = None


def details(db, listing):
    row = db.get(MVPListingVerification, listing.id)
    return json.loads(row.details) if row else {}


def save_details(db, listing, data):
    row = db.get(MVPListingVerification, listing.id)
    if not row:
        row = MVPListingVerification(listing_id=listing.id, details="{}")
        db.add(row)
    row.details = json.dumps(data, ensure_ascii=False)


def visible_media(listing, enabled=True):
    return sorted((item for item in listing.media if not enabled or item.is_visible),
                  key=lambda item: item.sort_order)


def room_details(room, enabled=True):
    extra = json.loads(room.room_metadata) if enabled and room.room_metadata else {}
    return RoomDetails(
        id=room.id, listing_id=room.listing_id, label=room.label, capacity=room.capacity,
        is_active=room.is_active, **extra,
    )


def guide_package(unit):
    data = json.loads(unit.room_metadata or "{}").get("guide_package")
    if data is None:
        return None
    return GuidePackageDetails(
        **data, id=unit.id, title=unit.label, max_people=unit.capacity, is_active=unit.is_active,
    )


def review(db, listing):
    content = details(db, listing)
    # The object key never leaves the server.
    content["files"] = [
        {key: value for key, value in item.items() if key != "key"}
        for item in content.get("files", []) if not item.get("removed")
    ]
    if listing.category == "guide":
        content["packages"] = [
            package.model_dump(mode="json") for unit in sorted(listing.units, key=lambda unit: unit.id)
            if (package := guide_package(unit)) is not None
        ]
    result = ListingResponse.model_validate(listing)
    visible = {photo.id for photo in visible_media(listing)}
    result.media = [item for item in result.media if item.id in visible]
    result.approval_status = (
        "more_evidence_required" if listing.approval_status == "evidence" else listing.approval_status
    )
    return ReviewListing(
        listing=result, details=content,
        rooms=[room_details(unit) for unit in listing.units] if listing.category != "guide" else [],
        homestay_metadata=(
            HomestayMetadata.model_validate_json(listing.homestay_metadata or "{}")
            if listing.category == "homestay" else None
        ),
        verification_status=result.approval_status,
        submitted_at=listing.submitted_at, decided_at=listing.decided_at,
        decision_reason=listing.decision_reason,
    )


def commit(db):
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Listing update conflicts with existing data") from None


def update_draft(db, listing, payload):
    require_editable(listing)
    fields = payload.model_dump(exclude_unset=True, mode="json")
    data = details(db, listing)
    material = False
    if "category" in fields and payload.category != listing.category:
        if payload.category is None:
            raise HTTPException(422, "Category cannot be null")
        if listing.units or db.scalar(select(MVPBooking.id).where(
            MVPBooking.listing_id == listing.id
        ).limit(1)):
            raise HTTPException(409, "Category cannot change after inventory or bookings exist")
        listing.category = payload.category
        listing.homestay_metadata = None
        data.pop("activity", None)
        data.pop("guide", None)
        data.pop("rental", None)
        material = True
    for key in ("title", "description", "district", "locality", "latitude", "longitude", "price"):
        if key not in fields:
            continue
        value = getattr(payload, key)
        if key in {"title", "description", "district", "locality", "price"} and value is None:
            raise HTTPException(422, f"{key} cannot be null")
        if isinstance(value, str):
            value = value.strip()
        material |= key != "price" and getattr(listing, key) != value
        setattr(listing, key, value)
    if (listing.latitude is None) != (listing.longitude is None):
        raise HTTPException(422, "Supply both coordinates")
    if "metadata" in fields:
        if listing.category != "homestay" or payload.metadata is None:
            raise HTTPException(422, "Only homestays support property metadata")
        if payload.metadata.cover_photo_id is not None and not any(
            item.id == payload.metadata.cover_photo_id for item in visible_media(listing)
        ):
            raise HTTPException(422, "Cover photo must belong to this listing")
        material = True
        listing.homestay_metadata = payload.metadata.model_dump_json()
    if "activity" in fields:
        if listing.category != "adventure" or payload.activity is None:
            raise HTTPException(422, "Activity details require an Adventure listing")
        config = payload.activity
        previous = data.get("activity", {})
        material |= any(previous.get(key) != value for key, value in fields["activity"].items()
                        if key not in {"min_participants", "max_participants"})
        if len(listing.units) > 1:
            raise HTTPException(409, "Activity scheduling requires one inventory unit")
        if db.scalar(select(MVPAvailabilitySlot.id).where(
            MVPAvailabilitySlot.listing_id == listing.id,
            MVPAvailabilitySlot.starts_at > now(),
            MVPAvailabilitySlot.capacity > config.max_participants,
        ).limit(1)) is not None:
            raise HTTPException(409, "Reduce future slot capacity before reducing group capacity")
        if listing.units:
            listing.units[0].capacity = config.max_participants
            listing.units[0].is_active = True
        else:
            listing.units.append(MVPInventoryUnit(
                provider_id=listing.provider_id, label="Activity group",
                capacity=config.max_participants, is_active=True,
            ))
        data["activity"] = fields["activity"]
    if "guide" in fields:
        if listing.category != "guide" or payload.guide is None:
            raise HTTPException(422, "Guide profile details require a Tour Guide listing")
        guide = payload.guide.model_dump(mode="json")
        material |= data.get("guide") != guide
        data["guide"] = guide
    if "rental" in fields:
        if listing.category not in {"bike", "bicycle"} or payload.rental is None:
            raise HTTPException(422, "Vehicle details require a rental listing")
        motorized_subtype = payload.rental.subtype in {"motorcycle", "scooter"}
        if motorized_subtype != (listing.category == "bike"):
            raise HTTPException(422, "Vehicle subtype does not match the rental category")
        if listing.category == "bicycle" and payload.rental.transmission == "automatic":
            raise HTTPException(422, "Choose a supported bicycle transmission")
        rental = payload.rental.model_dump(mode="json")
        material |= data.get("rental") != rental
        data["rental"] = rental
    for key in ("province", "address", "house_rules", "check_in_time", "check_out_time", "contact"):
        if key in fields:
            material |= data.get(key) != fields[key]
            data[key] = fields[key]
    if material:
        invalidate_review(listing)
    if listing.approval_status == "approved" and listing.price == 0:
        if listing.category != "homestay" or any(
            not room_details(room).nightly_price for room in listing.units if room.is_active
        ):
            raise HTTPException(422, "An approved listing must retain valid room or service prices")
    save_details(db, listing, data)
    commit(db)


def path_for(key):
    directory = Path(get_upload_directory()).resolve()
    if not key or Path(key).name != key:
        raise HTTPException(404, "File not found")
    path = (directory / key).resolve()
    if path.parent != directory or not path.is_file():
        raise HTTPException(404, "File not found")
    return path


def add_photo(db, listing, upload, alt_text):
    require_editable(listing)
    order = db.scalar(select(func.max(MVPListingMedia.sort_order)).where(
        MVPListingMedia.listing_id == listing.id
    ))
    order = 0 if order is None else order + 1
    if order > 32767:
        raise HTTPException(409, "Gallery limit reached")
    key = validate_and_save_image(upload, "listing_photo", None)
    photo = MVPListingMedia(listing_id=listing.id, storage_key=PHOTO_PREFIX + key,
                            sort_order=order, alt_text=alt_text)
    db.add(photo)
    invalidate_review(listing)
    try:
        commit(db)
    except Exception:
        path_for(key).unlink(missing_ok=True)
        raise
    return photo


def upload_evidence(db, listing, file, kind, redacted):
    require_editable(listing)
    data = details(db, listing)
    files = data.setdefault("files", [])
    if len(files) >= 200 or sum(not item.get("removed") for item in files) >= 30:
        raise HTTPException(409, "Evidence file limit reached")
    if kind == "customer_feedback" and not redacted:
        raise HTTPException(422, "Confirm customer personal information has been redacted")
    if kind in {"activity_photo", "vehicle_photo"} and file.content_type == "application/pdf":
        raise HTTPException(422, "Photo evidence must be an image")
    media_type = "image"
    if kind == "video" or file.content_type == "application/pdf":
        limit = (25 if kind == "video" else 10) * 1024 * 1024
        content = file.file.read(limit + 1)
        if len(content) > limit:
            raise HTTPException(413, "Evidence file too large")
        if kind == "video":
            if len(content) < 16 or content[4:8] != b"ftyp" or b"moov" not in content:
                raise HTTPException(422, "Expected a valid MP4 video")
            suffix, media_type = ".mp4", "video/mp4"
        else:
            if kind == "customer_feedback" or not content.startswith(b"%PDF-") or b"%%EOF" not in content[-1024:]:
                raise HTTPException(422, "Expected valid PDF evidence or an image screenshot")
            suffix, media_type = ".pdf", "application/pdf"
        key = f"listing_evidence_{uuid4().hex}{suffix}"
        (Path(get_upload_directory()) / key).write_bytes(content)
    else:
        key = validate_and_save_image(file, "listing_evidence", None)
    item = {"id": uuid4().hex, "kind": kind, "key": key, "media_type": media_type,
            "redacted": redacted if kind == "customer_feedback" else None}
    files.append(item)
    save_details(db, listing, data)
    invalidate_review(listing)
    try:
        commit(db)
    except Exception:
        path_for(key).unlink(missing_ok=True)
        raise
    return {key: value for key, value in item.items() if key != "key"}


def validate_submission(db, listing):
    data = details(db, listing)
    photos = [photo for photo in visible_media(listing) if photo.storage_key.startswith(PHOTO_PREFIX)]
    if not any((Path(get_upload_directory()) / photo.storage_key.removeprefix(PHOTO_PREFIX)).is_file()
               for photo in photos):
        raise HTTPException(422, "Upload at least one actual listing photo")
    if not all((len(listing.title.strip()) >= 3, len(listing.description.strip()) >= 10,
                listing.district.strip(), listing.locality.strip(),
                listing.latitude is not None, listing.longitude is not None)):
        raise HTTPException(422, "Complete the title, description, location and coordinates")
    if listing.category == "homestay":
        meta = HomestayMetadata.model_validate_json(listing.homestay_metadata or "{}")
        fields = ("province", "address", "house_rules", "check_in_time", "check_out_time", "contact")
        rooms = [room for room in listing.units if room.is_active]
        if (not meta.property_type or not all(str(data.get(field) or "").strip() for field in fields)
                or not rooms or any(not (room_details(room).nightly_price or listing.price) for room in rooms)):
            raise HTTPException(422, "Complete homestay property details and add an active room")
    else:
        if listing.price <= 0:
            raise HTTPException(422, "Set a positive LKR service price")
        if listing.category == "adventure":
            if not data.get("activity"):
                raise HTTPException(422, "Complete the activity category and scheduling details")
            activity = ActivityMetadata.model_validate(data["activity"])
            if not activity.safety_instructions:
                raise HTTPException(422, "Provide activity safety instructions")
        if listing.category == "guide" and (
            not data.get("guide") or not any(
                unit.is_active and guide_package(unit) is not None
                for unit in listing.units
            )
        ):
            raise HTTPException(422, "Complete the guide profile and add an active package")
        if listing.category in {"bike", "bicycle"} and not (
            data.get("rental") and any(unit.is_active and unit.capacity == 1 for unit in listing.units)
        ):
            raise HTTPException(422, "Complete rental details and add a vehicle to the fleet")
        expected = EVIDENCE_REQUIRED.get(listing.category, set())
        supplied = {item["kind"] for item in data.get("files", []) if not item.get("removed")}
        if expected and not supplied.intersection(expected) and not str(data.get("notes", "")).strip():
            raise HTTPException(422, f"Provide {' or '.join(sorted(expected))} evidence or an explanation")


def room_has_reservation(db, room, *, capacity=None, start=None, end=None):
    query = select(MVPBooking.id).join(MVPBookingAllocation).join(
        MVPAvailabilitySlot, MVPAvailabilitySlot.id == MVPBookingAllocation.slot_id
    ).where(
        MVPAvailabilitySlot.unit_id == room.id, MVPBookingAllocation.released_at.is_(None),
        MVPBooking.status.in_(("pending", "confirmed")), MVPBooking.ends_at > (start or now()),
    )
    if capacity is not None:
        query = query.where(MVPBooking.quantity > capacity)
    if end is not None:
        query = query.where(MVPBooking.starts_at < end)
    return db.scalar(query.limit(1)) is not None
