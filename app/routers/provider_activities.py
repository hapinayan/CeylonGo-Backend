
from datetime import date, datetime
from decimal import Decimal
import io
import logging
import os
from pathlib import Path
from typing import Annotated
import uuid

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse
from PIL import Image, UnidentifiedImageError
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

logger = logging.getLogger(__name__)

from app.core.dependencies import require_approved_provider
from app.core.listing_sync import sync_activity_listing_reference
from app.core.location_validation import (
    ACTIVITY_REVIEW_STATE,
    validate_coordinate_pair,
    write_review_snapshot,
)
from app.database.connection import get_db
from app.models.activity import Activity
from app.models.activity_booking import ActivityBooking
from app.models.activity_image import ActivityImage
from app.models.activity_slot import ActivitySlot
from app.models.provider_profile import ProviderProfile
from app.models.shared import ListingReference, Review
from app.schemas.activity import (
    ActivityActiveUpdate,
    ActivityCreate,
    ActivityImageResponse,
    ActivityImageUpdate,
    ActivityResponse,
    ActivityUpdate,
)
from app.schemas.activity_analytics import (
    ActivityAnalyticsOverview,
    ActivityPerformanceMetric,
    BookingAnalyticsOverview,
    CapacityAnalyticsOverview,
    CurrencyRevenueBreakdown,
    ProviderActivityAnalyticsResponse,
    RevenueAnalyticsOverview,
)


router = APIRouter(
    prefix="/api/v1/provider/activities",
    tags=["Provider Activities"],
)

activity_uploads_router = APIRouter(tags=["Activity Uploads"])

ALLOWED_ACTIVITY_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
ALLOWED_ACTIVITY_IMAGE_MIMES = {"image/jpeg", "image/png", "image/webp"}
ALLOWED_ACTIVITY_IMAGE_FORMATS = {"JPEG", "PNG", "WEBP"}
MAX_ACTIVITY_IMAGE_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB
MIN_ACTIVITY_IMAGE_DIMENSION = 50
MAX_ACTIVITY_IMAGE_DIMENSION = 8000
MAX_IMAGES_PER_ACTIVITY = 20


def get_activity_images_dir() -> Path:
    base_dir = os.getenv(
        "ACTIVITIES_IMAGES_DIR",
        str(Path(os.getcwd()) / "uploads" / "activities"),
    )
    p = Path(base_dir)
    p.mkdir(parents=True, exist_ok=True)
    return p.resolve()


def require_activity_provider(
    profile: Annotated[
        ProviderProfile,
        Depends(require_approved_provider),
    ],
) -> ProviderProfile:
    """Allow only approved activity providers."""
    if profile.provider_type != "activity":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Activity provider access required",
        )

    return profile


def get_owned_activity(
    activity_id: int,
    provider_profile: ProviderProfile,
    db: Session,
    for_update: bool = False,
) -> Activity:
    """Find an activity owned by the current provider."""
    query = db.query(Activity).filter(
        Activity.id == activity_id,
        Activity.provider_profile_id == provider_profile.id,
    )
    if for_update:
        query = query.with_for_update()

    activity = query.first()

    if activity is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Activity not found",
        )

    return activity


@router.post(
    "",
    response_model=ActivityResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a draft activity",
)
def create_activity(
    payload: ActivityCreate,
    provider_profile: Annotated[
        ProviderProfile,
        Depends(require_activity_provider),
    ],
    db: Annotated[Session, Depends(get_db)],
):
    validate_coordinate_pair(payload.latitude, payload.longitude)
    activity = Activity(
        **payload.model_dump(),
        provider_profile_id=provider_profile.id,
        approval_status="draft",
        is_active=False,
    )

    db.add(activity)

    try:
        db.commit()
        db.refresh(activity)
    except Exception:
        db.rollback()
        raise

    return activity


@router.get(
    "",
    response_model=list[ActivityResponse],
    summary="List my activities",
)
def list_my_activities(
    provider_profile: Annotated[
        ProviderProfile,
        Depends(require_activity_provider),
    ],
    db: Annotated[Session, Depends(get_db)],
):
    return (
        db.query(Activity)
        .filter(
            Activity.provider_profile_id == provider_profile.id
        )
        .order_by(Activity.created_at.desc())
        .all()
    )


@router.get(
    "/analytics",
    response_model=ProviderActivityAnalyticsResponse,
    summary="Get provider activity analytics and performance metrics (B01)",
)
def get_provider_activity_analytics(
    provider_profile: Annotated[
        ProviderProfile,
        Depends(require_activity_provider),
    ],
    db: Annotated[Session, Depends(get_db)],
    activity_id: int | None = Query(
        default=None,
        description="Filter metrics for a specific owned activity",
    ),
    date_from: date | None = Query(
        default=None,
        description="Start date filter (YYYY-MM-DD)",
    ),
    date_to: date | None = Query(
        default=None,
        description="End date filter (YYYY-MM-DD)",
    ),
    start_date: date | None = Query(
        default=None,
        description="Alias for date_from",
    ),
    end_date: date | None = Query(
        default=None,
        description="Alias for date_to",
    ),
):
    """
    Comprehensive provider activity analytics endpoint (B01).
    Aggregates activity counts by status, booking volume by status, participant metrics,
    revenue performance (gross, paid, unpaid), capacity utilization, and per-activity breakdown
    with rating and review stats, with zero N+1 queries.
    """
    eff_from = date_from or start_date
    eff_to = date_to or end_date

    if eff_from and eff_to and eff_from > eff_to:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="start_date / date_from cannot be after end_date / date_to",
        )

    # 1. Activities query
    if activity_id is not None:
        get_owned_activity(activity_id, provider_profile, db)
        owned_activities = (
            db.query(Activity)
            .filter(
                Activity.id == activity_id,
                Activity.provider_profile_id == provider_profile.id,
            )
            .all()
        )
    else:
        owned_activities = (
            db.query(Activity)
            .filter(Activity.provider_profile_id == provider_profile.id)
            .order_by(Activity.id.asc())
            .all()
        )

    owned_activity_ids = [a.id for a in owned_activities]

    # Activity overview counts
    total_activities = len(owned_activities)
    active_activities = sum(1 for a in owned_activities if a.is_active)
    approved_activities = sum(1 for a in owned_activities if a.approval_status == "approved")
    pending_activities = sum(1 for a in owned_activities if a.approval_status == "pending")
    draft_activities = sum(1 for a in owned_activities if a.approval_status == "draft")
    suspended_activities = sum(1 for a in owned_activities if a.approval_status == "suspended")
    rejected_activities = sum(1 for a in owned_activities if a.approval_status == "rejected")

    activity_overview = ActivityAnalyticsOverview(
        total_activities=total_activities,
        active_activities=active_activities,
        approved_activities=approved_activities,
        pending_activities=pending_activities,
        draft_activities=draft_activities,
        suspended_activities=suspended_activities,
        rejected_activities=rejected_activities,
    )

    # 2. Bookings query & metrics
    bookings: list[ActivityBooking] = []
    if owned_activity_ids:
        b_query = (
            db.query(ActivityBooking)
            .join(ActivitySlot, ActivityBooking.activity_slot_id == ActivitySlot.id)
            .filter(ActivitySlot.activity_id.in_(owned_activity_ids))
            .options(joinedload(ActivityBooking.activity_slot))
        )
        if eff_from is not None:
            b_query = b_query.filter(
                ActivityBooking.created_at >= datetime.combine(eff_from, datetime.min.time())
            )
        if eff_to is not None:
            b_query = b_query.filter(
                ActivityBooking.created_at <= datetime.combine(eff_to, datetime.max.time())
            )
        bookings = b_query.order_by(
            ActivityBooking.created_at.desc(),
            ActivityBooking.id.desc(),
        ).all()

    total_bookings = len(bookings)
    confirmed_bookings = sum(1 for b in bookings if b.booking_status == "confirmed")
    completed_bookings = sum(1 for b in bookings if b.booking_status == "completed")
    pending_bookings = sum(1 for b in bookings if b.booking_status == "pending")
    cancelled_bookings = sum(1 for b in bookings if b.booking_status == "cancelled")
    rejected_bookings = sum(1 for b in bookings if b.booking_status == "rejected")
    total_participants = sum((b.participants for b in bookings), 0)
    confirmed_participants = sum(
        (b.participants for b in bookings if b.booking_status in ("confirmed", "completed")),
        0,
    )
    cancellation_rate = (
        round((cancelled_bookings / total_bookings) * 100.0, 2)
        if total_bookings > 0
        else 0.0
    )
    cancellation_rate = min(100.0, max(0.0, cancellation_rate))

    booking_overview = BookingAnalyticsOverview(
        total_bookings=total_bookings,
        confirmed_bookings=confirmed_bookings,
        completed_bookings=completed_bookings,
        pending_bookings=pending_bookings,
        cancelled_bookings=cancelled_bookings,
        rejected_bookings=rejected_bookings,
        total_participants=total_participants,
        confirmed_participants=confirmed_participants,
        cancellation_rate=cancellation_rate,
    )

    # 3. Financials
    # Aggregate monetary figures partitioned by currency to prevent mixing
    currency_map: dict[str, dict[str, Decimal]] = {}
    for b in bookings:
        curr = b.currency or "LKR"
        if curr not in currency_map:
            currency_map[curr] = {
                "gross": Decimal("0.00"),
                "paid": Decimal("0.00"),
                "unpaid": Decimal("0.00"),
                "refunded": Decimal("0.00"),
            }

        # Exclude cancelled & rejected from gross, paid, unpaid
        if b.booking_status not in ("cancelled", "rejected"):
            if b.payment_status == "paid":
                currency_map[curr]["paid"] += b.total_price
                currency_map[curr]["gross"] += b.total_price
            elif b.payment_status in ("unpaid", "pending"):
                currency_map[curr]["unpaid"] += b.total_price
                currency_map[curr]["gross"] += b.total_price

        # Track refunds
        if b.payment_status in ("refunded", "refund_pending"):
            currency_map[curr]["refunded"] += b.total_price

    by_currency_list = [
        CurrencyRevenueBreakdown(
            currency=c,
            total_gross_revenue=vals["gross"],
            paid_revenue=vals["paid"],
            unpaid_revenue=vals["unpaid"],
            refunded_amount=vals["refunded"],
        )
        for c, vals in sorted(currency_map.items())
    ]

    primary_curr = "LKR" if "LKR" in currency_map else (by_currency_list[0].currency if by_currency_list else "LKR")
    primary_vals = currency_map.get(
        primary_curr,
        {
            "gross": Decimal("0.00"),
            "paid": Decimal("0.00"),
            "unpaid": Decimal("0.00"),
            "refunded": Decimal("0.00"),
        },
    )

    financial_overview = RevenueAnalyticsOverview(
        total_gross_revenue=primary_vals["gross"],
        paid_revenue=primary_vals["paid"],
        unpaid_revenue=primary_vals["unpaid"],
        refunded_amount=primary_vals["refunded"],
        currency=primary_curr,
        by_currency=by_currency_list,
    )

    # 4. Capacity metrics
    slots: list[ActivitySlot] = []
    if owned_activity_ids:
        s_query = (
            db.query(ActivitySlot)
            .filter(ActivitySlot.activity_id.in_(owned_activity_ids))
        )
        if eff_from is not None:
            s_query = s_query.filter(
                ActivitySlot.starts_at >= datetime.combine(eff_from, datetime.min.time())
            )
        if eff_to is not None:
            s_query = s_query.filter(
                ActivitySlot.starts_at <= datetime.combine(eff_to, datetime.max.time())
            )
        slots = s_query.order_by(
            ActivitySlot.starts_at.asc(),
            ActivitySlot.id.asc(),
        ).all()

    total_slots_offered = len(slots)
    total_capacity_offered = sum((s.capacity for s in slots), 0)
    total_capacity_reserved = sum((s.reserved_count for s in slots), 0)
    occupancy_rate = (
        round((total_capacity_reserved / total_capacity_offered) * 100.0, 2)
        if total_capacity_offered > 0
        else 0.0
    )
    occupancy_rate = min(100.0, max(0.0, occupancy_rate))

    capacity_overview = CapacityAnalyticsOverview(
        total_slots_offered=total_slots_offered,
        total_capacity_offered=total_capacity_offered,
        total_capacity_reserved=total_capacity_reserved,
        occupancy_rate=occupancy_rate,
    )

    # 5. Reviews aggregated batch query (0 N+1)
    review_stats: dict[int, tuple[float | None, int]] = {}
    if owned_activity_ids:
        rev_rows = (
            db.query(
                ListingReference.target_id,
                func.avg(Review.rating),
                func.count(Review.id),
            )
            .join(Review, Review.listing_reference_id == ListingReference.id)
            .filter(
                ListingReference.module_type == "activity",
                ListingReference.target_id.in_(owned_activity_ids),
                Review.is_published == True,
            )
            .group_by(ListingReference.target_id)
            .all()
        )
        for act_id, avg_r, cnt in rev_rows:
            review_stats[act_id] = (
                round(float(avg_r), 2) if avg_r is not None else None,
                cnt or 0,
            )

    # 6. Granular per-activity breakdown
    activity_bookings_map: dict[int, list[ActivityBooking]] = {
        act_id: [] for act_id in owned_activity_ids
    }
    for b in bookings:
        slot = b.activity_slot
        if slot and slot.activity_id in activity_bookings_map:
            activity_bookings_map[slot.activity_id].append(b)

    breakdown: list[ActivityPerformanceMetric] = []
    for act in owned_activities:
        act_b_list = activity_bookings_map.get(act.id, [])
        act_total_b = len(act_b_list)
        act_conf_b = sum(
            1 for b in act_b_list if b.booking_status in ("confirmed", "completed")
        )
        act_parts = sum((b.participants for b in act_b_list), 0)
        act_gross = sum(
            (
                b.total_price
                for b in act_b_list
                if b.booking_status not in ("cancelled", "rejected")
                and b.payment_status in ("paid", "unpaid", "pending")
            ),
            Decimal("0.00"),
        )
        avg_r, rev_cnt = review_stats.get(act.id, (None, 0))

        breakdown.append(
            ActivityPerformanceMetric(
                activity_id=act.id,
                title=act.title,
                category=act.category,
                approval_status=act.approval_status,
                is_active=act.is_active,
                total_bookings=act_total_b,
                confirmed_bookings=act_conf_b,
                total_participants=act_parts,
                gross_revenue=act_gross,
                average_rating=avg_r,
                total_reviews=rev_cnt,
            )
        )

    return ProviderActivityAnalyticsResponse(
        provider_profile_id=provider_profile.id,
        business_name=provider_profile.business_name,
        date_from=eff_from,
        date_to=eff_to,
        activity_filter_id=activity_id,
        activities=activity_overview,
        bookings=booking_overview,
        financials=financial_overview,
        capacity=capacity_overview,
        breakdown=breakdown,
    )


@router.get(
    "/{activity_id}",
    response_model=ActivityResponse,
    summary="Get my activity details",
)
def get_my_activity(
    activity_id: int,
    provider_profile: Annotated[
        ProviderProfile,
        Depends(require_activity_provider),
    ],
    db: Annotated[Session, Depends(get_db)],
):
    return get_owned_activity(
        activity_id,
        provider_profile,
        db,
    )


@router.patch(
    "/{activity_id}",
    response_model=ActivityResponse,
    summary="Update a draft or rejected activity",
)
def update_activity(
    activity_id: int,
    payload: ActivityUpdate,
    provider_profile: Annotated[
        ProviderProfile,
        Depends(require_activity_provider),
    ],
    db: Annotated[Session, Depends(get_db)],
):
    activity = get_owned_activity(
        activity_id,
        provider_profile,
        db,
    )

    if activity.approval_status not in (
        "draft",
        "rejected",
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Only draft or rejected activities "
                "can be edited"
            ),
        )

    updates = payload.model_dump(exclude_unset=True)
    if {"latitude", "longitude"} & updates.keys():
        validate_coordinate_pair(
            updates.get("latitude", activity.latitude),
            updates.get("longitude", activity.longitude),
        )

    if activity.approval_status == "rejected":
        updates.update(
            approval_status="draft", rejection_reason=None, reviewed_by=None,
            reviewed_at=None, is_active=False,
        )

    try:
        if updates:
            write_review_snapshot(db, activity, ACTIVITY_REVIEW_STATE, updates)
        sync_activity_listing_reference(db, activity)
        db.commit()
        db.refresh(activity)
    except Exception:
        db.rollback()
        raise

    return activity


@router.post(
    "/{activity_id}/submit",
    response_model=ActivityResponse,
    summary="Submit an activity for admin review",
)
def submit_activity(
    activity_id: int,
    provider_profile: Annotated[
        ProviderProfile,
        Depends(require_activity_provider),
    ],
    db: Annotated[Session, Depends(get_db)],
):
    activity = get_owned_activity(
        activity_id,
        provider_profile,
        db,
    )

    if activity.approval_status != "draft":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only draft activities can be submitted",
        )

    activity.approval_status = "pending"
    activity.is_active = False

    try:
        db.commit()
        db.refresh(activity)
    except Exception:
        db.rollback()
        raise

    return activity


@router.patch(
    "/{activity_id}/active",
    response_model=ActivityResponse,
    summary="Activate or deactivate an approved activity",
)
def set_activity_active_status(
    activity_id: int,
    payload: ActivityActiveUpdate,
    provider_profile: Annotated[
        ProviderProfile,
        Depends(require_activity_provider),
    ],
    db: Annotated[Session, Depends(get_db)],
):
    activity = get_owned_activity(
        activity_id,
        provider_profile,
        db,
    )

    if payload.is_active:
        if activity.approval_status != "approved":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Only approved activities can be activated",
            )
        activity.is_active = True
    else:
        activity.is_active = False

    sync_activity_listing_reference(db, activity)

    try:
        db.commit()
        db.refresh(activity)
    except Exception:
        db.rollback()
        raise

    return activity


@router.post(
    "/{activity_id}/images",
    response_model=ActivityImageResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload an image to an activity gallery",
)
async def upload_activity_image(
    activity_id: int,
    file: UploadFile = File(...),
    alt_text: str | None = Form(default=None),
    is_cover: bool = Form(default=False),
    display_order: int = Form(default=0),
    provider_profile: Annotated[
        ProviderProfile,
        Depends(require_activity_provider),
    ] = None,
    db: Annotated[Session, Depends(get_db)] = None,
):
    activity = get_owned_activity(
        activity_id,
        provider_profile,
        db,
    )

    current_image_count = (
        db.query(ActivityImage)
        .filter(ActivityImage.activity_id == activity.id)
        .count()
    )
    if current_image_count >= MAX_IMAGES_PER_ACTIVITY:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Maximum image limit of {MAX_IMAGES_PER_ACTIVITY} reached for this activity",
        )

    clean_alt = alt_text.strip() if alt_text and alt_text.strip() else None
    if clean_alt and len(clean_alt) > 255:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="alt_text cannot exceed 255 characters",
        )

    if display_order < 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="display_order cannot be negative",
        )

    original_filename = file.filename or ""
    client_ext = Path(original_filename).suffix.lower()
    if client_ext and client_ext not in ALLOWED_ACTIVITY_IMAGE_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file extension '{client_ext}'. Allowed extensions: {', '.join(sorted(ALLOWED_ACTIVITY_IMAGE_EXTENSIONS))}",
        )

    if file.content_type and file.content_type.lower() not in ALLOWED_ACTIVITY_IMAGE_MIMES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid Content-Type '{file.content_type}'. Must be one of: {', '.join(sorted(ALLOWED_ACTIVITY_IMAGE_MIMES))}",
        )

    try:
        contents = await file.read()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Could not read uploaded image file",
        ) from exc

    if len(contents) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded image file is empty",
        )
    if len(contents) > MAX_ACTIVITY_IMAGE_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Uploaded file exceeds maximum allowed size of {MAX_ACTIVITY_IMAGE_SIZE_BYTES // (1024 * 1024)} MB",
        )

    try:
        with Image.open(io.BytesIO(contents)) as img:
            img.verify()
            detected_format = img.format
            if detected_format not in ALLOWED_ACTIVITY_IMAGE_FORMATS:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Unsupported image format '{detected_format}'. Supported formats: JPEG, PNG, WebP.",
                )

        with Image.open(io.BytesIO(contents)) as img:
            img.load()
            width, height = img.size
            if width < MIN_ACTIVITY_IMAGE_DIMENSION or height < MIN_ACTIVITY_IMAGE_DIMENSION:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Image dimensions ({width}x{height}) are too small. Minimum required: {MIN_ACTIVITY_IMAGE_DIMENSION}x{MIN_ACTIVITY_IMAGE_DIMENSION} pixels.",
                )
            if width > MAX_ACTIVITY_IMAGE_DIMENSION or height > MAX_ACTIVITY_IMAGE_DIMENSION:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Image dimensions ({width}x{height}) exceed maximum allowed {MAX_ACTIVITY_IMAGE_DIMENSION}x{MAX_ACTIVITY_IMAGE_DIMENSION} pixels.",
                )
    except HTTPException:
        raise
    except (UnidentifiedImageError, SyntaxError, ValueError, OSError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or corrupted image content",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to decode image content",
        ) from exc

    ext = ".jpg" if detected_format == "JPEG" else f".{detected_format.lower()}"
    safe_filename = f"act_img_{activity.id}_{uuid.uuid4().hex}{ext}"
    target_dir = get_activity_images_dir().resolve()
    target_file = (target_dir / safe_filename).resolve()

    if not target_file.is_relative_to(target_dir) or target_file == target_dir:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid file path detected",
        )

    with open(target_file, "wb") as f:
        f.write(contents)

    if is_cover:
        db.query(ActivityImage).filter(
            ActivityImage.activity_id == activity.id,
            ActivityImage.is_cover == True,
        ).update({"is_cover": False}, synchronize_session="fetch")

    image_url = f"/uploads/activities/{safe_filename}"
    new_image = ActivityImage(
        activity_id=activity.id,
        image_url=image_url,
        alt_text=clean_alt,
        is_cover=bool(is_cover),
        display_order=int(display_order),
    )

    try:
        db.add(new_image)
        db.commit()
        db.refresh(new_image)
    except Exception:
        db.rollback()
        if target_file.is_file():
            try:
                target_file.unlink()
            except OSError:
                pass
        raise

    return new_image


@router.get(
    "/{activity_id}/images",
    response_model=list[ActivityImageResponse],
    summary="List photos for a provider's activity",
)
def list_activity_images(
    activity_id: int,
    provider_profile: Annotated[
        ProviderProfile,
        Depends(require_activity_provider),
    ],
    db: Annotated[Session, Depends(get_db)],
):
    activity = get_owned_activity(
        activity_id,
        provider_profile,
        db,
    )

    return (
        db.query(ActivityImage)
        .filter(ActivityImage.activity_id == activity.id)
        .order_by(ActivityImage.display_order.asc(), ActivityImage.id.asc())
        .all()
    )


@router.patch(
    "/{activity_id}/images/{image_id}",
    response_model=ActivityImageResponse,
    summary="Update activity image metadata",
)
def update_activity_image(
    activity_id: int,
    image_id: int,
    payload: ActivityImageUpdate,
    provider_profile: Annotated[
        ProviderProfile,
        Depends(require_activity_provider),
    ],
    db: Annotated[Session, Depends(get_db)],
):
    activity = get_owned_activity(
        activity_id,
        provider_profile,
        db,
    )

    image = (
        db.query(ActivityImage)
        .filter(
            ActivityImage.id == image_id,
            ActivityImage.activity_id == activity.id,
        )
        .first()
    )

    if not image:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Activity image not found",
        )

    if payload.is_cover is True:
        db.query(ActivityImage).filter(
            ActivityImage.activity_id == activity.id,
            ActivityImage.is_cover == True,
        ).update({"is_cover": False}, synchronize_session="fetch")
        image.is_cover = True
    elif payload.is_cover is False:
        image.is_cover = False

    if payload.alt_text is not None:
        clean_alt = payload.alt_text.strip() if payload.alt_text else None
        if clean_alt and len(clean_alt) > 255:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="alt_text cannot exceed 255 characters",
            )
        image.alt_text = clean_alt

    if payload.display_order is not None:
        image.display_order = payload.display_order

    try:
        db.commit()
        db.refresh(image)
    except Exception:
        db.rollback()
        raise

    return image


@router.delete(
    "/{activity_id}/images/{image_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an activity image",
)
def delete_activity_image(
    activity_id: int,
    image_id: int,
    provider_profile: Annotated[
        ProviderProfile,
        Depends(require_activity_provider),
    ],
    db: Annotated[Session, Depends(get_db)],
):
    activity = get_owned_activity(
        activity_id,
        provider_profile,
        db,
        for_update=True,
    )

    image = (
        db.query(ActivityImage)
        .filter(
            ActivityImage.id == image_id,
            ActivityImage.activity_id == activity.id,
        )
        .first()
    )

    if not image:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Activity image not found",
        )

    image_url = image.image_url
    try:
        db.delete(image)
        db.commit()
    except Exception:
        db.rollback()
        raise

    # Physical file cleanup after successful DB deletion
    if image_url and image_url.startswith("/uploads/activities/"):
        filename = Path(image_url).name
        upload_dir = get_activity_images_dir().resolve()
        target_path = (upload_dir / filename).resolve()
        if (
            target_path.is_relative_to(upload_dir)
            and target_path != upload_dir
            and target_path.is_file()
        ):
            try:
                target_path.unlink()
            except OSError as exc:
                logger.warning(
                    "Failed to delete physical image file %s: %s",
                    target_path,
                    exc,
                )

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@activity_uploads_router.get(
    "/uploads/activities/{filename}",
    summary="Serve uploaded activity image",
)
def serve_activity_image(filename: str):
    clean_filename = Path(filename).name
    if clean_filename != filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid filename",
        )
    upload_dir = get_activity_images_dir().resolve()
    target_path = (upload_dir / clean_filename).resolve()
    if (
        not target_path.is_relative_to(upload_dir)
        or target_path == upload_dir
        or not target_path.is_file()
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Image not found",
        )
    ext = target_path.suffix.lower()
    media_type = "image/jpeg" if ext in (".jpg", ".jpeg") else f"image/{ext.lstrip('.')}"
    return FileResponse(target_path, media_type=media_type)

