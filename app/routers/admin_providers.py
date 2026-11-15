import logging
import math
import mimetypes
import os
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from sqlalchemy import func, or_
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.dependencies import require_admin
from app.core.email import (
    send_provider_approval_email,
    send_provider_rejection_email,
)
from app.database.connection import get_db
from app.models.provider_profile import ProviderProfile
from app.models.user import User
from app.routers.provider_registration import get_upload_directory
from app.schemas.admin_providers import (
    ProviderDetailResponse,
    ProviderListItemResponse,
    ProviderListResponse,
    ProviderReviewActionResponse,
    RejectProviderRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/admin/providers",
    tags=["Admin Providers"],
    dependencies=[Depends(require_admin)],
)

ALLOWED_DOCUMENT_TYPES = {"selfie", "nic_front", "nic_back"}


@router.get(
    "",
    response_model=ProviderListResponse,
    status_code=status.HTTP_200_OK,
    summary="List provider applications with filtering and pagination",
)
def list_provider_applications(
    db: Annotated[Session, Depends(get_db)],
    status_filter: Annotated[
        str | None,
        Query(
            alias="status",
            description="Filter by approval status: pending, approved, rejected",
        ),
    ] = None,
    search: Annotated[
        str | None,
        Query(description="Search by provider name, email, or business name"),
    ] = None,
    page: Annotated[int, Query(ge=1, description="Page number")] = 1,
    limit: Annotated[
        int, Query(ge=1, le=100, description="Items per page")
    ] = 20,
):
    """
    List service provider applications with optional status filtering, search, and pagination.
    Orders newest applications first. Never returns passwords or private file paths.
    """
    query = db.query(ProviderProfile).join(User, ProviderProfile.user_id == User.id)

    if status_filter:
        clean_status = status_filter.strip().lower()
        if clean_status in ("pending", "approved", "rejected"):
            query = query.filter(ProviderProfile.approval_status == clean_status)

    if search:
        term = f"%{search.strip().lower()}%"
        query = query.filter(
            or_(
                func.lower(User.full_name).like(term),
                func.lower(User.email).like(term),
                func.lower(ProviderProfile.business_name).like(term),
            )
        )

    total = query.count()
    total_pages = max(1, math.ceil(total / limit))

    profiles = (
        query.order_by(ProviderProfile.created_at.desc())
        .offset((page - 1) * limit)
        .limit(limit)
        .all()
    )

    items = [
        ProviderListItemResponse(
            id=p.id,
            user_id=p.user_id,
            full_name=p.user.full_name,
            email=p.user.email,
            business_name=p.business_name,
            provider_type=p.provider_type,
            district=p.district,
            approval_status=p.approval_status,
            created_at=p.created_at,
        )
        for p in profiles
    ]

    return ProviderListResponse(
        items=items,
        total=total,
        page=page,
        limit=limit,
        total_pages=total_pages,
    )


@router.get(
    "/{provider_id}",
    response_model=ProviderDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Get full provider application details for administrative review",
)
def get_provider_application_details(
    provider_id: int,
    db: Annotated[Session, Depends(get_db)],
):
    """
    Retrieve comprehensive provider application details for review.
    Provides safe boolean indicators for identity documents instead of raw server file paths.
    """
    profile = (
        db.query(ProviderProfile)
        .filter(ProviderProfile.id == provider_id)
        .first()
    )

    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Provider application not found.",
        )

    reviewer_name = (
        profile.reviewer.full_name if profile.reviewer else None
    )

    return ProviderDetailResponse(
        id=profile.id,
        user_id=profile.user_id,
        full_name=profile.user.full_name,
        email=profile.user.email,
        role=profile.user.role,
        is_active=profile.user.is_active,
        phone=profile.phone,
        nic_number=profile.nic_number,
        permanent_province=profile.permanent_province,
        permanent_district=profile.permanent_district,
        permanent_city=profile.permanent_city,
        permanent_landmark=profile.permanent_landmark,
        permanent_postal_code=profile.permanent_postal_code,
        business_name=profile.business_name,
        provider_type=profile.provider_type,
        secondary_phone=profile.secondary_phone,
        district=profile.district,
        business_province=profile.business_province,
        business_district=profile.business_district,
        business_city=profile.business_city,
        business_landmark=profile.business_landmark,
        business_postal_code=profile.business_postal_code,
        business_registration_number=profile.business_registration_number,
        description=profile.description,
        approval_status=profile.approval_status,
        created_at=profile.created_at,
        reviewed_at=profile.reviewed_at,
        reviewed_by=profile.reviewed_by,
        reviewer_name=reviewer_name,
        rejection_reason=profile.rejection_reason,
        selfie_uploaded=bool(profile.selfie_image_path),
        nic_front_uploaded=bool(profile.nic_front_image_path),
        nic_back_uploaded=bool(profile.nic_back_image_path),
    )


@router.get(
    "/{provider_id}/documents/{document_type}",
    response_class=FileResponse,
    summary="Securely view an identity verification document",
)
def view_identity_document(
    provider_id: int,
    document_type: str,
    db: Annotated[Session, Depends(get_db)],
):
    """
    Securely stream an uploaded identity document (selfie, nic_front, nic_back) to authenticated Admins.
    Guards against path traversal, checks MIME types, sets anti-caching headers, and enforces allowlist.
    """
    clean_doc_type = document_type.strip().lower()
    if clean_doc_type not in ALLOWED_DOCUMENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid document type '{document_type}'. Allowed: selfie, nic_front, nic_back.",
        )

    profile = (
        db.query(ProviderProfile)
        .filter(ProviderProfile.id == provider_id)
        .first()
    )

    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Provider application not found.",
        )

    if clean_doc_type == "selfie":
        doc_ref = profile.selfie_image_path
    elif clean_doc_type == "nic_front":
        doc_ref = profile.nic_front_image_path
    else:
        doc_ref = profile.nic_back_image_path

    if not doc_ref:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Requested {clean_doc_type} document not found for this provider.",
        )

    upload_dir = get_upload_directory()
    clean_filename = os.path.basename(doc_ref)
    file_path = os.path.abspath(os.path.join(upload_dir, clean_filename))

    # Path traversal protection
    if not file_path.startswith(os.path.abspath(upload_dir)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid document path.",
        )

    if not os.path.exists(file_path) or not os.path.isfile(file_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document file not found on storage server.",
        )

    media_type, _ = mimetypes.guess_type(file_path)
    if not media_type:
        ext = os.path.splitext(file_path)[1].lower()
        if ext in (".jpg", ".jpeg"):
            media_type = "image/jpeg"
        elif ext == ".png":
            media_type = "image/png"
        elif ext == ".webp":
            media_type = "image/webp"
        else:
            media_type = "application/octet-stream"

    return FileResponse(
        path=file_path,
        media_type=media_type,
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post(
    "/{provider_id}/approve",
    response_model=ProviderReviewActionResponse,
    status_code=status.HTTP_200_OK,
    summary="Approve a pending provider application",
)
def approve_provider_application(
    provider_id: int,
    admin_user: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Approve a pending provider application.
    Enforces that documents are present, sets approval_status to 'approved',
    keeps login inactive and password null until Phase 2E password setup.
    Dispatches approval email notification truthfully.
    """
    query = db.query(ProviderProfile).filter(ProviderProfile.id == provider_id)
    try:
        profile = query.with_for_update().first()
    except SQLAlchemyError:
        profile = query.first()

    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Provider application not found.",
        )

    if profile.approval_status == "approved":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provider application is already approved.",
        )

    if profile.approval_status == "rejected":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot approve a rejected application directly. The provider must resubmit first.",
        )

    if profile.approval_status != "pending":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only pending provider applications can be approved.",
        )

    # Confirm required identity documents are present
    if not (
        profile.selfie_image_path
        and profile.nic_front_image_path
        and profile.nic_back_image_path
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot approve application: identity documents (selfie, NIC front, and NIC back) are missing.",
        )

    now = datetime.now(UTC)

    profile.approval_status = "approved"
    profile.reviewed_by = admin_user.id
    profile.reviewed_at = now
    profile.rejection_reason = None

    # Maintain strict onboarding security
    profile.user.password_hash = None
    profile.user.is_active = False

    db.commit()
    db.refresh(profile)

    # Dispatches email notification safely
    email_sent = send_provider_approval_email(
        to_email=profile.user.email,
        provider_name=profile.user.full_name,
        business_name=profile.business_name,
    )

    return ProviderReviewActionResponse(
        success=True,
        provider_id=profile.id,
        approval_status="approved",
        rejection_reason=None,
        message="Provider application has been successfully approved.",
        email_sent=email_sent,
    )


@router.post(
    "/{provider_id}/reject",
    response_model=ProviderReviewActionResponse,
    status_code=status.HTTP_200_OK,
    summary="Reject a pending provider application with a stated reason",
)
def reject_provider_application(
    provider_id: int,
    payload: RejectProviderRequest,
    admin_user: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Reject a pending provider application with a mandatory reason.
    Keeps provider account inactive and records rejection reason.
    Enables provider to resubmit via fresh OTP verification.
    """
    query = db.query(ProviderProfile).filter(ProviderProfile.id == provider_id)
    try:
        profile = query.with_for_update().first()
    except SQLAlchemyError:
        profile = query.first()

    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Provider application not found.",
        )

    if profile.approval_status == "rejected":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provider application is already rejected.",
        )

    if profile.approval_status == "approved":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot reject an already approved application.",
        )

    if profile.approval_status != "pending":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only pending provider applications can be rejected.",
        )

    now = datetime.now(UTC)

    profile.approval_status = "rejected"
    profile.rejection_reason = payload.reason
    profile.reviewed_by = admin_user.id
    profile.reviewed_at = now

    profile.user.is_active = False

    db.commit()
    db.refresh(profile)

    email_sent = send_provider_rejection_email(
        to_email=profile.user.email,
        provider_name=profile.user.full_name,
        business_name=profile.business_name,
        rejection_reason=payload.reason,
    )

    return ProviderReviewActionResponse(
        success=True,
        provider_id=profile.id,
        approval_status="rejected",
        rejection_reason=payload.reason,
        message="Provider application has been rejected.",
        email_sent=email_sent,
    )
