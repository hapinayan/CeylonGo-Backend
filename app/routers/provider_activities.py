
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
    Response,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse
from PIL import Image, UnidentifiedImageError
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

from app.core.dependencies import require_approved_provider
from app.core.location_validation import (
    ACTIVITY_REVIEW_STATE,
    validate_coordinate_pair,
    write_review_snapshot,
)
from app.database.connection import get_db
from app.models.activity import Activity
from app.models.activity_image import ActivityImage
from app.models.provider_profile import ProviderProfile
from app.schemas.activity import (
    ActivityActiveUpdate,
    ActivityCreate,
    ActivityImageResponse,
    ActivityImageUpdate,
    ActivityResponse,
    ActivityUpdate,
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

