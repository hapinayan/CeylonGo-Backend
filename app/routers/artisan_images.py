import io
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Header,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from PIL import Image, UnidentifiedImageError
from sqlalchemy.orm import Session

from app.core.dependencies import require_approved_provider
from app.core.security import decode_access_token
from app.database.connection import get_db
from app.models.artisan import ArtisanBusinessImage, ArtisanProductImage
from app.models.artisan_listing import ArtisanListing
from app.models.provider_profile import ProviderProfile
from app.models.showcase_item import ShowcaseItem
from app.models.user import User
from app.schemas.artisan_image import (
    ArtisanBusinessImageResponse,
    ArtisanImageReorderRequest,
    ArtisanImageUpdateRequest,
    ArtisanProductImageResponse,
)

router = APIRouter(prefix="/api/v1", tags=["Artisan Gallery"])
artisan_uploads_router = APIRouter(tags=["Artisan Uploads"])

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
ALLOWED_MIMES = {"image/jpeg", "image/png", "image/webp"}
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}
MAX_IMAGE_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB
MIN_IMAGE_DIMENSION = 50
MAX_IMAGE_DIMENSION = 8000
MAX_IMAGES_PER_ENTITY = 20

optional_security_scheme = HTTPBearer(auto_error=False)


def get_artisan_business_images_dir() -> Path:
    base_dir = os.getenv(
        "ARTISAN_BUSINESS_IMAGES_DIR",
        str(Path(os.getcwd()) / "uploads" / "artisan" / "business"),
    )
    p = Path(base_dir)
    p.mkdir(parents=True, exist_ok=True)
    return p.resolve()


def get_artisan_product_images_dir() -> Path:
    base_dir = os.getenv(
        "ARTISAN_PRODUCT_IMAGES_DIR",
        str(Path(os.getcwd()) / "uploads" / "artisan" / "products"),
    )
    p = Path(base_dir)
    p.mkdir(parents=True, exist_ok=True)
    return p.resolve()


def get_optional_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(optional_security_scheme)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
) -> User | None:
    if not credentials or not credentials.credentials:
        return None
    try:
        payload = decode_access_token(credentials.credentials)
        user_id_raw = payload.get("sub")
        if not user_id_raw:
            return None
        return db.query(User).filter(User.id == int(user_id_raw), User.is_active == True).first()
    except Exception:
        return None


async def validate_and_read_image_file(file: UploadFile) -> tuple[bytes, str]:
    original_filename = file.filename or ""
    client_ext = Path(original_filename).suffix.lower()
    if client_ext and client_ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file extension '{client_ext}'. Allowed extensions: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )

    if file.content_type and file.content_type.lower() not in ALLOWED_MIMES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid Content-Type '{file.content_type}'. Must be one of: {', '.join(sorted(ALLOWED_MIMES))}",
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
    if len(contents) > MAX_IMAGE_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Uploaded file exceeds maximum allowed size of {MAX_IMAGE_SIZE_BYTES // (1024 * 1024)} MB",
        )

    try:
        with Image.open(io.BytesIO(contents)) as img:
            img.verify()
            detected_format = img.format
            if detected_format not in ALLOWED_FORMATS:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Unsupported image format '{detected_format}'. Supported formats: JPEG, PNG, WebP.",
                )

        with Image.open(io.BytesIO(contents)) as img:
            img.load()
            width, height = img.size
            if width < MIN_IMAGE_DIMENSION or height < MIN_IMAGE_DIMENSION:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Image dimensions ({width}x{height}) are too small. Minimum required: {MIN_IMAGE_DIMENSION}x{MIN_IMAGE_DIMENSION} pixels.",
                )
            if width > MAX_IMAGE_DIMENSION or height > MAX_IMAGE_DIMENSION:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Image dimensions ({width}x{height}) exceed maximum allowed {MAX_IMAGE_DIMENSION}x{MAX_IMAGE_DIMENSION} pixels.",
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

    canonical_ext = ".jpg" if detected_format == "JPEG" else f".{detected_format.lower()}"
    return contents, canonical_ext


# ==============================================================================
# ARTISAN BUSINESS GALLERY ENDPOINTS
# ==============================================================================

@router.post(
    "/artisan-listings/{listing_id}/images",
    response_model=ArtisanBusinessImageResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload an image to an artisan business gallery",
)
async def upload_artisan_business_image(
    listing_id: int,
    file: UploadFile = File(...),
    caption: str | None = Form(default=None),
    is_primary: bool = Form(default=False),
    display_order: int | None = Form(default=None),
    provider_profile: Annotated[ProviderProfile, Depends(require_approved_provider)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
):
    if provider_profile.provider_type != "artisan":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: only artisan service providers can upload business images",
        )

    listing = db.query(ArtisanListing).filter(ArtisanListing.id == listing_id).first()
    if not listing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artisan listing not found")
    if listing.provider_profile_id != provider_profile.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access forbidden: you do not own this listing")

    existing_images = (
        db.query(ArtisanBusinessImage)
        .filter(ArtisanBusinessImage.artisan_listing_id == listing_id)
        .all()
    )
    if len(existing_images) >= MAX_IMAGES_PER_ENTITY:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Maximum image limit of {MAX_IMAGES_PER_ENTITY} reached for this listing",
        )

    clean_caption = caption.strip() if caption and caption.strip() else None
    if clean_caption and len(clean_caption) > 255:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Caption cannot exceed 255 characters")

    contents, canonical_ext = await validate_and_read_image_file(file)

    safe_filename = f"artisan_bus_{listing_id}_{uuid.uuid4().hex}{canonical_ext}"
    target_dir = get_artisan_business_images_dir()
    target_file = (target_dir / safe_filename).resolve()

    if not str(target_file).startswith(str(target_dir.resolve())):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid file path detected")

    with open(target_file, "wb") as f:
        f.write(contents)

    make_primary = bool(is_primary) or (len(existing_images) == 0)

    if make_primary and len(existing_images) > 0:
        db.query(ArtisanBusinessImage).filter(
            ArtisanBusinessImage.artisan_listing_id == listing_id,
            ArtisanBusinessImage.is_primary == True,
        ).update({"is_primary": False})

    if display_order is None or display_order < 0:
        max_order = max([img.display_order for img in existing_images], default=-1)
        display_order = max_order + 1

    image_url = f"/uploads/artisan/business/{safe_filename}"
    new_image = ArtisanBusinessImage(
        artisan_listing_id=listing_id,
        image_url=image_url,
        caption=clean_caption,
        display_order=display_order,
        is_primary=make_primary,
    )

    try:
        db.add(new_image)
        if make_primary:
            listing.cover_image_url = image_url
        listing.updated_at = datetime.now(timezone.utc)
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

    return ArtisanBusinessImageResponse.model_validate(new_image)


@router.get(
    "/artisan-listings/{listing_id}/images",
    response_model=list[ArtisanBusinessImageResponse],
    summary="List photo gallery images for an artisan listing",
)
def list_artisan_business_images(
    listing_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User | None, Depends(get_optional_user)] = None,
):
    listing = db.query(ArtisanListing).filter(ArtisanListing.id == listing_id).first()
    if not listing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artisan listing not found")

    is_owner = False
    if current_user and current_user.role == "provider":
        profile = db.query(ProviderProfile).filter(ProviderProfile.user_id == current_user.id).first()
        if profile and profile.id == listing.provider_profile_id:
            is_owner = True

    if not is_owner:
        provider = db.query(ProviderProfile).filter(ProviderProfile.id == listing.provider_profile_id).first()
        if not (
            listing.is_active
            and listing.approval_status == "approved"
            and provider
            and provider.approval_status == "approved"
        ):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artisan listing not found")

    images = (
        db.query(ArtisanBusinessImage)
        .filter(ArtisanBusinessImage.artisan_listing_id == listing_id)
        .order_by(
            ArtisanBusinessImage.is_primary.desc(),
            ArtisanBusinessImage.display_order.asc(),
            ArtisanBusinessImage.id.asc(),
        )
        .all()
    )
    return [ArtisanBusinessImageResponse.model_validate(img) for img in images]


@router.patch(
    "/artisan-listings/{listing_id}/images/reorder",
    response_model=list[ArtisanBusinessImageResponse],
    summary="Reorder gallery images for an artisan listing",
)
def reorder_artisan_business_images(
    listing_id: int,
    payload: ArtisanImageReorderRequest,
    provider_profile: Annotated[ProviderProfile, Depends(require_approved_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    if provider_profile.provider_type != "artisan":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access forbidden: only artisan service providers can manage gallery images")

    listing = db.query(ArtisanListing).filter(ArtisanListing.id == listing_id).first()
    if not listing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artisan listing not found")
    if listing.provider_profile_id != provider_profile.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access forbidden: you do not own this listing")

    existing_images = {
        img.id: img
        for img in db.query(ArtisanBusinessImage)
        .filter(ArtisanBusinessImage.artisan_listing_id == listing_id)
        .all()
    }

    for item in payload.images:
        if item.id not in existing_images:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Image ID {item.id} does not belong to this listing",
            )

    for item in payload.images:
        existing_images[item.id].display_order = item.display_order

    listing.updated_at = datetime.now(timezone.utc)
    db.commit()

    all_images = (
        db.query(ArtisanBusinessImage)
        .filter(ArtisanBusinessImage.artisan_listing_id == listing_id)
        .order_by(
            ArtisanBusinessImage.is_primary.desc(),
            ArtisanBusinessImage.display_order.asc(),
            ArtisanBusinessImage.id.asc(),
        )
        .all()
    )
    return [ArtisanBusinessImageResponse.model_validate(img) for img in all_images]


@router.patch(
    "/artisan-listings/{listing_id}/images/{image_id}/set-primary",
    response_model=ArtisanBusinessImageResponse,
    summary="Set an image as primary for an artisan listing",
)
@router.put(
    "/artisan-listings/{listing_id}/images/{image_id}/set-primary",
    response_model=ArtisanBusinessImageResponse,
    include_in_schema=False,
)
def set_primary_artisan_business_image(
    listing_id: int,
    image_id: int,
    provider_profile: Annotated[ProviderProfile, Depends(require_approved_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    if provider_profile.provider_type != "artisan":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access forbidden: only artisan service providers can manage gallery images")

    listing = db.query(ArtisanListing).filter(ArtisanListing.id == listing_id).first()
    if not listing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artisan listing not found")
    if listing.provider_profile_id != provider_profile.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access forbidden: you do not own this listing")

    target_image = (
        db.query(ArtisanBusinessImage)
        .filter(ArtisanBusinessImage.id == image_id, ArtisanBusinessImage.artisan_listing_id == listing_id)
        .first()
    )
    if not target_image:
        other = db.query(ArtisanBusinessImage).filter(ArtisanBusinessImage.id == image_id).first()
        if other:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Image does not belong to this listing")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Image not found")

    db.query(ArtisanBusinessImage).filter(
        ArtisanBusinessImage.artisan_listing_id == listing_id,
        ArtisanBusinessImage.id != target_image.id,
    ).update({"is_primary": False})

    target_image.is_primary = True
    listing.cover_image_url = target_image.image_url
    listing.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(target_image)
    return ArtisanBusinessImageResponse.model_validate(target_image)


@router.patch(
    "/artisan-listings/{listing_id}/images/{image_id}",
    response_model=ArtisanBusinessImageResponse,
    summary="Update caption or display order of an artisan business gallery image",
)
def update_artisan_business_image_metadata(
    listing_id: int,
    image_id: int,
    payload: ArtisanImageUpdateRequest,
    provider_profile: Annotated[ProviderProfile, Depends(require_approved_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    if provider_profile.provider_type != "artisan":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access forbidden: only artisan service providers can manage gallery images")

    listing = db.query(ArtisanListing).filter(ArtisanListing.id == listing_id).first()
    if not listing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artisan listing not found")
    if listing.provider_profile_id != provider_profile.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access forbidden: you do not own this listing")

    target_image = (
        db.query(ArtisanBusinessImage)
        .filter(ArtisanBusinessImage.id == image_id, ArtisanBusinessImage.artisan_listing_id == listing_id)
        .first()
    )
    if not target_image:
        other = db.query(ArtisanBusinessImage).filter(ArtisanBusinessImage.id == image_id).first()
        if other:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Image does not belong to this listing")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Image not found")

    if payload.caption is not None:
        target_image.caption = payload.caption.strip() if payload.caption.strip() else None
    if payload.display_order is not None:
        target_image.display_order = payload.display_order

    if payload.is_primary is True:
        db.query(ArtisanBusinessImage).filter(
            ArtisanBusinessImage.artisan_listing_id == listing_id,
            ArtisanBusinessImage.id != target_image.id,
        ).update({"is_primary": False})
        target_image.is_primary = True
        listing.cover_image_url = target_image.image_url

    listing.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(target_image)
    return ArtisanBusinessImageResponse.model_validate(target_image)


@router.delete(
    "/artisan-listings/{listing_id}/images/{image_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an image from an artisan listing gallery",
)
def delete_artisan_business_image(
    listing_id: int,
    image_id: int,
    provider_profile: Annotated[ProviderProfile, Depends(require_approved_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    if provider_profile.provider_type != "artisan":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access forbidden: only artisan service providers can manage gallery images")

    listing = db.query(ArtisanListing).filter(ArtisanListing.id == listing_id).first()
    if not listing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artisan listing not found")
    if listing.provider_profile_id != provider_profile.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access forbidden: you do not own this listing")

    target_image = (
        db.query(ArtisanBusinessImage)
        .filter(ArtisanBusinessImage.id == image_id, ArtisanBusinessImage.artisan_listing_id == listing_id)
        .first()
    )
    if not target_image:
        other = db.query(ArtisanBusinessImage).filter(ArtisanBusinessImage.id == image_id).first()
        if other:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Image does not belong to this listing")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Image not found")

    was_primary = target_image.is_primary
    image_url = target_image.image_url

    db.delete(target_image)
    db.flush()

    if was_primary:
        remaining = (
            db.query(ArtisanBusinessImage)
            .filter(ArtisanBusinessImage.artisan_listing_id == listing_id)
            .order_by(ArtisanBusinessImage.display_order.asc(), ArtisanBusinessImage.id.asc())
            .all()
        )
        if remaining:
            remaining[0].is_primary = True
            listing.cover_image_url = remaining[0].image_url
        else:
            listing.cover_image_url = None

    listing.updated_at = datetime.now(timezone.utc)
    db.commit()

    if image_url:
        upload_dir = get_artisan_business_images_dir().resolve()
        filename = os.path.basename(image_url)
        target_path = (upload_dir / filename).resolve()
        if str(target_path).startswith(str(upload_dir)) and target_path.is_file() and target_path != upload_dir:
            try:
                target_path.unlink()
            except OSError:
                pass

    return None


# ==============================================================================
# SHOWCASE PRODUCT GALLERY ENDPOINTS
# ==============================================================================

@router.post(
    "/showcase-items/{item_id}/images",
    response_model=ArtisanProductImageResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload an image to a showcase product gallery",
)
async def upload_artisan_product_image(
    item_id: int,
    file: UploadFile = File(...),
    caption: str | None = Form(default=None),
    is_primary: bool = Form(default=False),
    display_order: int | None = Form(default=None),
    provider_profile: Annotated[ProviderProfile, Depends(require_approved_provider)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
):
    if provider_profile.provider_type != "artisan":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: only artisan service providers can upload product images",
        )

    item = db.query(ShowcaseItem).filter(ShowcaseItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Showcase item not found")

    listing = db.query(ArtisanListing).filter(ArtisanListing.id == item.artisan_listing_id).first()
    if not listing or listing.provider_profile_id != provider_profile.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access forbidden: you do not own this showcase product")

    existing_images = (
        db.query(ArtisanProductImage)
        .filter(ArtisanProductImage.showcase_item_id == item_id)
        .all()
    )
    if len(existing_images) >= MAX_IMAGES_PER_ENTITY:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Maximum image limit of {MAX_IMAGES_PER_ENTITY} reached for this product",
        )

    clean_caption = caption.strip() if caption and caption.strip() else None
    if clean_caption and len(clean_caption) > 255:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Caption cannot exceed 255 characters")

    contents, canonical_ext = await validate_and_read_image_file(file)

    safe_filename = f"artisan_prod_{item_id}_{uuid.uuid4().hex}{canonical_ext}"
    target_dir = get_artisan_product_images_dir()
    target_file = (target_dir / safe_filename).resolve()

    if not str(target_file).startswith(str(target_dir.resolve())):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid file path detected")

    with open(target_file, "wb") as f:
        f.write(contents)

    make_primary = bool(is_primary) or (len(existing_images) == 0)

    if make_primary and len(existing_images) > 0:
        db.query(ArtisanProductImage).filter(
            ArtisanProductImage.showcase_item_id == item_id,
            ArtisanProductImage.is_primary == True,
        ).update({"is_primary": False})

    if display_order is None or display_order < 0:
        max_order = max([img.display_order for img in existing_images], default=-1)
        display_order = max_order + 1

    image_url = f"/uploads/artisan/products/{safe_filename}"
    new_image = ArtisanProductImage(
        showcase_item_id=item_id,
        image_url=image_url,
        caption=clean_caption,
        display_order=display_order,
        is_primary=make_primary,
    )

    try:
        db.add(new_image)
        if make_primary:
            item.image_url = image_url
        item.updated_at = datetime.now(timezone.utc)
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

    return ArtisanProductImageResponse.model_validate(new_image)


@router.get(
    "/showcase-items/{item_id}/images",
    response_model=list[ArtisanProductImageResponse],
    summary="List photo gallery images for a showcase product",
)
def list_artisan_product_images(
    item_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User | None, Depends(get_optional_user)] = None,
):
    item = db.query(ShowcaseItem).filter(ShowcaseItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Showcase item not found")

    listing = db.query(ArtisanListing).filter(ArtisanListing.id == item.artisan_listing_id).first()
    if not listing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artisan listing not found")

    is_owner = False
    if current_user and current_user.role == "provider":
        profile = db.query(ProviderProfile).filter(ProviderProfile.user_id == current_user.id).first()
        if profile and profile.id == listing.provider_profile_id:
            is_owner = True

    if not is_owner:
        provider = db.query(ProviderProfile).filter(ProviderProfile.id == listing.provider_profile_id).first()
        if not (
            item.is_active
            and item.approval_status == "approved"
            and listing.is_active
            and listing.approval_status == "approved"
            and provider
            and provider.approval_status == "approved"
        ):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Showcase item not found")

    images = (
        db.query(ArtisanProductImage)
        .filter(ArtisanProductImage.showcase_item_id == item_id)
        .order_by(
            ArtisanProductImage.is_primary.desc(),
            ArtisanProductImage.display_order.asc(),
            ArtisanProductImage.id.asc(),
        )
        .all()
    )
    return [ArtisanProductImageResponse.model_validate(img) for img in images]


@router.patch(
    "/showcase-items/{item_id}/images/reorder",
    response_model=list[ArtisanProductImageResponse],
    summary="Reorder gallery images for a showcase product",
)
def reorder_artisan_product_images(
    item_id: int,
    payload: ArtisanImageReorderRequest,
    provider_profile: Annotated[ProviderProfile, Depends(require_approved_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    if provider_profile.provider_type != "artisan":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access forbidden: only artisan service providers can manage gallery images")

    item = db.query(ShowcaseItem).filter(ShowcaseItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Showcase item not found")

    listing = db.query(ArtisanListing).filter(ArtisanListing.id == item.artisan_listing_id).first()
    if not listing or listing.provider_profile_id != provider_profile.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access forbidden: you do not own this showcase product")

    existing_images = {
        img.id: img
        for img in db.query(ArtisanProductImage)
        .filter(ArtisanProductImage.showcase_item_id == item_id)
        .all()
    }

    for reorder_item in payload.images:
        if reorder_item.id not in existing_images:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Image ID {reorder_item.id} does not belong to this showcase product",
            )

    for reorder_item in payload.images:
        existing_images[reorder_item.id].display_order = reorder_item.display_order

    item.updated_at = datetime.now(timezone.utc)
    db.commit()

    all_images = (
        db.query(ArtisanProductImage)
        .filter(ArtisanProductImage.showcase_item_id == item_id)
        .order_by(
            ArtisanProductImage.is_primary.desc(),
            ArtisanProductImage.display_order.asc(),
            ArtisanProductImage.id.asc(),
        )
        .all()
    )
    return [ArtisanProductImageResponse.model_validate(img) for img in all_images]


@router.patch(
    "/showcase-items/{item_id}/images/{image_id}/set-primary",
    response_model=ArtisanProductImageResponse,
    summary="Set an image as primary for a showcase product",
)
@router.put(
    "/showcase-items/{item_id}/images/{image_id}/set-primary",
    response_model=ArtisanProductImageResponse,
    include_in_schema=False,
)
def set_primary_artisan_product_image(
    item_id: int,
    image_id: int,
    provider_profile: Annotated[ProviderProfile, Depends(require_approved_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    if provider_profile.provider_type != "artisan":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access forbidden: only artisan service providers can manage gallery images")

    item = db.query(ShowcaseItem).filter(ShowcaseItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Showcase item not found")

    listing = db.query(ArtisanListing).filter(ArtisanListing.id == item.artisan_listing_id).first()
    if not listing or listing.provider_profile_id != provider_profile.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access forbidden: you do not own this showcase product")

    target_image = (
        db.query(ArtisanProductImage)
        .filter(ArtisanProductImage.id == image_id, ArtisanProductImage.showcase_item_id == item_id)
        .first()
    )
    if not target_image:
        other = db.query(ArtisanProductImage).filter(ArtisanProductImage.id == image_id).first()
        if other:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Image does not belong to this showcase product")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Image not found")

    db.query(ArtisanProductImage).filter(
        ArtisanProductImage.showcase_item_id == item_id,
        ArtisanProductImage.id != target_image.id,
    ).update({"is_primary": False})

    target_image.is_primary = True
    item.image_url = target_image.image_url
    item.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(target_image)
    return ArtisanProductImageResponse.model_validate(target_image)


@router.patch(
    "/showcase-items/{item_id}/images/{image_id}",
    response_model=ArtisanProductImageResponse,
    summary="Update caption or display order of a showcase product gallery image",
)
def update_artisan_product_image_metadata(
    item_id: int,
    image_id: int,
    payload: ArtisanImageUpdateRequest,
    provider_profile: Annotated[ProviderProfile, Depends(require_approved_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    if provider_profile.provider_type != "artisan":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access forbidden: only artisan service providers can manage gallery images")

    item = db.query(ShowcaseItem).filter(ShowcaseItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Showcase item not found")

    listing = db.query(ArtisanListing).filter(ArtisanListing.id == item.artisan_listing_id).first()
    if not listing or listing.provider_profile_id != provider_profile.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access forbidden: you do not own this showcase product")

    target_image = (
        db.query(ArtisanProductImage)
        .filter(ArtisanProductImage.id == image_id, ArtisanProductImage.showcase_item_id == item_id)
        .first()
    )
    if not target_image:
        other = db.query(ArtisanProductImage).filter(ArtisanProductImage.id == image_id).first()
        if other:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Image does not belong to this showcase product")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Image not found")

    if payload.caption is not None:
        target_image.caption = payload.caption.strip() if payload.caption.strip() else None
    if payload.display_order is not None:
        target_image.display_order = payload.display_order

    if payload.is_primary is True:
        db.query(ArtisanProductImage).filter(
            ArtisanProductImage.showcase_item_id == item_id,
            ArtisanProductImage.id != target_image.id,
        ).update({"is_primary": False})
        target_image.is_primary = True
        item.image_url = target_image.image_url

    item.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(target_image)
    return ArtisanProductImageResponse.model_validate(target_image)


@router.delete(
    "/showcase-items/{item_id}/images/{image_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an image from a showcase product gallery",
)
def delete_artisan_product_image(
    item_id: int,
    image_id: int,
    provider_profile: Annotated[ProviderProfile, Depends(require_approved_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    if provider_profile.provider_type != "artisan":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access forbidden: only artisan service providers can manage gallery images")

    item = db.query(ShowcaseItem).filter(ShowcaseItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Showcase item not found")

    listing = db.query(ArtisanListing).filter(ArtisanListing.id == item.artisan_listing_id).first()
    if not listing or listing.provider_profile_id != provider_profile.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access forbidden: you do not own this showcase product")

    target_image = (
        db.query(ArtisanProductImage)
        .filter(ArtisanProductImage.id == image_id, ArtisanProductImage.showcase_item_id == item_id)
        .first()
    )
    if not target_image:
        other = db.query(ArtisanProductImage).filter(ArtisanProductImage.id == image_id).first()
        if other:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Image does not belong to this showcase product")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Image not found")

    was_primary = target_image.is_primary
    image_url = target_image.image_url

    db.delete(target_image)
    db.flush()

    if was_primary:
        remaining = (
            db.query(ArtisanProductImage)
            .filter(ArtisanProductImage.showcase_item_id == item_id)
            .order_by(ArtisanProductImage.display_order.asc(), ArtisanProductImage.id.asc())
            .all()
        )
        if remaining:
            remaining[0].is_primary = True
            item.image_url = remaining[0].image_url
        else:
            item.image_url = None

    item.updated_at = datetime.now(timezone.utc)
    db.commit()

    if image_url:
        upload_dir = get_artisan_product_images_dir().resolve()
        filename = os.path.basename(image_url)
        target_path = (upload_dir / filename).resolve()
        if str(target_path).startswith(str(upload_dir)) and target_path.is_file() and target_path != upload_dir:
            try:
                target_path.unlink()
            except OSError:
                pass

    return None


# ==============================================================================
# PUBLIC IMAGE SERVING ENDPOINTS
# ==============================================================================

@artisan_uploads_router.get(
    "/uploads/artisan/business/{filename}",
    summary="Publicly view an artisan business gallery image",
)
@router.get(
    "/uploads/artisan/business/{filename}",
    summary="Publicly view an artisan business gallery image (api alias)",
    include_in_schema=False,
)
def serve_artisan_business_image(filename: str):
    clean_filename = os.path.basename(filename)
    upload_dir = get_artisan_business_images_dir().resolve()
    target_path = (upload_dir / clean_filename).resolve()
    if not str(target_path).startswith(str(upload_dir)) or not target_path.is_file() or target_path == upload_dir:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Image not found")
    ext = target_path.suffix.lower()
    media_type = "image/jpeg" if ext in (".jpg", ".jpeg") else f"image/{ext.lstrip('.')}"
    return FileResponse(target_path, media_type=media_type)


@artisan_uploads_router.get(
    "/uploads/artisan/products/{filename}",
    summary="Publicly view an artisan product gallery image",
)
@router.get(
    "/uploads/artisan/products/{filename}",
    summary="Publicly view an artisan product gallery image (api alias)",
    include_in_schema=False,
)
def serve_artisan_product_image(filename: str):
    clean_filename = os.path.basename(filename)
    upload_dir = get_artisan_product_images_dir().resolve()
    target_path = (upload_dir / clean_filename).resolve()
    if not str(target_path).startswith(str(upload_dir)) or not target_path.is_file() or target_path == upload_dir:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Image not found")
    ext = target_path.suffix.lower()
    media_type = "image/jpeg" if ext in (".jpg", ".jpeg") else f"image/{ext.lstrip('.')}"
    return FileResponse(target_path, media_type=media_type)

