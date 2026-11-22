from datetime import date, timedelta
import mimetypes
import os
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Path,
    Query,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.dependencies import require_approved_provider
from app.database.connection import get_db
from app.models.provider_profile import ProviderProfile
from app.models.tour_guide import (
    TourGuide,
    TourGuideAvailability,
    TourGuideBooking,
    TourGuideCredential,
    TourGuideImage,
    TourGuideLanguage,
    TourGuidePackage,
    TourGuideServiceArea,
    TourGuideVerification,
)
from app.schemas.tour_guide import (
    CredentialType,
    GuideCategoriesUpdate,
    GuideCategoryInfo,
    TourGuideAvailabilityBulkBlockRequest,
    TourGuideAvailabilityCreate,
    TourGuideAvailabilityResponse,
    TourGuideAvailabilityUpdate,
    TourGuideContactResponse,
    TourGuideContactUpdate,
    TourGuideCreate,
    TourGuideCredentialResponse,
    TourGuideCredentialUpdate,
    TourGuideImageResponse,
    TourGuideImageUpdate,
    TourGuideLanguageCreate,
    TourGuideLanguageResponse,
    TourGuideLanguageUpdate,
    TourGuidePackageCreate,
    TourGuidePackageResponse,
    TourGuidePackageUpdate,
    TourGuideResponse,
    TourGuideServiceAreaCreate,
    TourGuideServiceAreaResponse,
    TourGuideServiceAreaUpdate,
    TourGuideUpdate,
    TourGuideVerificationRecordResponse,
    TourGuideVerificationStatusResponse,
)
from app.services.tour_guide_sync import (
    ALLOWED_CREDENTIAL_EXTENSIONS,
    ALLOWED_IMAGE_EXTENSIONS,
    GUIDE_CATEGORIES,
    categories_to_specialties_string,
    delete_local_credential_file,
    delete_local_tour_guide_image,
    find_conflicting_active_booking,
    find_overlapping_availability_slot,
    get_tour_guide_credentials_dir,
    get_tour_guide_images_dir,
    resolve_safe_storage_path,
    save_tour_guide_credential_file,
    save_tour_guide_image_file,
    sync_tour_guide_listing_reference,
)

MAX_GALLERY_IMAGES_PER_GUIDE = 20

router = APIRouter(
    prefix="/api/v1/provider/tour-guides",
    tags=["Tour Guide Provider"],
)

public_router = APIRouter(
    tags=["Tour Guides"],
)


def require_tour_guide_provider(
    provider_profile: Annotated[
        ProviderProfile,
        Depends(require_approved_provider),
    ],
) -> ProviderProfile:
    """
    Allow only approved Tour Guide providers.
    """

    if provider_profile.provider_type != "tour_guide":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only approved Tour Guide providers can access this endpoint",
        )

    return provider_profile


GuideProvider = Annotated[ProviderProfile, Depends(require_tour_guide_provider)]
DatabaseSession = Annotated[Session, Depends(get_db)]


def find_owned_guide(db: Session, provider_id: int) -> TourGuide | None:
    return db.query(TourGuide).filter(TourGuide.provider_profile_id == provider_id).first()


def require_owned_guide(provider: GuideProvider, db: DatabaseSession) -> TourGuide:
    guide = find_owned_guide(db, provider.id)
    if guide is None:
        raise HTTPException(status_code=404, detail="Tour guide profile not found")
    return guide


OwnedGuide = Annotated[TourGuide, Depends(require_owned_guide)]


def commit_changes(db: Session) -> None:
    try:
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise


def _serialize_credential(cred: TourGuideCredential) -> TourGuideCredentialResponse:
    return TourGuideCredentialResponse(
        id=cred.id,
        tour_guide_id=cred.tour_guide_id,
        credential_type=cred.credential_type,
        title=cred.title,
        issuing_authority=cred.issuing_authority,
        certificate_number=cred.certificate_number,
        issue_date=cred.issue_date,
        expiry_date=cred.expiry_date,
        is_confidential=cred.is_confidential,
        verification_status=cred.verification_status,
        has_document=bool(cred.file_reference),
        created_at=cred.created_at,
        updated_at=cred.updated_at,
    )


def _stream_private_credential_file(cred: TourGuideCredential) -> FileResponse:
    if not cred.file_reference:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Credential document not found",
        )
    filename = os.path.basename(cred.file_reference)
    file_path = resolve_safe_storage_path(get_tour_guide_credentials_dir(), filename)
    if not file_path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Credential document file not found on storage server",
        )
    ext = file_path.suffix.lower()
    media_type = (
        ALLOWED_CREDENTIAL_EXTENSIONS.get(ext)
        or mimetypes.guess_type(str(file_path))[0]
        or "application/octet-stream"
    )
    return FileResponse(
        path=str(file_path),
        media_type=media_type,
        filename=f"credential_{cred.tour_guide_id}_{cred.id}{ext}",
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


# =============================================================================
# PUBLIC GALLERY IMAGE SERVING
# =============================================================================

@public_router.get(
    "/api/v1/uploads/tour-guides/{filename}",
    response_class=FileResponse,
    summary="Serve a public tour guide gallery image",
)
def serve_tour_guide_image(filename: str):
    file_path = resolve_safe_storage_path(get_tour_guide_images_dir(), filename)
    ext = file_path.suffix.lower()
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Image not found",
        )
    if not file_path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Image not found",
        )
    return FileResponse(
        path=str(file_path),
        media_type=ALLOWED_IMAGE_EXTENSIONS[ext],
        headers={"X-Content-Type-Options": "nosniff"},
    )


# =============================================================================
# 1. CORE PROVIDER PROFILE CRUD (PRESERVED)
# =============================================================================

@router.post(
    "",
    response_model=TourGuideResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a draft tour guide profile",
)
def create_tour_guide(payload: TourGuideCreate, provider: GuideProvider, db: DatabaseSession):
    if find_owned_guide(db, provider.id) is not None:
        raise HTTPException(status_code=409, detail="Tour guide profile already exists")

    guide = TourGuide(
        **payload.model_dump(),
        provider_profile_id=provider.id,
        approval_status="draft",
        is_active=False,
    )
    db.add(guide)
    try:
        commit_changes(db)
    except IntegrityError as exc:
        if find_owned_guide(db, provider.id) is not None:
            raise HTTPException(status_code=409, detail="Tour guide profile already exists") from exc
        raise
    return guide


@router.get("", response_model=TourGuideResponse, summary="Get your tour guide profile")
def get_tour_guide(guide: OwnedGuide):
    return guide


@router.patch("", response_model=TourGuideResponse, summary="Update your tour guide profile")
def update_tour_guide(payload: TourGuideUpdate, guide: OwnedGuide, db: DatabaseSession):
    changes = payload.model_dump(exclude_unset=True)
    latitude = changes.get("base_latitude", guide.base_latitude)
    longitude = changes.get("base_longitude", guide.base_longitude)
    if (latitude is None) != (longitude is None):
        raise HTTPException(status_code=422, detail="Latitude and longitude must be provided together")

    location_fields = ("base_latitude", "base_longitude", "base_city", "base_district")
    if any(key in changes and changes[key] != getattr(guide, key) for key in location_fields):
        if guide.approval_status == "pending":
            raise HTTPException(409, detail="Location is currently under review")
        if guide.approval_status == "approved":
            changes.update(
                approval_status="draft", is_active=False, reviewed_by=None, reviewed_at=None,
            )
        try:
            updated = (
                db.query(TourGuide)
                .filter(
                    TourGuide.id == guide.id,
                    TourGuide.base_latitude == guide.base_latitude,
                    TourGuide.base_longitude == guide.base_longitude,
                    TourGuide.base_city == guide.base_city,
                    TourGuide.base_district == guide.base_district,
                    TourGuide.approval_status == guide.approval_status,
                )
                .update(changes, synchronize_session=False)
            )
        except SQLAlchemyError:
            db.rollback()
            raise
        if updated == 0:
            db.rollback()
            raise HTTPException(
                status_code=409, detail="Tour guide coordinates changed. Retry the update.",
            )
        db.expire(guide)
        sync_tour_guide_listing_reference(db, guide)
        commit_changes(db)
        db.expire(guide)
        return guide

    for field, value in changes.items():
        setattr(guide, field, value)
    sync_tour_guide_listing_reference(db, guide)
    commit_changes(db)
    return guide


@router.post("/submit", response_model=TourGuideResponse)
def submit_guide_for_location_review(guide: OwnedGuide, db: DatabaseSession):
    if guide.base_latitude is None or guide.base_longitude is None:
        raise HTTPException(422, detail="Provide a public base location before submission")
    if guide.approval_status not in ("draft", "rejected"):
        raise HTTPException(409, detail="Only draft or rejected profiles can be submitted")
    updated = db.query(TourGuide).filter(
        TourGuide.id == guide.id, TourGuide.approval_status == guide.approval_status,
        TourGuide.base_latitude == guide.base_latitude,
        TourGuide.base_longitude == guide.base_longitude,
        TourGuide.base_city == guide.base_city, TourGuide.base_district == guide.base_district,
    ).update(
        {"approval_status": "pending", "is_active": False, "rejection_reason": None},
        synchronize_session=False,
    )
    if updated != 1:
        db.rollback()
        raise HTTPException(409, detail="Tour guide location or review status changed")
    db.expire(guide)
    sync_tour_guide_listing_reference(db, guide)
    commit_changes(db)
    db.expire(guide)
    return guide


@router.delete("", status_code=status.HTTP_204_NO_CONTENT, summary="Deactivate your tour guide profile")
def deactivate_tour_guide(guide: OwnedGuide, db: DatabaseSession):
    guide.is_active = False
    sync_tour_guide_listing_reference(db, guide)
    commit_changes(db)


# =============================================================================
# 2. GUIDE CATEGORIES MANAGEMENT (PR-07)
# =============================================================================

@router.put(
    "/categories",
    response_model=TourGuideResponse,
    summary="Set validated tour guide specialty categories",
)
def set_tour_guide_categories(
    payload: GuideCategoriesUpdate,
    guide: OwnedGuide,
    db: DatabaseSession,
):
    guide.specialties = categories_to_specialties_string(list(payload.categories))
    commit_changes(db)
    db.refresh(guide)
    return guide


# =============================================================================
# 3. LANGUAGES MANAGEMENT (PR-05)
# =============================================================================

@router.get(
    "/languages",
    response_model=list[TourGuideLanguageResponse],
    summary="List languages supported by your tour guide profile",
)
def list_guide_languages(guide: OwnedGuide, db: DatabaseSession):
    return (
        db.query(TourGuideLanguage)
        .filter(TourGuideLanguage.tour_guide_id == guide.id)
        .order_by(TourGuideLanguage.is_primary.desc(), TourGuideLanguage.language_name.asc())
        .all()
    )


@router.post(
    "/languages",
    response_model=TourGuideLanguageResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a language to your tour guide profile",
)
def add_guide_language(
    payload: TourGuideLanguageCreate,
    guide: OwnedGuide,
    db: DatabaseSession,
):
    existing = (
        db.query(TourGuideLanguage)
        .filter(
            TourGuideLanguage.tour_guide_id == guide.id,
            TourGuideLanguage.language_code == payload.language_code,
        )
        .first()
    )
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Language '{payload.language_code}' is already added to your profile",
        )

    if payload.is_primary:
        db.query(TourGuideLanguage).filter(
            TourGuideLanguage.tour_guide_id == guide.id,
            TourGuideLanguage.is_primary == True,
        ).update({"is_primary": False}, synchronize_session=False)

    language = TourGuideLanguage(
        tour_guide_id=guide.id,
        **payload.model_dump(),
    )
    db.add(language)
    try:
        commit_changes(db)
    except IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Language '{payload.language_code}' is already added to your profile",
        ) from exc
    db.refresh(language)
    return language


@router.patch(
    "/languages/{language_id}",
    response_model=TourGuideLanguageResponse,
    summary="Update a language on your tour guide profile",
)
def update_guide_language(
    language_id: Annotated[int, Path(gt=0)],
    payload: TourGuideLanguageUpdate,
    guide: OwnedGuide,
    db: DatabaseSession,
):
    language = (
        db.query(TourGuideLanguage)
        .filter(
            TourGuideLanguage.id == language_id,
            TourGuideLanguage.tour_guide_id == guide.id,
        )
        .first()
    )
    if language is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Language not found")

    changes = payload.model_dump(exclude_unset=True)
    if changes.get("is_primary") is True:
        db.query(TourGuideLanguage).filter(
            TourGuideLanguage.tour_guide_id == guide.id,
            TourGuideLanguage.id != language.id,
            TourGuideLanguage.is_primary == True,
        ).update({"is_primary": False}, synchronize_session=False)

    for field, value in changes.items():
        setattr(language, field, value)
    commit_changes(db)
    db.refresh(language)
    return language


@router.delete(
    "/languages/{language_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a language from your tour guide profile",
)
def delete_guide_language(
    language_id: Annotated[int, Path(gt=0)],
    guide: OwnedGuide,
    db: DatabaseSession,
):
    language = (
        db.query(TourGuideLanguage)
        .filter(
            TourGuideLanguage.id == language_id,
            TourGuideLanguage.tour_guide_id == guide.id,
        )
        .first()
    )
    if language is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Language not found")

    db.delete(language)
    commit_changes(db)


# =============================================================================
# 4. SERVICE AREAS MANAGEMENT (PR-06)
# =============================================================================

@router.get(
    "/service-areas",
    response_model=list[TourGuideServiceAreaResponse],
    summary="List districts and destinations covered by your tour guide profile",
)
def list_guide_service_areas(guide: OwnedGuide, db: DatabaseSession):
    return (
        db.query(TourGuideServiceArea)
        .filter(TourGuideServiceArea.tour_guide_id == guide.id)
        .order_by(
            TourGuideServiceArea.is_primary.desc(),
            TourGuideServiceArea.district.asc(),
            TourGuideServiceArea.city.asc(),
        )
        .all()
    )


@router.post(
    "/service-areas",
    response_model=TourGuideServiceAreaResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a covered district/destination service area",
)
def add_guide_service_area(
    payload: TourGuideServiceAreaCreate,
    guide: OwnedGuide,
    db: DatabaseSession,
):
    existing = (
        db.query(TourGuideServiceArea)
        .filter(
            TourGuideServiceArea.tour_guide_id == guide.id,
            TourGuideServiceArea.district == payload.district,
            TourGuideServiceArea.city == payload.city,
        )
        .first()
    )
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This service area (district and city) already exists on your profile",
        )

    if payload.is_primary:
        db.query(TourGuideServiceArea).filter(
            TourGuideServiceArea.tour_guide_id == guide.id,
            TourGuideServiceArea.is_primary == True,
        ).update({"is_primary": False}, synchronize_session=False)

    area = TourGuideServiceArea(
        tour_guide_id=guide.id,
        **payload.model_dump(),
    )
    db.add(area)
    try:
        commit_changes(db)
    except IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This service area (district and city) already exists on your profile",
        ) from exc
    db.refresh(area)
    return area


@router.patch(
    "/service-areas/{area_id}",
    response_model=TourGuideServiceAreaResponse,
    summary="Update a service area on your tour guide profile",
)
def update_guide_service_area(
    area_id: Annotated[int, Path(gt=0)],
    payload: TourGuideServiceAreaUpdate,
    guide: OwnedGuide,
    db: DatabaseSession,
):
    area = (
        db.query(TourGuideServiceArea)
        .filter(
            TourGuideServiceArea.id == area_id,
            TourGuideServiceArea.tour_guide_id == guide.id,
        )
        .first()
    )
    if area is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service area not found")

    changes = payload.model_dump(exclude_unset=True)
    new_district = changes.get("district", area.district)
    new_city = changes.get("city", area.city)
    duplicate = (
        db.query(TourGuideServiceArea)
        .filter(
            TourGuideServiceArea.tour_guide_id == guide.id,
            TourGuideServiceArea.id != area.id,
            TourGuideServiceArea.district == new_district,
            TourGuideServiceArea.city == new_city,
        )
        .first()
    )
    if duplicate is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Another service area with the same district and city already exists",
        )

    if changes.get("is_primary") is True:
        db.query(TourGuideServiceArea).filter(
            TourGuideServiceArea.tour_guide_id == guide.id,
            TourGuideServiceArea.id != area.id,
            TourGuideServiceArea.is_primary == True,
        ).update({"is_primary": False}, synchronize_session=False)

    for field, value in changes.items():
        setattr(area, field, value)
    commit_changes(db)
    db.refresh(area)
    return area


@router.delete(
    "/service-areas/{area_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a service area from your tour guide profile",
)
def delete_guide_service_area(
    area_id: Annotated[int, Path(gt=0)],
    guide: OwnedGuide,
    db: DatabaseSession,
):
    area = (
        db.query(TourGuideServiceArea)
        .filter(
            TourGuideServiceArea.id == area_id,
            TourGuideServiceArea.tour_guide_id == guide.id,
        )
        .first()
    )
    if area is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service area not found")

    db.delete(area)
    commit_changes(db)


# =============================================================================
# 5. PROFILE IMAGE GALLERY MANAGEMENT (PR-03)
# =============================================================================

@router.get(
    "/images",
    response_model=list[TourGuideImageResponse],
    summary="List your tour guide profile and gallery photos",
)
def list_guide_images(guide: OwnedGuide, db: DatabaseSession):
    return (
        db.query(TourGuideImage)
        .filter(TourGuideImage.tour_guide_id == guide.id)
        .order_by(
            TourGuideImage.is_profile.desc(),
            TourGuideImage.display_order.asc(),
            TourGuideImage.id.asc(),
        )
        .all()
    )


@router.post(
    "/images",
    response_model=TourGuideImageResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a profile or gallery photo for your tour guide profile",
)
async def upload_guide_image(
    guide: OwnedGuide,
    db: DatabaseSession,
    file: UploadFile = File(...),
    caption: Annotated[str | None, Form(max_length=255)] = None,
    display_order: Annotated[int, Form(ge=0, le=2147483647)] = 0,
    is_profile: Annotated[bool, Form()] = False,
):
    current_count = (
        db.query(TourGuideImage)
        .filter(TourGuideImage.tour_guide_id == guide.id)
        .count()
    )
    if current_count >= MAX_GALLERY_IMAGES_PER_GUIDE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Maximum of {MAX_GALLERY_IMAGES_PER_GUIDE} gallery images allowed per guide",
        )

    image_url = await save_tour_guide_image_file(guide.id, file)

    try:
        if is_profile:
            db.query(TourGuideImage).filter(
                TourGuideImage.tour_guide_id == guide.id,
                TourGuideImage.is_profile == True,
            ).update({"is_profile": False}, synchronize_session=False)
            db.flush()

        image = TourGuideImage(
            tour_guide_id=guide.id,
            image_url=image_url,
            caption=caption.strip() if caption else None,
            display_order=display_order,
            is_profile=is_profile,
        )
        db.add(image)
        commit_changes(db)
        db.refresh(image)
        return image
    except Exception:
        delete_local_tour_guide_image(image_url)
        raise


@router.patch(
    "/images/{image_id}",
    response_model=TourGuideImageResponse,
    summary="Update photo caption, order, or primary profile image status",
)
def update_guide_image(
    image_id: Annotated[int, Path(gt=0)],
    payload: TourGuideImageUpdate,
    guide: OwnedGuide,
    db: DatabaseSession,
):
    image = (
        db.query(TourGuideImage)
        .filter(
            TourGuideImage.id == image_id,
            TourGuideImage.tour_guide_id == guide.id,
        )
        .first()
    )
    if image is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Image not found")

    changes = payload.model_dump(exclude_unset=True)
    if changes.get("is_profile") is True and not image.is_profile:
        db.query(TourGuideImage).filter(
            TourGuideImage.tour_guide_id == guide.id,
            TourGuideImage.id != image.id,
            TourGuideImage.is_profile == True,
        ).update({"is_profile": False}, synchronize_session=False)
        db.flush()

    for field, value in changes.items():
        setattr(image, field, value)
    commit_changes(db)
    db.refresh(image)
    return image


@router.put(
    "/images/{image_id}/file",
    response_model=TourGuideImageResponse,
    summary="Replace an existing gallery photo file",
)
async def replace_guide_image_file(
    image_id: Annotated[int, Path(gt=0)],
    guide: OwnedGuide,
    db: DatabaseSession,
    file: UploadFile = File(...),
):
    image = (
        db.query(TourGuideImage)
        .filter(
            TourGuideImage.id == image_id,
            TourGuideImage.tour_guide_id == guide.id,
        )
        .first()
    )
    if image is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Image not found")

    old_url = image.image_url
    new_url = await save_tour_guide_image_file(guide.id, file)
    try:
        image.image_url = new_url
        commit_changes(db)
        db.refresh(image)
    except Exception:
        delete_local_tour_guide_image(new_url)
        raise

    if old_url and old_url != new_url:
        delete_local_tour_guide_image(old_url)
    return image


@router.delete(
    "/images/{image_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a photo from your tour guide gallery",
)
def delete_guide_image(
    image_id: Annotated[int, Path(gt=0)],
    guide: OwnedGuide,
    db: DatabaseSession,
):
    image = (
        db.query(TourGuideImage)
        .filter(
            TourGuideImage.id == image_id,
            TourGuideImage.tour_guide_id == guide.id,
        )
        .first()
    )
    if image is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Image not found")

    old_url = image.image_url
    db.delete(image)
    commit_changes(db)
    delete_local_tour_guide_image(old_url)


# =============================================================================
# 6. PRIVATE QUALIFICATIONS & CREDENTIALS MANAGEMENT (PR-04 / GS-02)
# =============================================================================

@router.get(
    "/credentials",
    response_model=list[TourGuideCredentialResponse],
    summary="List your private qualification and license credentials",
)
def list_guide_credentials(guide: OwnedGuide, db: DatabaseSession):
    rows = (
        db.query(TourGuideCredential)
        .filter(TourGuideCredential.tour_guide_id == guide.id)
        .order_by(TourGuideCredential.created_at.desc(), TourGuideCredential.id.desc())
        .all()
    )
    return [_serialize_credential(c) for c in rows]


@router.post(
    "/credentials",
    response_model=TourGuideCredentialResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a private qualification, license, or certificate document",
)
async def upload_guide_credential(
    guide: OwnedGuide,
    db: DatabaseSession,
    credential_type: Annotated[CredentialType, Form()],
    title: Annotated[str, Form(min_length=2, max_length=150)],
    issuing_authority: Annotated[str, Form(min_length=2, max_length=150)],
    file: UploadFile = File(...),
    certificate_number: Annotated[str | None, Form(max_length=100)] = None,
    issue_date: Annotated[date | None, Form()] = None,
    expiry_date: Annotated[date | None, Form()] = None,
):
    if issue_date and expiry_date and expiry_date < issue_date:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="expiry_date must be on or after issue_date",
        )

    clean_title = title.strip()
    clean_authority = issuing_authority.strip()
    if len(clean_title) < 2 or len(clean_authority) < 2:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="title and issuing_authority must be at least 2 characters",
        )

    file_ref = await save_tour_guide_credential_file(guide.id, file)
    try:
        cred = TourGuideCredential(
            tour_guide_id=guide.id,
            credential_type=credential_type,
            title=clean_title,
            issuing_authority=clean_authority,
            certificate_number=certificate_number.strip() if certificate_number else None,
            issue_date=issue_date,
            expiry_date=expiry_date,
            file_reference=file_ref,
            is_confidential=True,
            verification_status="pending",
        )
        db.add(cred)

        if guide.verification_status in ("unverified", "rejected"):
            guide.verification_status = "pending"

        commit_changes(db)
        db.refresh(cred)
        return _serialize_credential(cred)
    except Exception:
        delete_local_credential_file(file_ref)
        raise


@router.get(
    "/credentials/{credential_id}/document",
    response_class=FileResponse,
    summary="Securely view your own private credential document",
)
def view_own_credential_document(
    credential_id: Annotated[int, Path(gt=0)],
    guide: OwnedGuide,
    db: DatabaseSession,
):
    cred = (
        db.query(TourGuideCredential)
        .filter(
            TourGuideCredential.id == credential_id,
            TourGuideCredential.tour_guide_id == guide.id,
        )
        .first()
    )
    if cred is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Credential not found")
    return _stream_private_credential_file(cred)


@router.patch(
    "/credentials/{credential_id}",
    response_model=TourGuideCredentialResponse,
    summary="Update credential metadata (resets credential to pending verification)",
)
def update_guide_credential(
    credential_id: Annotated[int, Path(gt=0)],
    payload: TourGuideCredentialUpdate,
    guide: OwnedGuide,
    db: DatabaseSession,
):
    cred = (
        db.query(TourGuideCredential)
        .filter(
            TourGuideCredential.id == credential_id,
            TourGuideCredential.tour_guide_id == guide.id,
        )
        .first()
    )
    if cred is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Credential not found")

    changes = payload.model_dump(exclude_unset=True)
    effective_issue = changes.get("issue_date", cred.issue_date)
    effective_expiry = changes.get("expiry_date", cred.expiry_date)
    if effective_issue and effective_expiry and effective_expiry < effective_issue:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="expiry_date must be on or after issue_date",
        )

    if changes:
        for field, value in changes.items():
            setattr(cred, field, value)
        cred.verification_status = "pending"
        db.flush()
        if guide.verification_status in ("unverified", "rejected"):
            guide.verification_status = "pending"
        elif guide.verification_status == "verified":
            other_approved = (
                db.query(TourGuideCredential.id)
                .filter(
                    TourGuideCredential.tour_guide_id == guide.id,
                    TourGuideCredential.id != cred.id,
                    TourGuideCredential.verification_status == "approved",
                )
                .first()
            )
            if other_approved is None:
                guide.verification_status = "pending"
        commit_changes(db)
        db.refresh(cred)

    return _serialize_credential(cred)


@router.put(
    "/credentials/{credential_id}/document",
    response_model=TourGuideCredentialResponse,
    summary="Replace a private credential document file (resets to pending verification)",
)
async def replace_guide_credential_document(
    credential_id: Annotated[int, Path(gt=0)],
    guide: OwnedGuide,
    db: DatabaseSession,
    file: UploadFile = File(...),
):
    cred = (
        db.query(TourGuideCredential)
        .filter(
            TourGuideCredential.id == credential_id,
            TourGuideCredential.tour_guide_id == guide.id,
        )
        .first()
    )
    if cred is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Credential not found")

    old_ref = cred.file_reference
    new_ref = await save_tour_guide_credential_file(guide.id, file)
    try:
        cred.file_reference = new_ref
        cred.is_confidential = True
        cred.verification_status = "pending"
        db.flush()
        if guide.verification_status in ("unverified", "rejected"):
            guide.verification_status = "pending"
        elif guide.verification_status == "verified":
            other_approved = (
                db.query(TourGuideCredential.id)
                .filter(
                    TourGuideCredential.tour_guide_id == guide.id,
                    TourGuideCredential.id != cred.id,
                    TourGuideCredential.verification_status == "approved",
                )
                .first()
            )
            if other_approved is None:
                guide.verification_status = "pending"
        commit_changes(db)
        db.refresh(cred)
    except Exception:
        delete_local_credential_file(new_ref)
        raise

    if old_ref and old_ref != new_ref:
        delete_local_credential_file(old_ref)
    return _serialize_credential(cred)


@router.delete(
    "/credentials/{credential_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an unreviewed credential from your profile",
)
def delete_guide_credential(
    credential_id: Annotated[int, Path(gt=0)],
    guide: OwnedGuide,
    db: DatabaseSession,
):
    cred = (
        db.query(TourGuideCredential)
        .filter(
            TourGuideCredential.id == credential_id,
            TourGuideCredential.tour_guide_id == guide.id,
        )
        .first()
    )
    if cred is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Credential not found")

    has_verifications = (
        db.query(TourGuideVerification.id)
        .filter(TourGuideVerification.credential_id == cred.id)
        .first()
        is not None
    )
    if has_verifications:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot delete a credential with verification review history; replace the document instead",
        )

    old_ref = cred.file_reference
    db.delete(cred)
    commit_changes(db)
    delete_local_credential_file(old_ref)


# =============================================================================
# 7. TOUR PACKAGES MANAGEMENT (PR-08 / PR-09 / GS-03 / GS-04 / GS-05)
# =============================================================================

@router.get(
    "/packages",
    response_model=list[TourGuidePackageResponse],
    summary="List your tour packages",
)
def list_guide_packages(
    guide: OwnedGuide,
    db: DatabaseSession,
    include_inactive: bool = True,
):
    query = db.query(TourGuidePackage).filter(
        TourGuidePackage.tour_guide_id == guide.id
    )
    if not include_inactive:
        query = query.filter(TourGuidePackage.is_active == True)
    return query.order_by(TourGuidePackage.is_active.desc(), TourGuidePackage.id.asc()).all()


@router.get(
    "/packages/{package_id}",
    response_model=TourGuidePackageResponse,
    summary="Get details of one of your tour packages",
)
def get_guide_package(
    package_id: Annotated[int, Path(gt=0)],
    guide: OwnedGuide,
    db: DatabaseSession,
):
    pkg = (
        db.query(TourGuidePackage)
        .filter(
            TourGuidePackage.id == package_id,
            TourGuidePackage.tour_guide_id == guide.id,
        )
        .first()
    )
    if pkg is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Package not found")
    return pkg


@router.post(
    "/packages",
    response_model=TourGuidePackageResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new half-day, full-day, or custom tour package",
)
def create_guide_package(
    payload: TourGuidePackageCreate,
    guide: OwnedGuide,
    db: DatabaseSession,
):
    pkg = TourGuidePackage(
        tour_guide_id=guide.id,
        **payload.model_dump(),
    )
    db.add(pkg)
    commit_changes(db)
    db.refresh(pkg)
    return pkg


@router.patch(
    "/packages/{package_id}",
    response_model=TourGuidePackageResponse,
    summary="Update one of your tour packages",
)
def update_guide_package(
    package_id: Annotated[int, Path(gt=0)],
    payload: TourGuidePackageUpdate,
    guide: OwnedGuide,
    db: DatabaseSession,
):
    pkg = (
        db.query(TourGuidePackage)
        .filter(
            TourGuidePackage.id == package_id,
            TourGuidePackage.tour_guide_id == guide.id,
        )
        .first()
    )
    if pkg is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Package not found")

    changes = payload.model_dump(exclude_unset=True)
    lat = changes.get("meeting_latitude", pkg.meeting_latitude)
    lon = changes.get("meeting_longitude", pkg.meeting_longitude)
    if (lat is None) != (lon is None):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="meeting_latitude and meeting_longitude must be provided together",
        )

    for field, value in changes.items():
        setattr(pkg, field, value)
    commit_changes(db)
    db.refresh(pkg)
    return pkg


@router.delete(
    "/packages/{package_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an unused package or deactivate a package with bookings",
)
def delete_guide_package(
    package_id: Annotated[int, Path(gt=0)],
    guide: OwnedGuide,
    db: DatabaseSession,
):
    pkg = (
        db.query(TourGuidePackage)
        .filter(
            TourGuidePackage.id == package_id,
            TourGuidePackage.tour_guide_id == guide.id,
        )
        .first()
    )
    if pkg is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Package not found")

    has_bookings = (
        db.query(TourGuideBooking.id)
        .filter(TourGuideBooking.package_id == pkg.id)
        .first()
        is not None
    )
    if has_bookings:
        pkg.is_active = False
    else:
        db.delete(pkg)
    commit_changes(db)


# =============================================================================
# 8. AVAILABILITY CALENDAR & OVERLAP VALIDATION (PR-10)
# =============================================================================

@router.get(
    "/availability",
    response_model=list[TourGuideAvailabilityResponse],
    summary="List your open and blocked availability calendar slots",
)
def list_guide_availability(
    guide: OwnedGuide,
    db: DatabaseSession,
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    is_available: bool | None = Query(default=None),
):
    if date_from and date_to and date_to < date_from:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="date_to must be on or after date_from",
        )

    query = db.query(TourGuideAvailability).filter(
        TourGuideAvailability.tour_guide_id == guide.id
    )
    if date_from is not None:
        query = query.filter(TourGuideAvailability.available_date >= date_from)
    if date_to is not None:
        query = query.filter(TourGuideAvailability.available_date <= date_to)
    if is_available is not None:
        query = query.filter(TourGuideAvailability.is_available == is_available)

    return query.order_by(
        TourGuideAvailability.available_date.asc(),
        TourGuideAvailability.start_time.asc(),
        TourGuideAvailability.id.asc(),
    ).all()


@router.post(
    "/availability",
    response_model=TourGuideAvailabilityResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add an open or blocked availability slot (rejects overlaps)",
)
def create_guide_availability(
    payload: TourGuideAvailabilityCreate,
    guide: OwnedGuide,
    db: DatabaseSession,
):
    overlap = find_overlapping_availability_slot(
        db,
        tour_guide_id=guide.id,
        available_date=payload.available_date,
        start_time=payload.start_time,
        end_time=payload.end_time,
    )
    if overlap is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Availability slot overlaps with existing slot #{overlap.id} "
                f"on {payload.available_date}"
            ),
        )

    if not payload.is_available:
        conflict_booking = find_conflicting_active_booking(
            db,
            tour_guide_id=guide.id,
            target_date=payload.available_date,
            start_time=payload.start_time,
            end_time=payload.end_time,
        )
        if conflict_booking is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Cannot block {payload.available_date}: conflicts with active "
                    f"booking #{conflict_booking.id}"
                ),
            )

    slot = TourGuideAvailability(
        tour_guide_id=guide.id,
        **payload.model_dump(),
    )
    db.add(slot)
    try:
        commit_changes(db)
    except IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Duplicate or conflicting availability slot",
        ) from exc
    db.refresh(slot)
    return slot


@router.post(
    "/availability/block-range",
    response_model=list[TourGuideAvailabilityResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Block a contiguous range of full days (atomic overlap & booking check)",
)
def block_guide_availability_range(
    payload: TourGuideAvailabilityBulkBlockRequest,
    guide: OwnedGuide,
    db: DatabaseSession,
):
    days_count = (payload.end_date - payload.start_date).days + 1
    dates = [payload.start_date + timedelta(days=offset) for offset in range(days_count)]

    for target_date in dates:
        overlap = find_overlapping_availability_slot(
            db,
            tour_guide_id=guide.id,
            available_date=target_date,
            start_time=None,
            end_time=None,
        )
        if overlap is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Date {target_date} overlaps with existing availability slot #{overlap.id}",
            )
        conflict_booking = find_conflicting_active_booking(
            db,
            tour_guide_id=guide.id,
            target_date=target_date,
            start_time=None,
            end_time=None,
        )
        if conflict_booking is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Date {target_date} conflicts with active booking #{conflict_booking.id}",
            )

    created_slots: list[TourGuideAvailability] = []
    for target_date in dates:
        slot = TourGuideAvailability(
            tour_guide_id=guide.id,
            available_date=target_date,
            start_time=None,
            end_time=None,
            is_available=False,
            reason=payload.reason,
        )
        db.add(slot)
        created_slots.append(slot)

    commit_changes(db)
    for slot in created_slots:
        db.refresh(slot)
    return created_slots


@router.patch(
    "/availability/{slot_id}",
    response_model=TourGuideAvailabilityResponse,
    summary="Update an availability slot with overlap and booking conflict checks",
)
def update_guide_availability(
    slot_id: Annotated[int, Path(gt=0)],
    payload: TourGuideAvailabilityUpdate,
    guide: OwnedGuide,
    db: DatabaseSession,
):
    slot = (
        db.query(TourGuideAvailability)
        .filter(
            TourGuideAvailability.id == slot_id,
            TourGuideAvailability.tour_guide_id == guide.id,
        )
        .first()
    )
    if slot is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Availability slot not found",
        )

    changes = payload.model_dump(exclude_unset=True)
    new_date = changes.get("available_date", slot.available_date)
    new_start = changes.get("start_time", slot.start_time)
    new_end = changes.get("end_time", slot.end_time)
    new_is_available = changes.get("is_available", slot.is_available)

    if (new_start is None) != (new_end is None):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="start_time and end_time must both be provided or both be null",
        )
    if new_start is not None and new_end is not None and new_end <= new_start:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="end_time must be strictly after start_time",
        )

    overlap = find_overlapping_availability_slot(
        db,
        tour_guide_id=guide.id,
        available_date=new_date,
        start_time=new_start,
        end_time=new_end,
        exclude_slot_id=slot.id,
    )
    if overlap is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Updated slot overlaps with existing slot #{overlap.id} on {new_date}",
        )

    if not new_is_available:
        conflict_booking = find_conflicting_active_booking(
            db,
            tour_guide_id=guide.id,
            target_date=new_date,
            start_time=new_start,
            end_time=new_end,
        )
        if conflict_booking is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Cannot block {new_date}: conflicts with active booking #{conflict_booking.id}",
            )

    for field, value in changes.items():
        setattr(slot, field, value)
    commit_changes(db)
    db.refresh(slot)
    return slot


@router.delete(
    "/availability/{slot_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove an availability calendar slot",
)
def delete_guide_availability(
    slot_id: Annotated[int, Path(gt=0)],
    guide: OwnedGuide,
    db: DatabaseSession,
):
    slot = (
        db.query(TourGuideAvailability)
        .filter(
            TourGuideAvailability.id == slot_id,
            TourGuideAvailability.tour_guide_id == guide.id,
        )
        .first()
    )
    if slot is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Availability slot not found",
        )

    db.delete(slot)
    commit_changes(db)


# =============================================================================
# 9. PROVIDER CONTACT & VERIFICATION STATUS (PR-17 / PR-18)
# =============================================================================

@router.get(
    "/contact",
    response_model=TourGuideContactResponse,
    summary="View permitted contact details for your tour guide profile",
)
def get_guide_contact(
    guide: OwnedGuide,
    provider: GuideProvider,
):
    return TourGuideContactResponse(
        tour_guide_id=guide.id,
        provider_profile_id=provider.id,
        business_name=provider.business_name,
        email=provider.user.email,
        phone=provider.phone,
        secondary_phone=provider.secondary_phone,
        base_city=guide.base_city,
        base_district=guide.base_district,
    )


@router.patch(
    "/contact",
    response_model=TourGuideContactResponse,
    summary="Update permitted phone contact details for your tour guide profile",
)
def update_guide_contact(
    payload: TourGuideContactUpdate,
    guide: OwnedGuide,
    provider: GuideProvider,
    db: DatabaseSession,
):
    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(provider, field, value)
    commit_changes(db)
    db.refresh(provider)
    return TourGuideContactResponse(
        tour_guide_id=guide.id,
        provider_profile_id=provider.id,
        business_name=provider.business_name,
        email=provider.user.email,
        phone=provider.phone,
        secondary_phone=provider.secondary_phone,
        base_city=guide.base_city,
        base_district=guide.base_district,
    )


@router.get(
    "/verification-status",
    response_model=TourGuideVerificationStatusResponse,
    summary="View approval status, verification badge state, credentials, and review history",
)
def get_guide_verification_status(
    guide: OwnedGuide,
    provider: GuideProvider,
    db: DatabaseSession,
):
    credentials = (
        db.query(TourGuideCredential)
        .filter(TourGuideCredential.tour_guide_id == guide.id)
        .order_by(TourGuideCredential.created_at.desc(), TourGuideCredential.id.desc())
        .all()
    )
    verifications = (
        db.query(TourGuideVerification)
        .filter(TourGuideVerification.tour_guide_id == guide.id)
        .order_by(TourGuideVerification.verified_at.desc(), TourGuideVerification.id.desc())
        .all()
    )
    summary = {"pending": 0, "approved": 0, "rejected": 0}
    for cred in credentials:
        if cred.verification_status in summary:
            summary[cred.verification_status] += 1

    return TourGuideVerificationStatusResponse(
        tour_guide_id=guide.id,
        provider_approval_status=provider.approval_status,
        guide_approval_status=guide.approval_status,
        verification_status=guide.verification_status,
        is_verified_badge=guide.verification_status == "verified",
        is_active=guide.is_active,
        reviewed_at=guide.reviewed_at,
        rejection_reason=guide.rejection_reason,
        years_of_experience=guide.years_of_experience,
        experience_verified=guide.verification_status == "verified" and summary["approved"] > 0,
        credentials_summary=summary,
        credentials=[_serialize_credential(c) for c in credentials],
        verification_history=[
            TourGuideVerificationRecordResponse.model_validate(v) for v in verifications
        ],
    )
