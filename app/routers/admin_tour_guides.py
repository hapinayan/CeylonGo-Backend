"""
Admin Router for Local Tour Guide Module 05 (AD-01, AD-02, AD-03, AD-04).

Endpoints under /api/v1/admin/tour-guides (require_admin):
- GET  ""                                  List tour guides with filters & pagination
- GET  "/credentials"                      List submitted guide credentials
- GET  "/credentials/{id}/document"        Securely stream confidential credential document
- POST "/credentials/{id}/verify"          Review & verify a guide credential
- GET  "/{guide_id}"                       Inspect complete tour guide profile & history
- POST "/{guide_id}/approve"               Approve a pending tour guide profile
- POST "/{guide_id}/reject"                Reject a pending tour guide profile with reason
- POST "/{guide_id}/suspend"               Suspend an active/inappropriate guide profile
- POST "/{guide_id}/reinstate"             Reinstate a suspended tour guide profile
- POST "/{guide_id}/verification-badge"    Grant or revoke Verified Guide Badge (evidence-gated)
"""

from datetime import UTC, date, datetime
import math
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status
from fastapi.responses import FileResponse
from sqlalchemy import func, or_
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, selectinload

from app.core.dependencies import (
    require_admin,
    require_operations_admin,
    require_operations_or_support_read,
)
from app.core.notification_service import create_notification
from app.database.connection import get_db
from app.models.provider_profile import ProviderProfile
from app.models.tour_guide import (
    TourGuide,
    TourGuideCredential,
    TourGuideVerification,
)
from app.models.user import User
from app.routers.tour_guide import (
    _serialize_credential,
    _stream_private_credential_file,
)
from app.schemas.tour_guide import (
    AdminBadgeUpdateRequest,
    AdminBadgeUpdateResponse,
    AdminCredentialListItem,
    AdminCredentialListResponse,
    AdminCredentialVerifyRequest,
    AdminCredentialVerifyResponse,
    AdminTourGuideActionResponse,
    AdminTourGuideBookingListResponse,
    AdminTourGuideDecisionRequest,
    AdminTourGuideDetailResponse,
    AdminTourGuideListItem,
    AdminTourGuideListResponse,
    TourGuideBookingDetailResponse,
    TourGuideImageResponse,
    TourGuideLanguageResponse,
    TourGuidePackageResponse,
    TourGuideResponse,
    TourGuideServiceAreaResponse,
    TourGuideVerificationRecordResponse,
)
from app.services.tour_guide_booking import (
    admin_get_tour_guide_booking_detail,
    admin_list_tour_guide_bookings,
)
from app.services.tour_guide_sync import (
    record_tour_guide_admin_audit,
    sync_tour_guide_listing_reference,
)

router = APIRouter(
    prefix="/api/v1/admin/tour-guides",
    tags=["Admin Tour Guides"],
    dependencies=[Depends(require_admin)],
)

AdminUser = Annotated[User, Depends(require_operations_admin)]
DatabaseSession = Annotated[Session, Depends(get_db)]


def _commit(db: Session) -> None:
    try:
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _guide_owner_user_id(guide: TourGuide) -> int | None:
    if guide.provider_profile is not None:
        return guide.provider_profile.user_id
    return None


def _require_guide(db: Session, guide_id: int) -> TourGuide:
    guide = (
        db.query(TourGuide)
        .options(
            selectinload(TourGuide.provider_profile).selectinload(ProviderProfile.user),
            selectinload(TourGuide.languages),
            selectinload(TourGuide.service_areas),
            selectinload(TourGuide.images),
            selectinload(TourGuide.packages),
            selectinload(TourGuide.credentials),
            selectinload(TourGuide.verifications),
        )
        .filter(TourGuide.id == guide_id)
        .first()
    )
    if guide is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tour guide profile not found",
        )
    return guide


# =============================================================================
# 1. LIST TOUR GUIDES (AD-01 / AD-04)
# =============================================================================

@router.get(
    "",
    response_model=AdminTourGuideListResponse,
    summary="List tour guide profiles for admin review and moderation",
    dependencies=[Depends(require_operations_admin)],
)
def admin_list_tour_guides(
    db: DatabaseSession,
    approval_status: Annotated[str | None, Query()] = None,
    verification_status: Annotated[str | None, Query()] = None,
    district: Annotated[str | None, Query()] = None,
    is_active: Annotated[bool | None, Query()] = None,
    search: Annotated[str | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
):
    query = (
        db.query(TourGuide)
        .join(ProviderProfile, TourGuide.provider_profile_id == ProviderProfile.id)
        .join(User, ProviderProfile.user_id == User.id)
        .options(
            selectinload(TourGuide.provider_profile).selectinload(ProviderProfile.user),
            selectinload(TourGuide.credentials),
        )
    )

    if approval_status:
        cleaned = approval_status.strip().lower()
        if cleaned in ("draft", "pending", "approved", "rejected", "suspended"):
            query = query.filter(TourGuide.approval_status == cleaned)

    if verification_status:
        cleaned = verification_status.strip().lower()
        if cleaned in ("unverified", "pending", "verified", "rejected", "suspended"):
            query = query.filter(TourGuide.verification_status == cleaned)

    if district:
        query = query.filter(func.lower(TourGuide.base_district) == district.strip().lower())

    if is_active is not None:
        query = query.filter(TourGuide.is_active == is_active)

    if search:
        term = f"%{search.strip().lower()}%"
        query = query.filter(
            or_(
                func.lower(ProviderProfile.business_name).like(term),
                func.lower(User.full_name).like(term),
                func.lower(User.email).like(term),
                func.lower(TourGuide.base_city).like(term),
                func.lower(TourGuide.base_district).like(term),
            )
        )

    total = query.count()
    total_pages = max(1, math.ceil(total / page_size))
    guides = (
        query.order_by(TourGuide.created_at.desc(), TourGuide.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    items = []
    for g in guides:
        pending_creds = sum(1 for c in g.credentials if c.verification_status == "pending")
        approved_creds = sum(1 for c in g.credentials if c.verification_status == "approved")
        items.append(
            AdminTourGuideListItem(
                id=g.id,
                provider_profile_id=g.provider_profile_id,
                business_name=g.provider_profile.business_name,
                provider_name=g.provider_profile.user.full_name,
                email=g.provider_profile.user.email,
                base_city=g.base_city,
                base_district=g.base_district,
                daily_rate=g.daily_rate,
                currency=g.currency,
                approval_status=g.approval_status,
                verification_status=g.verification_status,
                is_active=g.is_active,
                is_available=g.is_available,
                years_of_experience=g.years_of_experience,
                rating_average=g.rating_average,
                review_count=g.review_count,
                pending_credentials_count=pending_creds,
                approved_credentials_count=approved_creds,
                created_at=g.created_at,
            )
        )

    return AdminTourGuideListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


# =============================================================================
# 2. CREDENTIAL VERIFICATION QUEUE & SECURE DOCUMENT STREAMING (AD-02 / GS-02)
# =============================================================================

@router.get(
    "/credentials",
    response_model=AdminCredentialListResponse,
    summary="List tour guide qualification credentials for admin review",
    dependencies=[Depends(require_operations_admin)],
)
def admin_list_guide_credentials(
    db: DatabaseSession,
    verification_status: Annotated[str | None, Query()] = None,
    tour_guide_id: Annotated[int | None, Query(gt=0)] = None,
    credential_type: Annotated[str | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
):
    query = (
        db.query(TourGuideCredential)
        .join(TourGuide, TourGuideCredential.tour_guide_id == TourGuide.id)
        .join(ProviderProfile, TourGuide.provider_profile_id == ProviderProfile.id)
        .options(
            selectinload(TourGuideCredential.tour_guide).selectinload(
                TourGuide.provider_profile
            )
        )
    )

    if verification_status:
        cleaned = verification_status.strip().lower()
        if cleaned in ("pending", "approved", "rejected"):
            query = query.filter(TourGuideCredential.verification_status == cleaned)

    if tour_guide_id is not None:
        query = query.filter(TourGuideCredential.tour_guide_id == tour_guide_id)

    if credential_type:
        query = query.filter(TourGuideCredential.credential_type == credential_type.strip().lower())

    total = query.count()
    total_pages = max(1, math.ceil(total / page_size))
    rows = (
        query.order_by(TourGuideCredential.created_at.desc(), TourGuideCredential.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    items = [
        AdminCredentialListItem(
            credential=_serialize_credential(c),
            business_name=c.tour_guide.provider_profile.business_name,
            guide_approval_status=c.tour_guide.approval_status,
            guide_verification_status=c.tour_guide.verification_status,
        )
        for c in rows
    ]

    return AdminCredentialListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.get(
    "/credentials/{credential_id}/document",
    response_class=FileResponse,
    summary="Securely stream a private tour guide credential document to admin",
    dependencies=[Depends(require_operations_admin)],
)
def admin_view_credential_document(
    credential_id: Annotated[int, Path(gt=0)],
    db: DatabaseSession,
):
    cred = (
        db.query(TourGuideCredential)
        .filter(TourGuideCredential.id == credential_id)
        .first()
    )
    if cred is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tour guide credential not found",
        )
    return _stream_private_credential_file(cred)


@router.post(
    "/credentials/{credential_id}/verify",
    response_model=AdminCredentialVerifyResponse,
    summary="Review a guide credential and record a verification audit entry",
)
def admin_verify_guide_credential(
    credential_id: Annotated[int, Path(gt=0)],
    payload: AdminCredentialVerifyRequest,
    request: Request,
    admin: AdminUser,
    db: DatabaseSession,
):
    cred = (
        db.query(TourGuideCredential)
        .options(
            selectinload(TourGuideCredential.tour_guide).selectinload(
                TourGuide.provider_profile
            )
        )
        .filter(TourGuideCredential.id == credential_id)
        .first()
    )
    if cred is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tour guide credential not found",
        )

    guide = cred.tour_guide
    now_utc = datetime.now(UTC).replace(tzinfo=None)

    if payload.status == "approved":
        cred.verification_status = "approved"
    elif payload.status == "rejected":
        cred.verification_status = "rejected"
    else:
        # requires_info keeps credential pending until provider updates/re-uploads
        cred.verification_status = "pending"

    verification = TourGuideVerification(
        tour_guide_id=guide.id,
        credential_id=cred.id,
        reviewed_by=admin.id,
        verification_type=payload.verification_type.strip(),
        status=payload.status,
        notes=payload.notes.strip() if payload.notes else None,
        rejection_reason=payload.rejection_reason.strip() if payload.rejection_reason else None,
        verified_at=now_utc,
    )
    db.add(verification)
    db.flush()

    # Keep guide.verification_status consistent without auto-granting badge prematurely
    if guide.verification_status == "unverified":
        guide.verification_status = "pending"
    elif payload.status == "rejected" and guide.verification_status == "pending":
        remaining_non_rejected = (
            db.query(TourGuideCredential.id)
            .filter(
                TourGuideCredential.tour_guide_id == guide.id,
                TourGuideCredential.verification_status != "rejected",
            )
            .first()
        )
        if remaining_non_rejected is None:
            guide.verification_status = "rejected"

    record_tour_guide_admin_audit(
        db,
        admin_user_id=admin.id,
        action=f"tour_guide_credential_{payload.status}",
        entity_name="tour_guide_credentials",
        entity_id=cred.id,
        details={
            "tour_guide_id": guide.id,
            "verification_id": verification.id,
            "status": payload.status,
            "notes": verification.notes,
            "rejection_reason": verification.rejection_reason,
        },
        ip_address=_client_ip(request),
    )

    owner_user_id = _guide_owner_user_id(guide)
    if owner_user_id:
        detail_msg = (
            f"Your credential '{cred.title}' was marked '{payload.status}'."
        )
        if verification.rejection_reason:
            detail_msg += f" Reason: {verification.rejection_reason}"
        elif verification.notes:
            detail_msg += f" Notes: {verification.notes}"
        create_notification(
            db=db,
            user_id=owner_user_id,
            title=f"Credential Review: {payload.status.replace('_', ' ').title()}",
            message=detail_msg,
            notification_type=f"guide_credential_{payload.status}",
            link_url="/provider/tour-guides/verification-status",
        )

    _commit(db)
    db.refresh(cred)
    db.refresh(verification)

    return AdminCredentialVerifyResponse(
        credential=_serialize_credential(cred),
        verification=TourGuideVerificationRecordResponse.model_validate(verification),
        guide_verification_status=guide.verification_status,
        message=f"Credential review recorded with status '{payload.status}'.",
    )


# =============================================================================
# 2B. ADMIN BOOKING MONITORING & DISPUTE-SAFE OVERSIGHT (AD-05)
# =============================================================================

@router.get(
    "/bookings",
    response_model=AdminTourGuideBookingListResponse,
    summary="List and monitor all tour guide bookings and issues",
    dependencies=[Depends(require_operations_or_support_read)],
)
def admin_list_guide_bookings(
    db: DatabaseSession,
    booking_status: Annotated[str | None, Query()] = None,
    payment_method: Annotated[str | None, Query()] = None,
    payment_status: Annotated[str | None, Query()] = None,
    tour_guide_id: Annotated[int | None, Query(gt=0)] = None,
    traveler_id: Annotated[int | None, Query(gt=0)] = None,
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    issues_only: bool = Query(
        default=False,
        description="Filter cancelled, rejected, expired, refund_pending, or failed bookings",
    ),
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
):
    return admin_list_tour_guide_bookings(
        db,
        booking_status=booking_status,
        payment_method=payment_method,
        payment_status=payment_status,
        tour_guide_id=tour_guide_id,
        traveler_id=traveler_id,
        date_from=date_from,
        date_to=date_to,
        issues_only=issues_only,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/bookings/{booking_id}",
    response_model=TourGuideBookingDetailResponse,
    summary="Inspect a specific tour guide booking, policy snapshots, and full audit trail",
    dependencies=[Depends(require_operations_or_support_read)],
)
def admin_get_guide_booking_detail(
    booking_id: Annotated[int, Path(gt=0)],
    db: DatabaseSession,
):
    return admin_get_tour_guide_booking_detail(db, booking_id=booking_id)


# =============================================================================
# 3. ADMIN GUIDE DETAIL, APPROVAL, MODERATION & BADGE (AD-01 / AD-03 / AD-04)
# =============================================================================

@router.get(
    "/{guide_id}",
    response_model=AdminTourGuideDetailResponse,
    summary="Inspect complete tour guide profile, packages, credentials, and verification history",
    dependencies=[Depends(require_operations_admin)],
)
def admin_get_tour_guide_detail(
    guide_id: Annotated[int, Path(gt=0)],
    db: DatabaseSession,
):
    guide = _require_guide(db, guide_id)
    provider = guide.provider_profile
    return AdminTourGuideDetailResponse(
        guide=TourGuideResponse.model_validate(guide),
        business_name=provider.business_name,
        provider_name=provider.user.full_name,
        email=provider.user.email,
        phone=provider.phone,
        secondary_phone=provider.secondary_phone,
        provider_approval_status=provider.approval_status,
        languages=[
            TourGuideLanguageResponse.model_validate(item) for item in guide.languages
        ],
        service_areas=[
            TourGuideServiceAreaResponse.model_validate(item) for item in guide.service_areas
        ],
        images=[TourGuideImageResponse.model_validate(item) for item in guide.images],
        packages=[
            TourGuidePackageResponse.model_validate(item) for item in guide.packages
        ],
        credentials=[_serialize_credential(c) for c in guide.credentials],
        verification_history=[
            TourGuideVerificationRecordResponse.model_validate(v)
            for v in sorted(guide.verifications, key=lambda r: (r.verified_at, r.id), reverse=True)
        ],
    )


@router.post(
    "/{guide_id}/approve",
    response_model=AdminTourGuideActionResponse,
    summary="Approve a pending tour guide profile and activate public discovery",
)
def admin_approve_tour_guide(
    guide_id: Annotated[int, Path(gt=0)],
    request: Request,
    admin: AdminUser,
    db: DatabaseSession,
    payload: AdminTourGuideDecisionRequest | None = None,
):
    guide = _require_guide(db, guide_id)

    if guide.approval_status != "pending":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Only pending tour guide profiles can be approved (current: '{guide.approval_status}')",
        )

    now_utc = datetime.now(UTC).replace(tzinfo=None)
    guide.approval_status = "approved"
    guide.is_active = True
    guide.reviewed_by = admin.id
    guide.reviewed_at = now_utc
    guide.rejection_reason = None

    listing_ref = sync_tour_guide_listing_reference(db, guide)

    record_tour_guide_admin_audit(
        db,
        admin_user_id=admin.id,
        action="tour_guide_approved",
        entity_name="tour_guides",
        entity_id=guide.id,
        details={"notes": payload.reason if payload else None},
        ip_address=_client_ip(request),
    )

    owner_user_id = _guide_owner_user_id(guide)
    if owner_user_id:
        create_notification(
            db=db,
            user_id=owner_user_id,
            title="Tour Guide Profile Approved",
            message="Your Local Tour Guide profile has been approved and is now active for traveler discovery.",
            notification_type="guide_profile_approved",
            link_url="/provider/tour-guides",
        )

    _commit(db)
    db.refresh(guide)

    return AdminTourGuideActionResponse(
        success=True,
        tour_guide_id=guide.id,
        approval_status=guide.approval_status,
        verification_status=guide.verification_status,
        is_active=guide.is_active,
        rejection_reason=guide.rejection_reason,
        listing_reference_synced=listing_ref is not None,
        message="Tour guide profile approved and activated.",
    )


@router.post(
    "/{guide_id}/reject",
    response_model=AdminTourGuideActionResponse,
    summary="Reject a pending tour guide profile with a mandatory reason",
)
def admin_reject_tour_guide(
    guide_id: Annotated[int, Path(gt=0)],
    payload: AdminTourGuideDecisionRequest,
    request: Request,
    admin: AdminUser,
    db: DatabaseSession,
):
    if not payload.reason or len(payload.reason.strip()) < 5:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="A rejection reason of at least 5 characters is required",
        )

    guide = _require_guide(db, guide_id)
    if guide.approval_status != "pending":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Only pending tour guide profiles can be rejected (current: '{guide.approval_status}')",
        )

    reason = payload.reason.strip()
    now_utc = datetime.now(UTC).replace(tzinfo=None)
    guide.approval_status = "rejected"
    guide.is_active = False
    guide.reviewed_by = admin.id
    guide.reviewed_at = now_utc
    guide.rejection_reason = reason

    listing_ref = sync_tour_guide_listing_reference(db, guide)

    record_tour_guide_admin_audit(
        db,
        admin_user_id=admin.id,
        action="tour_guide_rejected",
        entity_name="tour_guides",
        entity_id=guide.id,
        details={"reason": reason},
        ip_address=_client_ip(request),
    )

    owner_user_id = _guide_owner_user_id(guide)
    if owner_user_id:
        create_notification(
            db=db,
            user_id=owner_user_id,
            title="Tour Guide Profile Rejected",
            message=f"Your Local Tour Guide profile was rejected. Reason: {reason}",
            notification_type="guide_profile_rejected",
            link_url="/provider/tour-guides/verification-status",
        )

    _commit(db)
    db.refresh(guide)

    return AdminTourGuideActionResponse(
        success=True,
        tour_guide_id=guide.id,
        approval_status=guide.approval_status,
        verification_status=guide.verification_status,
        is_active=guide.is_active,
        rejection_reason=guide.rejection_reason,
        listing_reference_synced=listing_ref is not None,
        message="Tour guide profile rejected.",
    )


@router.post(
    "/{guide_id}/suspend",
    response_model=AdminTourGuideActionResponse,
    summary="Suspend an inappropriate or policy-violating tour guide profile",
)
def admin_suspend_tour_guide(
    guide_id: Annotated[int, Path(gt=0)],
    payload: AdminTourGuideDecisionRequest,
    request: Request,
    admin: AdminUser,
    db: DatabaseSession,
):
    if not payload.reason or len(payload.reason.strip()) < 5:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="A suspension reason of at least 5 characters is required",
        )

    guide = _require_guide(db, guide_id)
    if guide.approval_status == "suspended":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Tour guide profile is already suspended",
        )

    reason = payload.reason.strip()
    now_utc = datetime.now(UTC).replace(tzinfo=None)
    previous_status = guide.approval_status
    guide.approval_status = "suspended"
    guide.is_active = False
    guide.reviewed_by = admin.id
    guide.reviewed_at = now_utc
    guide.rejection_reason = reason

    listing_ref = sync_tour_guide_listing_reference(db, guide)

    record_tour_guide_admin_audit(
        db,
        admin_user_id=admin.id,
        action="tour_guide_suspended",
        entity_name="tour_guides",
        entity_id=guide.id,
        details={"previous_status": previous_status, "reason": reason},
        ip_address=_client_ip(request),
    )

    owner_user_id = _guide_owner_user_id(guide)
    if owner_user_id:
        create_notification(
            db=db,
            user_id=owner_user_id,
            title="Tour Guide Profile Suspended",
            message=f"Your Local Tour Guide profile has been suspended. Reason: {reason}",
            notification_type="guide_profile_suspended",
            link_url="/provider/tour-guides/verification-status",
        )

    _commit(db)
    db.refresh(guide)

    return AdminTourGuideActionResponse(
        success=True,
        tour_guide_id=guide.id,
        approval_status=guide.approval_status,
        verification_status=guide.verification_status,
        is_active=guide.is_active,
        rejection_reason=guide.rejection_reason,
        listing_reference_synced=listing_ref is not None,
        message="Tour guide profile suspended and removed from public discovery.",
    )


@router.post(
    "/{guide_id}/reinstate",
    response_model=AdminTourGuideActionResponse,
    summary="Reinstate a suspended tour guide profile",
)
def admin_reinstate_tour_guide(
    guide_id: Annotated[int, Path(gt=0)],
    request: Request,
    admin: AdminUser,
    db: DatabaseSession,
    payload: AdminTourGuideDecisionRequest | None = None,
):
    guide = _require_guide(db, guide_id)
    if guide.approval_status != "suspended":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Only suspended tour guide profiles can be reinstated (current: '{guide.approval_status}')",
        )

    now_utc = datetime.now(UTC).replace(tzinfo=None)
    guide.approval_status = "approved"
    guide.is_active = True
    guide.reviewed_by = admin.id
    guide.reviewed_at = now_utc
    guide.rejection_reason = None

    listing_ref = sync_tour_guide_listing_reference(db, guide)

    record_tour_guide_admin_audit(
        db,
        admin_user_id=admin.id,
        action="tour_guide_reinstated",
        entity_name="tour_guides",
        entity_id=guide.id,
        details={"notes": payload.reason if payload else None},
        ip_address=_client_ip(request),
    )

    owner_user_id = _guide_owner_user_id(guide)
    if owner_user_id:
        create_notification(
            db=db,
            user_id=owner_user_id,
            title="Tour Guide Profile Reinstated",
            message="Your Local Tour Guide profile has been reinstated and is active again.",
            notification_type="guide_profile_reinstated",
            link_url="/provider/tour-guides",
        )

    _commit(db)
    db.refresh(guide)

    return AdminTourGuideActionResponse(
        success=True,
        tour_guide_id=guide.id,
        approval_status=guide.approval_status,
        verification_status=guide.verification_status,
        is_active=guide.is_active,
        rejection_reason=guide.rejection_reason,
        listing_reference_synced=listing_ref is not None,
        message="Tour guide profile reinstated.",
    )


@router.post(
    "/{guide_id}/verification-badge",
    response_model=AdminBadgeUpdateResponse,
    summary="Grant or revoke the Verified Guide Badge after credential evidence review",
)
def admin_update_verification_badge(
    guide_id: Annotated[int, Path(gt=0)],
    payload: AdminBadgeUpdateRequest,
    request: Request,
    admin: AdminUser,
    db: DatabaseSession,
):
    guide = _require_guide(db, guide_id)

    if payload.verification_status == "verified":
        # Spec Section 11: Verification badges require reviewed credential evidence
        approved_evidence = (
            db.query(TourGuideVerification.id)
            .join(
                TourGuideCredential,
                (TourGuideVerification.credential_id == TourGuideCredential.id)
                & (TourGuideVerification.tour_guide_id == TourGuideCredential.tour_guide_id),
            )
            .filter(
                TourGuideVerification.tour_guide_id == guide.id,
                TourGuideVerification.status == "approved",
                TourGuideCredential.verification_status == "approved",
            )
            .first()
        )
        if approved_evidence is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Cannot grant Verified Guide Badge without at least one approved "
                    "credential backed by an admin verification review."
                ),
            )

    previous_verification = guide.verification_status
    guide.verification_status = payload.verification_status

    record_tour_guide_admin_audit(
        db,
        admin_user_id=admin.id,
        action=f"tour_guide_badge_{payload.verification_status}",
        entity_name="tour_guides",
        entity_id=guide.id,
        details={
            "previous_verification_status": previous_verification,
            "new_verification_status": payload.verification_status,
            "reason": payload.reason,
        },
        ip_address=_client_ip(request),
    )

    owner_user_id = _guide_owner_user_id(guide)
    if owner_user_id:
        if payload.verification_status == "verified":
            title = "Verified Guide Badge Granted"
            msg = "Congratulations! Your Local Tour Guide profile has been granted the Verified Guide Badge."
        else:
            title = f"Verification Status Updated: {payload.verification_status.title()}"
            msg = (
                f"Your guide verification status was updated to '{payload.verification_status}'. "
                f"Reason: {payload.reason}"
            )
        create_notification(
            db=db,
            user_id=owner_user_id,
            title=title,
            message=msg,
            notification_type=f"guide_badge_{payload.verification_status}",
            link_url="/provider/tour-guides/verification-status",
        )

    _commit(db)
    db.refresh(guide)

    return AdminBadgeUpdateResponse(
        success=True,
        tour_guide_id=guide.id,
        verification_status=guide.verification_status,
        is_verified_badge=guide.verification_status == "verified",
        message=f"Guide verification status updated to '{guide.verification_status}'.",
    )
