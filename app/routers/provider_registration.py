import io
import logging
import os
import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from PIL import Image
from sqlalchemy.orm import Session

from app.core.dependencies import require_registration_authorization
from app.core.rate_limiter import (
    provider_identity_upload_limiter,
    provider_registration_limiter,
    provider_submit_limiter,
)
from app.database.connection import get_db
from app.models.provider_profile import ProviderProfile
from app.models.provider_registration_authorization import (
    ProviderRegistrationAuthorization,
)
from app.models.provider_registration_draft import ProviderRegistrationDraft
from app.models.user import User
from app.schemas.provider_registration import (
    BusinessDetailsRequest,
    DraftResponse,
    PersonalDetailsRequest,
    RegistrationSubmitResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/providers/registration",
    tags=["Provider Registration"],
)

MAX_IMAGE_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB
ALLOWED_IMAGE_FORMATS = {"JPEG", "PNG", "WEBP"}


def rate_limit_registration(request: Request) -> None:
    provider_registration_limiter.check(request)


def rate_limit_identity_upload(request: Request) -> None:
    provider_identity_upload_limiter.check(request)


def rate_limit_submit(request: Request) -> None:
    provider_submit_limiter.check(request)


def get_upload_directory() -> str:
    base_dir = os.getenv(
        "PROVIDER_UPLOAD_DIR",
        os.path.join(os.getcwd(), "uploads", "identity_documents"),
    )
    os.makedirs(base_dir, exist_ok=True)
    return base_dir


def compute_draft_response(draft: ProviderRegistrationDraft) -> DraftResponse:
    personal_completed = bool(
        draft.full_name
        and draft.primary_phone
        and draft.nic_number
        and draft.date_of_birth
        and draft.gender
        and draft.nationality
        and draft.permanent_province
        and draft.permanent_district
        and draft.permanent_city
        and draft.permanent_landmark
        and draft.permanent_postal_code
    )
    business_completed = bool(
        draft.business_name
        and draft.provider_category
        and draft.business_province
        and draft.business_district
        and draft.business_city
        and draft.business_postal_code
        and draft.business_description
    )
    is_passport = (draft.identity_document_type == "passport")
    if is_passport:
        identity_completed = bool(
            draft.selfie_file_reference
            and draft.nic_front_file_reference
        )
    else:
        identity_completed = bool(
            draft.selfie_file_reference
            and draft.nic_front_file_reference
            and draft.nic_back_file_reference
        )

    if not personal_completed:
        current_step = "personal"
    elif not business_completed:
        current_step = "business"
    elif not identity_completed:
        current_step = "identity"
    else:
        current_step = "review"

    return DraftResponse(
        email=draft.email,
        full_name=draft.full_name,
        phone=draft.primary_phone,
        identity_document_type=draft.identity_document_type or "nic",
        nic_number=draft.nic_number,
        date_of_birth=draft.date_of_birth,
        gender=draft.gender,
        nationality=draft.nationality,
        permanent_province=draft.permanent_province,
        permanent_district=draft.permanent_district,
        permanent_city=draft.permanent_city,
        permanent_landmark=draft.permanent_landmark,
        permanent_postal_code=draft.permanent_postal_code,
        business_name=draft.business_name,
        provider_type=draft.provider_category,
        secondary_phone=draft.secondary_phone,
        business_province=draft.business_province,
        business_district=draft.business_district,
        business_city=draft.business_city,
        business_landmark=draft.business_landmark,
        business_postal_code=draft.business_postal_code,
        business_registration_number=draft.business_registration_number,
        description=draft.business_description,
        selfie_uploaded=bool(draft.selfie_file_reference),
        nic_front_uploaded=bool(draft.nic_front_file_reference),
        nic_back_uploaded=bool(draft.nic_back_file_reference),
        personal_completed=personal_completed,
        business_completed=business_completed,
        identity_completed=identity_completed,
        current_step=current_step,
        updated_at=draft.updated_at,
    )


def get_or_create_draft(email: str, db: Session) -> ProviderRegistrationDraft:
    draft = (
        db.query(ProviderRegistrationDraft)
        .filter(ProviderRegistrationDraft.email == email)
        .first()
    )
    if not draft:
        draft = ProviderRegistrationDraft(email=email, registration_step="personal")
        db.add(draft)
        db.flush()
    return draft


def validate_and_save_image(
    file: UploadFile,
    doc_type: str,
    old_file_ref: str | None,
) -> str:
    """
    Validate image file size, content integrity (via PIL), format, and store safely.
    Deletes superseded files safely. Never exposes raw server paths.
    """
    try:
        content = file.file.read()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Could not read {doc_type} image file.",
        ) from exc

    if len(content) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{doc_type} file is empty.",
        )

    if len(content) > MAX_IMAGE_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{doc_type} exceeds maximum allowed size of 5 MB.",
        )

    # Validate image format and content integrity
    try:
        with Image.open(io.BytesIO(content)) as img:
            img.verify()
            fmt = img.format
            if fmt not in ALLOWED_IMAGE_FORMATS:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"{doc_type} must be a JPEG, PNG, or WebP image. Detected: {fmt}.",
                )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid or corrupted image content for {doc_type}.",
        ) from exc

    upload_dir = get_upload_directory()
    ext = ".jpg" if fmt == "JPEG" else f".{fmt.lower()}"
    safe_filename = f"{doc_type}_{uuid.uuid4().hex}{ext}"
    target_path = os.path.abspath(os.path.join(upload_dir, safe_filename))

    # Path traversal guard
    if not target_path.startswith(os.path.abspath(upload_dir)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid file path detected.",
        )

    # Persist file
    with open(target_path, "wb") as f:
        f.write(content)

    # Safely remove superseded file if it exists
    if old_file_ref:
        clean_old_filename = os.path.basename(old_file_ref)
        old_path = os.path.abspath(os.path.join(upload_dir, clean_old_filename))
        if old_path.startswith(os.path.abspath(upload_dir)) and os.path.isfile(old_path):
            try:
                os.remove(old_path)
            except OSError as err:
                logger.warning("Could not delete superseded file %s: %s", old_path, err)

    return safe_filename


@router.put(
    "/personal",
    response_model=DraftResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(rate_limit_registration)],
    summary="Save provider personal details in registration draft",
)
def save_personal_details(
    payload: PersonalDetailsRequest,
    auth_record: Annotated[
        ProviderRegistrationAuthorization,
        Depends(require_registration_authorization),
    ],
    db: Annotated[Session, Depends(get_db)],
):
    email = auth_record.email

    doc_label = "Passport" if payload.identity_document_type == "passport" else "NIC"

    # Check for duplicate identity document in existing provider profiles
    existing_profile_query = db.query(ProviderProfile).filter(
        ProviderProfile.nic_number == payload.nic_number,
        ProviderProfile.identity_document_type == payload.identity_document_type,
    )
    if auth_record.purpose == "resubmission":
        user = db.query(User).filter_by(email=email).first()
        if user:
            existing_profile_query = existing_profile_query.filter(
                ProviderProfile.user_id != user.id
            )

    if existing_profile_query.first():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"This {doc_label} number is already associated with another provider account.",
        )

    # Check for duplicate identity document in other active drafts
    other_draft = (
        db.query(ProviderRegistrationDraft)
        .filter(
            ProviderRegistrationDraft.nic_number == payload.nic_number,
            ProviderRegistrationDraft.identity_document_type == payload.identity_document_type,
            ProviderRegistrationDraft.email != email,
        )
        .first()
    )
    if other_draft:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"This {doc_label} number is currently used in another pending registration draft.",
        )

    draft = get_or_create_draft(email, db)
    draft.full_name = payload.full_name
    draft.primary_phone = payload.phone
    draft.identity_document_type = payload.identity_document_type
    draft.nic_number = payload.nic_number
    draft.date_of_birth = payload.date_of_birth
    draft.gender = payload.gender
    draft.nationality = payload.nationality
    draft.permanent_province = payload.permanent_province
    draft.permanent_district = payload.permanent_district
    draft.permanent_city = payload.permanent_city
    draft.permanent_landmark = payload.permanent_landmark
    draft.permanent_postal_code = payload.permanent_postal_code
    draft.registration_step = "business"
    draft.updated_at = datetime.now(UTC)

    db.commit()
    db.refresh(draft)
    return compute_draft_response(draft)


@router.put(
    "/business",
    response_model=DraftResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(rate_limit_registration)],
    summary="Save provider business details in registration draft",
)
def save_business_details(
    payload: BusinessDetailsRequest,
    auth_record: Annotated[
        ProviderRegistrationAuthorization,
        Depends(require_registration_authorization),
    ],
    db: Annotated[Session, Depends(get_db)],
):
    email = auth_record.email
    draft = get_or_create_draft(email, db)

    # Determine business address
    if payload.same_as_permanent:
        b_province = payload.business_province or draft.permanent_province
        b_district = payload.business_district or draft.permanent_district
        b_city = payload.business_city or draft.permanent_city
        b_landmark = payload.business_landmark or draft.permanent_landmark
        b_postal = payload.business_postal_code or draft.permanent_postal_code
    else:
        b_province = payload.business_province
        b_district = payload.business_district
        b_city = payload.business_city
        b_landmark = payload.business_landmark
        b_postal = payload.business_postal_code

    draft.business_name = payload.business_name
    draft.provider_category = payload.provider_type
    draft.secondary_phone = payload.secondary_phone
    draft.business_province = b_province
    draft.business_district = b_district
    draft.business_city = b_city
    draft.business_landmark = b_landmark
    draft.business_postal_code = b_postal
    draft.business_registration_number = payload.business_registration_number
    draft.business_description = payload.description
    draft.registration_step = "identity"
    draft.updated_at = datetime.now(UTC)

    db.commit()
    db.refresh(draft)
    return compute_draft_response(draft)


@router.post(
    "/identity",
    response_model=DraftResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(rate_limit_identity_upload)],
    summary="Upload identity verification documents (selfie, NIC front, NIC back)",
)
def upload_identity_documents(
    auth_record: Annotated[
        ProviderRegistrationAuthorization,
        Depends(require_registration_authorization),
    ],
    db: Annotated[Session, Depends(get_db)],
    selfie: Annotated[UploadFile | None, File()] = None,
    nic_front: Annotated[UploadFile | None, File()] = None,
    nic_back: Annotated[UploadFile | None, File()] = None,
):
    if not selfie and not nic_front and not nic_back:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one identity document (selfie, nic_front, or nic_back) must be provided.",
        )

    email = auth_record.email
    draft = get_or_create_draft(email, db)

    if selfie:
        draft.selfie_file_reference = validate_and_save_image(
            selfie, "selfie", draft.selfie_file_reference
        )

    if nic_front:
        draft.nic_front_file_reference = validate_and_save_image(
            nic_front, "nic_front", draft.nic_front_file_reference
        )

    if nic_back:
        draft.nic_back_file_reference = validate_and_save_image(
            nic_back, "nic_back", draft.nic_back_file_reference
        )

    draft.registration_step = "review"
    draft.updated_at = datetime.now(UTC)

    db.commit()
    db.refresh(draft)
    return compute_draft_response(draft)


@router.get(
    "/draft",
    response_model=DraftResponse,
    status_code=status.HTTP_200_OK,
    summary="Retrieve current provider registration draft progress",
)
def get_registration_draft(
    auth_record: Annotated[
        ProviderRegistrationAuthorization,
        Depends(require_registration_authorization),
    ],
    db: Annotated[Session, Depends(get_db)],
):
    email = auth_record.email
    draft = (
        db.query(ProviderRegistrationDraft)
        .filter(ProviderRegistrationDraft.email == email)
        .first()
    )

    if not draft:
        return DraftResponse(
            email=email,
            current_step="personal",
            personal_completed=False,
            business_completed=False,
            identity_completed=False,
        )

    return compute_draft_response(draft)


@router.post(
    "/submit",
    response_model=RegistrationSubmitResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(rate_limit_submit)],
    summary="Submit provider registration draft for administrative review",
)
def submit_provider_registration(
    auth_record: Annotated[
        ProviderRegistrationAuthorization,
        Depends(require_registration_authorization),
    ],
    db: Annotated[Session, Depends(get_db)],
):
    email = auth_record.email
    draft = (
        db.query(ProviderRegistrationDraft)
        .filter(ProviderRegistrationDraft.email == email)
        .first()
    )

    if not draft:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No registration draft found. Please complete personal, business, and identity details.",
        )

    # 1. Comprehensive Validation of Required Fields
    # Personal details
    if not (
        draft.full_name
        and draft.primary_phone
        and draft.nic_number
        and draft.date_of_birth
        and draft.gender
        and draft.nationality
        and draft.permanent_province
        and draft.permanent_district
        and draft.permanent_city
        and draft.permanent_landmark
        and draft.permanent_postal_code
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Incomplete personal details. All personal fields, demographic information, and permanent residential address are required.",
        )

    # Business details
    if not (
        draft.business_name
        and draft.provider_category
        and draft.business_province
        and draft.business_district
        and draft.business_city
        and draft.business_postal_code
        and draft.business_description
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Incomplete business details. Business name, provider type, business address, and description are required.",
        )

    # Identity images
    is_passport = (draft.identity_document_type == "passport")
    if is_passport:
        if not (
            draft.selfie_file_reference
            and draft.nic_front_file_reference
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Incomplete identity verification. Selfie and passport photo page are required.",
            )
    else:
        if not (
            draft.selfie_file_reference
            and draft.nic_front_file_reference
            and draft.nic_back_file_reference
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Incomplete identity verification. All three documents (selfie, NIC front, and NIC back) are required.",
            )

    now = datetime.now(UTC)
    doc_type = draft.identity_document_type or "nic"
    doc_label = "Passport" if doc_type == "passport" else "NIC"

    # 2. Check Purpose & Apply Profile Changes
    if auth_record.purpose == "registration":
        # Ensure user does not already exist
        existing_user = db.query(User).filter_by(email=email).first()
        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="An account with this email already exists.",
            )

        # Ensure Identity Document is unique for this document type
        existing_profile = (
            db.query(ProviderProfile)
            .filter_by(
                nic_number=draft.nic_number,
                identity_document_type=doc_type,
            )
            .first()
        )
        if existing_profile:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"This {doc_label} number is already associated with another provider account.",
            )

        # Create User record (inactive, no password until approved)
        new_user = User(
            full_name=draft.full_name,
            email=draft.email,
            password_hash=None,
            role="provider",
            is_active=False,
        )
        db.add(new_user)
        db.flush()

        # Create ProviderProfile record
        new_profile = ProviderProfile(
            user_id=new_user.id,
            business_name=draft.business_name,
            provider_type=draft.provider_category,
            phone=draft.primary_phone,
            secondary_phone=draft.secondary_phone,
            district=draft.business_district or draft.permanent_district,
            description=draft.business_description,
            business_registration_number=draft.business_registration_number,
            identity_document_type=doc_type,
            nic_number=draft.nic_number,
            date_of_birth=draft.date_of_birth,
            gender=draft.gender,
            nationality=draft.nationality,
            permanent_province=draft.permanent_province,
            permanent_district=draft.permanent_district,
            permanent_city=draft.permanent_city,
            permanent_landmark=draft.permanent_landmark,
            permanent_postal_code=draft.permanent_postal_code,
            business_province=draft.business_province,
            business_district=draft.business_district,
            business_city=draft.business_city,
            business_landmark=draft.business_landmark,
            business_postal_code=draft.business_postal_code,
            selfie_image_path=draft.selfie_file_reference,
            nic_front_image_path=draft.nic_front_file_reference,
            nic_back_image_path=draft.nic_back_file_reference,
            approval_status="pending",
            reviewed_by=None,
            reviewed_at=None,
            rejection_reason=None,
        )
        db.add(new_profile)

    elif auth_record.purpose == "resubmission":
        existing_user = db.query(User).filter_by(email=email).first()
        if not existing_user or existing_user.role != "provider":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Provider account not found for resubmission.",
            )

        existing_profile = (
            db.query(ProviderProfile)
            .filter_by(user_id=existing_user.id)
            .first()
        )
        if not existing_profile:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Provider profile not found for resubmission.",
            )

        if existing_profile.approval_status != "rejected":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only rejected provider profiles can be resubmitted.",
            )

        # Check duplicate Identity Document among OTHER profiles
        other_profile = (
            db.query(ProviderProfile)
            .filter(
                ProviderProfile.nic_number == draft.nic_number,
                ProviderProfile.identity_document_type == doc_type,
                ProviderProfile.id != existing_profile.id,
            )
            .first()
        )
        if other_profile:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"This {doc_label} number is already associated with another provider account.",
            )

        # Update existing profile
        existing_profile.business_name = draft.business_name
        existing_profile.provider_type = draft.provider_category
        existing_profile.phone = draft.primary_phone
        existing_profile.secondary_phone = draft.secondary_phone
        existing_profile.district = draft.business_district or draft.permanent_district
        existing_profile.description = draft.business_description
        existing_profile.business_registration_number = draft.business_registration_number
        existing_profile.identity_document_type = doc_type
        existing_profile.nic_number = draft.nic_number
        existing_profile.date_of_birth = draft.date_of_birth
        existing_profile.gender = draft.gender
        existing_profile.nationality = draft.nationality
        existing_profile.permanent_province = draft.permanent_province
        existing_profile.permanent_district = draft.permanent_district
        existing_profile.permanent_city = draft.permanent_city
        existing_profile.permanent_landmark = draft.permanent_landmark
        existing_profile.permanent_postal_code = draft.permanent_postal_code
        existing_profile.business_province = draft.business_province
        existing_profile.business_district = draft.business_district
        existing_profile.business_city = draft.business_city
        existing_profile.business_landmark = draft.business_landmark
        existing_profile.business_postal_code = draft.business_postal_code
        existing_profile.selfie_image_path = draft.selfie_file_reference
        existing_profile.nic_front_image_path = draft.nic_front_file_reference
        existing_profile.nic_back_image_path = draft.nic_back_file_reference

        # Reset approval metadata
        existing_profile.approval_status = "pending"
        existing_profile.reviewed_by = None
        existing_profile.reviewed_at = None
        existing_profile.rejection_reason = None

        # Keep user inactive until re-approved
        existing_user.is_active = False

    # 3. Atomically consume the authorization token
    auth_record.is_consumed = True
    auth_record.consumed_at = now

    # 4. Clean up draft record
    db.delete(draft)

    db.commit()

    return RegistrationSubmitResponse(
        submitted=True,
        approval_status="pending",
        next_action="await_admin_review",
        message="Your application has been submitted for review.",
    )
