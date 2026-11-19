from fastapi import HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import set_committed_value

ACTIVITY_LOCATION_FIELDS = (
    "latitude",
    "longitude",
    "meeting_point",
    "city",
    "district",
)
ACTIVITY_REVIEW_STATE = (
    *ACTIVITY_LOCATION_FIELDS,
    "approval_status",
    "is_active",
    "reviewed_at",
)
HOMESTAY_LOCATION_FIELDS = (
    "latitude",
    "longitude",
    "address",
    "city",
    "district",
    "province",
    "location",
)
HOMESTAY_REVIEW_STATE = (
    *HOMESTAY_LOCATION_FIELDS,
    "approval_status",
    "is_active",
    "reviewed_at",
)
ECO_LOCATION_FIELDS = (
    "latitude",
    "longitude",
    "address",
    "meeting_point",
    "city",
    "district",
)
ECO_REVIEW_STATE = (
    *ECO_LOCATION_FIELDS,
    "listing_status",
    "is_published",
    "approval_reviewed_at",
)
ARTISAN_LOCATION_FIELDS = ("latitude", "longitude", "address", "city", "district")
ARTISAN_REVIEW_STATE = (
    *ARTISAN_LOCATION_FIELDS,
    "approval_status",
    "is_active",
    "is_verified",
)


def validate_coordinate_pair(latitude, longitude) -> None:
    """Validate the resulting pair after merging a provider's partial update."""
    if (latitude is None) != (longitude is None):
        raise HTTPException(
            422, detail="Latitude and longitude must be provided together"
        )


def write_review_snapshot(db: Session, listing, fields: tuple[str, ...], changes: dict):
    """Write location or review changes only while their observed state matches."""
    model = type(listing)
    checks = [getattr(model, field) == getattr(listing, field) for field in fields]
    with db.no_autoflush:
        updated = (
            db.query(model)
            .filter(model.id == listing.id, *checks)
            .update(
                changes,
                synchronize_session=False,
            )
        )
    if updated != 1:
        db.rollback()
        raise HTTPException(
            409, detail="Listing location or review status changed. Retry."
        )
    for field, value in changes.items():
        set_committed_value(listing, field, value)
