from collections.abc import Callable
from datetime import UTC, datetime

from fastapi import HTTPException, Request
from sqlalchemy import false, update
from sqlalchemy.orm import Session

from app.core.audit import contains_unsafe_admin_note, record_admin_audit_log
from app.core.booking_transactions import lock_booking_resource
from app.models.shared import ListingReference, Review
from app.services.community_posts import sanitize_community_text


def lock_review_moderation(db: Session, resource: str) -> None:
    if db.get_bind().dialect.name == "sqlite":
        # Start a SQLite write transaction before reading the state to be moderated.
        table = Review.__table__
        db.execute(update(table).where(false()).values(id=table.c.id))
    else:
        lock_booking_resource(db, resource)


def scoped_reviews(db: Session, module: str):
    return (
        db.query(Review)
        .join(ListingReference, Review.listing_reference_id == ListingReference.id)
        .filter(ListingReference.module_type == module)
    )


def apply_review_moderation(
    db: Session, *, review: Review, module: str, admin_id: int, published: bool,
    reason: str | None, request: Request | None = None,
    update_ratings: Callable[[], None] | None = None,
) -> None:
    """Apply an actual transition after the caller locks and reloads the scoped review."""
    try:
        cleaned_reason = sanitize_community_text(reason, field_name="reason", max_length=500)
        if cleaned_reason and contains_unsafe_admin_note(cleaned_reason):
            raise HTTPException(422, "Moderation reason contains credentials or personal information")
        previous = bool(review.is_published)
        if previous != published:
            review.is_published = published
            review.updated_at = datetime.now(UTC).replace(tzinfo=None)
            if update_ratings is not None:
                db.flush()
                update_ratings()
            record_admin_audit_log(
                db, admin_user_id=admin_id, action=f"moderate_{module}_review",
                entity_name="review" if module == "tour_guide" else "reviews",
                entity_id=review.id, request=request,
                details={"module": module, "is_published": published,
                         "previous_is_published": previous, "new_is_published": published,
                         "reason": cleaned_reason},
            )
        db.commit()
    except Exception:
        db.rollback()
        raise
