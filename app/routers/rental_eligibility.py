"""
FastAPI router for CeylonGo Rental Driving Eligibility & Licensing (Phase 5 Batch 1).
Handles:
- AR-01: Driving License Verification (traveler submission, status, admin moderation)
- AR-02: Minimum Age Requirements (trusted identity verification)
- AR-11: Tuk-Tuk License & Permit Verification (domestic vs. foreign IDP / AAC endorsement)
"""

from datetime import date, datetime, timezone
import math
import mimetypes
import os
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.core.audit import sanitize_audit_details
from app.core.dependencies import (
    is_admin_role,
    require_admin,
    require_operations_admin,
    require_operations_or_support_read,
    resolve_canonical_admin_role,
)
from app.core.notification_service import create_notification
from app.models.rental import RentalEligibilityCheck
from app.models.shared import AdminAuditLog
from app.models.user import User
from app.routers.auth import get_current_user
from app.schemas.rental_eligibility import (
    AdminEligibilityItem,
    AdminEligibilityPagination,
    AdminEligibilityReviewRequest,
    AdminEligibilityReviewResponse,
    RentalEligibilityResponse,
    RentalEligibilitySubmitRequest,
)

router = APIRouter(
    prefix="/api/v1/rental-eligibility",
    tags=["Rental Driving Eligibility & Licensing"],
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def mask_credential(value: str | None) -> str | None:
    if not value or len(value) <= 4:
        return "****" if value else None
    return f"{value[:2]}****{value[-4:]}"


def log_admin_audit(
    db: Session,
    admin_id: int,
    action: str,
    entity_name: str,
    entity_id: int | None,
    details: str,
    ip_address: str | None = None,
) -> None:
    audit = AdminAuditLog(
        admin_user_id=admin_id,
        action=action,
        entity_name=entity_name,
        entity_id=entity_id,
        details=sanitize_audit_details(details),
        ip_address=ip_address,
    )
    db.add(audit)


# ==============================================================================
# TRAVELER ELIGIBILITY ENDPOINTS (AR-01, AR-11)
# ==============================================================================

@router.post(
    "/license",
    response_model=RentalEligibilityResponse,
    status_code=status.HTTP_200_OK,
    summary="Submit or resubmit driving license credentials for motorized vehicle rentals",
)
def submit_driving_license(
    payload: RentalEligibilitySubmitRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Submits or updates traveler driving license credentials.
    Enforces Condition 10: Resubmission invalidates any prior approval, resetting
    verification_status to 'pending' until explicitly reviewed again.
    """
    today = date.today()
    if payload.license_expiry_date <= today:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Driving license has expired on {payload.license_expiry_date}. Must be valid beyond today.",
        )

    # Check for existing eligibility record
    eligibility = (
        db.query(RentalEligibilityCheck)
        .filter(RentalEligibilityCheck.user_id == current_user.id)
        .first()
    )

    if eligibility:
        # Resubmission resets approval to pending
        eligibility.driver_license_number = payload.driver_license_number.strip()
        eligibility.license_country = payload.license_country.strip()
        eligibility.license_expiry_date = payload.license_expiry_date
        if payload.license_front_image:
            eligibility.license_front_image = payload.license_front_image.strip()
        if payload.license_back_image:
            eligibility.license_back_image = payload.license_back_image.strip()
        if payload.idp_number is not None:
            eligibility.idp_number = payload.idp_number.strip() if payload.idp_number.strip() else None
        if payload.date_of_birth is not None:
            eligibility.date_of_birth = payload.date_of_birth
        if payload.recognition_permit_number is not None:
            eligibility.recognition_permit_number = payload.recognition_permit_number.strip() if payload.recognition_permit_number.strip() else None
        if payload.recognition_permit_document_url is not None:
            eligibility.recognition_permit_document_url = payload.recognition_permit_document_url.strip() if payload.recognition_permit_document_url.strip() else None

        eligibility.verification_status = "pending"
        eligibility.reviewed_by = None
        eligibility.reviewed_at = None
        eligibility.rejection_reason = None
    else:
        eligibility = RentalEligibilityCheck(
            user_id=current_user.id,
            driver_license_number=payload.driver_license_number.strip(),
            license_country=payload.license_country.strip(),
            license_expiry_date=payload.license_expiry_date,
            license_front_image=payload.license_front_image.strip() if payload.license_front_image else None,
            license_back_image=payload.license_back_image.strip() if payload.license_back_image else None,
            idp_number=payload.idp_number.strip() if (payload.idp_number and payload.idp_number.strip()) else None,
            date_of_birth=payload.date_of_birth,
            recognition_permit_number=payload.recognition_permit_number.strip() if (payload.recognition_permit_number and payload.recognition_permit_number.strip()) else None,
            recognition_permit_document_url=payload.recognition_permit_document_url.strip() if (payload.recognition_permit_document_url and payload.recognition_permit_document_url.strip()) else None,
            verification_status="pending",
        )
        db.add(eligibility)

    db.commit()
    db.refresh(eligibility)

    is_active_approved = eligibility.verification_status == "approved" and eligibility.license_expiry_date >= today
    is_foreign = eligibility.license_country.strip().lower() != "sri lanka"
    has_foreign_permit = bool(getattr(eligibility, "idp_number", None) or getattr(eligibility, "recognition_permit_number", None))
    tuktuk_eligible = is_active_approved and (not is_foreign or has_foreign_permit)

    dob_val = getattr(eligibility, "date_of_birth", None)
    dob_masked = f"{dob_val.year}-**-**" if dob_val else None
    rec_permit_val = getattr(eligibility, "recognition_permit_number", None)

    return RentalEligibilityResponse(
        id=eligibility.id,
        user_id=eligibility.user_id,
        driver_license_number_masked=mask_credential(eligibility.driver_license_number),
        license_country=eligibility.license_country,
        license_expiry_date=eligibility.license_expiry_date,
        date_of_birth_masked=dob_masked,
        has_idp=bool(eligibility.idp_number),
        idp_number_masked=mask_credential(eligibility.idp_number),
        has_recognition_permit=bool(rec_permit_val),
        recognition_permit_number_masked=mask_credential(rec_permit_val),
        verification_status=eligibility.verification_status,
        reviewed_at=eligibility.reviewed_at,
        rejection_reason=eligibility.rejection_reason,
        is_eligible_for_motorcycles=is_active_approved,
        is_eligible_for_tuktuks=tuktuk_eligible,
        is_eligible_for_bicycles=True,
        created_at=eligibility.created_at,
    )


@router.get(
    "/me",
    response_model=RentalEligibilityResponse,
    status_code=status.HTTP_200_OK,
    summary="Get authenticated traveler driving eligibility status and category permissions",
)
def get_my_driving_eligibility(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    eligibility = (
        db.query(RentalEligibilityCheck)
        .filter(RentalEligibilityCheck.user_id == current_user.id)
        .first()
    )

    today = date.today()
    if not eligibility:
        return RentalEligibilityResponse(
            id=0,
            user_id=current_user.id,
            driver_license_number_masked="Unregistered",
            license_country="None",
            license_expiry_date=today,
            has_idp=False,
            idp_number_masked=None,
            verification_status="unregistered",
            reviewed_at=None,
            rejection_reason=None,
            is_eligible_for_motorcycles=False,
            is_eligible_for_tuktuks=False,
            is_eligible_for_bicycles=True,
            created_at=utc_now(),
        )

    is_active_approved = eligibility.verification_status == "approved" and eligibility.license_expiry_date >= today
    is_foreign = eligibility.license_country.strip().lower() != "sri lanka"
    tuktuk_eligible = is_active_approved and (not is_foreign or bool(eligibility.idp_number))

    dob_val = getattr(eligibility, "date_of_birth", None)
    dob_masked = f"{dob_val.year}-**-**" if dob_val else None
    rec_permit_val = getattr(eligibility, "recognition_permit_number", None)

    return RentalEligibilityResponse(
        id=eligibility.id,
        user_id=eligibility.user_id,
        driver_license_number_masked=mask_credential(eligibility.driver_license_number),
        license_country=eligibility.license_country,
        license_expiry_date=eligibility.license_expiry_date,
        date_of_birth_masked=dob_masked,
        has_idp=bool(eligibility.idp_number),
        idp_number_masked=mask_credential(eligibility.idp_number),
        has_recognition_permit=bool(rec_permit_val),
        recognition_permit_number_masked=mask_credential(rec_permit_val),
        verification_status=eligibility.verification_status,
        reviewed_at=eligibility.reviewed_at,
        rejection_reason=eligibility.rejection_reason,
        is_eligible_for_motorcycles=is_active_approved,
        is_eligible_for_tuktuks=tuktuk_eligible,
        is_eligible_for_bicycles=True,
        created_at=eligibility.created_at,
    )


# ==============================================================================
# SECURE DOCUMENT ACCESS (CONDITION 9)
# ==============================================================================

@router.get(
    "/document/{filename}",
    summary="Securely view or stream uploaded driving license document with path traversal protection",
)
def get_license_document_file(
    filename: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    storage_base = os.getenv(
        "RENTAL_DOCUMENTS_DIR",
        os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "rental_docs"),
    )

    clean_filename = os.path.basename(filename)
    target_path = os.path.join(storage_base, clean_filename)

    real_base = os.path.realpath(storage_base)
    real_target = os.path.realpath(target_path)

    if not real_target.startswith(real_base):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid document path reference.",
        )

    # Access control: file must belong to user or requester must be admin
    eligibility = (
        db.query(RentalEligibilityCheck)
        .filter(
            or_(
                RentalEligibilityCheck.license_front_image.like(f"%{clean_filename}%"),
                RentalEligibilityCheck.license_back_image.like(f"%{clean_filename}%"),
            )
        )
        .first()
    )

    if not eligibility:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document record not found.",
        )

    is_owner = eligibility.user_id == current_user.id
    is_authorized_admin = (
        is_admin_role(current_user.role)
        and resolve_canonical_admin_role(current_user.role) in ("super_admin", "operations_admin")
    )
    if not is_owner and not is_authorized_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not authorized to access this driving credential.",
        )

    if not os.path.exists(real_target) or not os.path.isfile(real_target):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Physical document file not found.",
        )

    mime_type, _ = mimetypes.guess_type(real_target)
    headers = {
        "Cache-Control": "private, no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
        "Expires": "0",
        "Content-Disposition": f'inline; filename="{clean_filename}"',
    }

    return FileResponse(
        real_target,
        media_type=mime_type or "application/octet-stream",
        headers=headers,
    )


# ==============================================================================
# ADMIN ELIGIBILITY MODERATION (AR-01, AR-11)
# ==============================================================================

@router.get(
    "/admin/all",
    response_model=AdminEligibilityPagination,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_operations_or_support_read)],
    summary="Admin moderation queue for traveler driving eligibility submissions",
)
def list_admin_driving_eligibility(
    db: Annotated[Session, Depends(get_db)],
    verification_status: Annotated[str | None, Query(description="pending, approved, rejected, expired")] = None,
    country: Annotated[str | None, Query()] = None,
    user_id: Annotated[int | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
):
    query = (
        db.query(RentalEligibilityCheck, User)
        .join(User, RentalEligibilityCheck.user_id == User.id)
    )

    if verification_status:
        query = query.filter(func.lower(RentalEligibilityCheck.verification_status) == verification_status.strip().lower())
    if country:
        query = query.filter(func.lower(RentalEligibilityCheck.license_country) == country.strip().lower())
    if user_id:
        query = query.filter(RentalEligibilityCheck.user_id == user_id)

    total = query.count()
    total_pages = max(1, math.ceil(total / page_size)) if total > 0 else 0

    results = (
        query.order_by(RentalEligibilityCheck.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    items = [
        AdminEligibilityItem(
            id=el.id,
            user_id=el.user_id,
            traveler_name=u.full_name,
            traveler_email=u.email,
            driver_license_number=el.driver_license_number,
            license_country=el.license_country,
            license_expiry_date=el.license_expiry_date,
            date_of_birth=getattr(el, "date_of_birth", None),
            idp_number=el.idp_number,
            recognition_permit_number=getattr(el, "recognition_permit_number", None),
            recognition_permit_document_url=getattr(el, "recognition_permit_document_url", None),
            license_front_image=el.license_front_image,
            license_back_image=el.license_back_image,
            verification_status=el.verification_status,
            reviewed_by=el.reviewed_by,
            reviewed_at=el.reviewed_at,
            rejection_reason=el.rejection_reason,
            created_at=el.created_at,
        )
        for el, u in results
    ]

    return AdminEligibilityPagination(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.get(
    "/admin/pending",
    response_model=AdminEligibilityPagination,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_operations_or_support_read)],
    summary="Admin convenience queue for unreviewed driving eligibility submissions",
)
def list_pending_driving_eligibility(
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
):
    return list_admin_driving_eligibility(
        db=db,
        verification_status="pending",
        country=None,
        user_id=None,
        page=page,
        page_size=page_size,
    )


@router.post(
    "/admin/{eligibility_id}/review",
    response_model=AdminEligibilityReviewResponse,
    status_code=status.HTTP_200_OK,
    summary="Admin review, approval, or rejection of driver license credentials with audit logging",
)
def review_driving_eligibility(
    eligibility_id: int,
    payload: AdminEligibilityReviewRequest,
    admin_user: Annotated[User, Depends(require_operations_admin)],
    db: Annotated[Session, Depends(get_db)],
    request: Request,
):
    client_ip = request.client.host if request.client else None
    eligibility = (
        db.query(RentalEligibilityCheck)
        .filter(RentalEligibilityCheck.id == eligibility_id)
        .first()
    )
    if not eligibility:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Rental eligibility record #{eligibility_id} not found.",
        )

    prev_status = eligibility.verification_status

    if payload.decision == "rejected":
        # Condition 4: Mandatory rejection reason
        if not payload.rejection_reason or not payload.rejection_reason.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Rejection reason is mandatory when rejecting a driving license submission.",
            )
        eligibility.verification_status = "rejected"
        eligibility.rejection_reason = payload.rejection_reason.strip()
        msg = f"Driving license #{eligibility.id} rejected. Reason: {eligibility.rejection_reason}"
        action_name = "reject_driver_eligibility"

        create_notification(
            db=db,
            user_id=eligibility.user_id,
            title="Driving License Verification Rejected",
            message=f"Your driving license verification was rejected. Reason: {eligibility.rejection_reason}",
            notification_type="license_verification_rejected",
            link_url="/traveler/rental-eligibility",
        )

    elif payload.decision == "approved":
        today = date.today()
        if eligibility.license_expiry_date < today:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot approve an expired driving license.",
            )
        eligibility.verification_status = "approved"
        eligibility.rejection_reason = None
        if payload.verified_date_of_birth:
            eligibility.date_of_birth = payload.verified_date_of_birth
        if payload.recognition_permit_number:
            eligibility.recognition_permit_number = payload.recognition_permit_number.strip()
        msg = f"Driving license #{eligibility.id} approved successfully."
        action_name = "approve_driver_eligibility"

        create_notification(
            db=db,
            user_id=eligibility.user_id,
            title="Driving License Verification Approved",
            message="Your driving license has been verified and approved. You are now eligible to book motorized rental vehicles.",
            notification_type="license_verification_approved",
            link_url="/traveler/rental-eligibility",
        )

    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid decision '{payload.decision}'. Allowed: approved, rejected.",
        )

    eligibility.reviewed_by = admin_user.id
    eligibility.reviewed_at = utc_now()

    # Condition 5: Record in AdminAuditLog
    log_admin_audit(
        db=db,
        admin_id=admin_user.id,
        action=action_name,
        entity_name="rental_eligibility_checks",
        entity_id=eligibility.id,
        details=f"Admin {payload.decision} eligibility check #{eligibility.id} for user #{eligibility.user_id}. Reason: {payload.rejection_reason or 'None'}",
        ip_address=client_ip,
    )

    db.commit()
    db.refresh(eligibility)

    return AdminEligibilityReviewResponse(
        id=eligibility.id,
        user_id=eligibility.user_id,
        driver_license_number=eligibility.driver_license_number,
        license_country=eligibility.license_country,
        previous_status=prev_status,
        new_status=eligibility.verification_status,
        reviewed_by=admin_user.id,
        reviewed_at=eligibility.reviewed_at,
        rejection_reason=eligibility.rejection_reason,
        message=msg,
    )
