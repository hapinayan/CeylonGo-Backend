"""
FastAPI router for CeylonGo Rental Admin Portal (Phase 4 Batch 1).
Handles:
- AD-02: Rental Vehicle Listing Approval & Moderation
- AD-03: Rental Vehicle Document Verification
- Admin Audit Logging & Provider Notification Integration
"""

from datetime import date, datetime, timezone
from decimal import Decimal
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
    require_admin,
    require_finance_admin,
    require_finance_or_support_read,
    require_operations_admin,
    require_operations_or_finance_admin,
    require_operations_or_support_read,
    require_support_or_operations_admin,
    resolve_canonical_admin_role,
)
from app.core.notification_service import create_notification
from app.models.provider_profile import ProviderProfile
from app.models.rental import (
    RentalConditionEvidence,
    RentalDeposit,
    RentalIncident,
    RentalInspection,
    RentalLocation,
    RentalRatePlan,
    RentalVehicleDocument,
    RentalVehicleImage,
)
from app.models.rental_booking import RentalBooking
from app.models.rental_vehicle import RentalVehicle
from app.models.shared import (
    AdminAuditLog,
    BookingReference,
    BookingStatusHistory,
    ListingReference,
    PaymentRefund,
    PaymentTransaction,
    PromotionCampaign,
    PromotionEvent,
    PromotionPackage,
    ProviderSettlement,
)
from app.models.user import User
from app.schemas.admin_rentals import (
    AdminBookingStatusHistoryItem,
    AdminDocumentVerifyRequest,
    AdminDocumentVerifyResponse,
    AdminIncidentListResponse,
    AdminIncidentUpdateRequest,
    AdminIncidentUpdateResponse,
    AdminPendingDocumentsResponse,
    AdminRentalBookingDetailResponse,
    AdminRentalBookingInspectionItem,
    AdminRentalBookingListItem,
    AdminRentalBookingListResponse,
    AdminRentalDepositItem,
    AdminRentalDepositListResponse,
    AdminRentalFleetSummaryResponse,
    AdminRentalIncidentItem,
    AdminRentalPaymentListItem,
    AdminRentalPaymentListResponse,
    AdminRentalPromotionListItem,
    AdminRentalPromotionListResponse,
    AdminRentalPromotionReviewRequest,
    AdminRentalPromotionReviewResponse,
    AdminRentalRefundListItem,
    AdminRentalRefundListResponse,
    AdminRentalRefundReviewRequest,
    AdminRentalRefundReviewResponse,
    AdminRentalRevenueAnalyticsResponse,
    AdminRentalSettlementListItem,
    AdminRentalSettlementListResponse,
    AdminVehicleDetailResponse,
    AdminVehicleDocumentItem,
    AdminVehicleListItem,
    AdminVehicleListResponse,
    AdminVehicleReviewRequest,
    AdminVehicleReviewResponse,
    AdminVehicleVerifyStatusRequest,
    AdminVehicleVerifyStatusResponse,
)

router = APIRouter(
    prefix="/api/v1/admin/rentals",
    tags=["Rental Admin Operations"],
    dependencies=[Depends(require_admin)],
)


# ==============================================================================
# HELPER FUNCTIONS
# ==============================================================================

def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def get_rental_documents_directory() -> str:
    base_dir = os.getenv(
        "RENTAL_DOCUMENTS_DIR",
        os.path.join(os.getcwd(), "uploads", "rental_documents"),
    )
    os.makedirs(base_dir, exist_ok=True)
    return base_dir


def log_admin_audit(
    db: Session,
    admin_id: int,
    action: str,
    entity_name: str,
    entity_id: int | None = None,
    details: str | None = None,
    ip_address: str | None = None,
) -> AdminAuditLog:
    log_entry = AdminAuditLog(
        admin_user_id=admin_id,
        action=action,
        entity_name=entity_name,
        entity_id=entity_id,
        details=sanitize_audit_details(details),
        ip_address=ip_address,
    )
    db.add(log_entry)
    return log_entry


# ==============================================================================
# AD-02: RENTAL VEHICLE LISTING APPROVAL & MODERATION
# ==============================================================================

@router.get(
    "/vehicles",
    response_model=AdminVehicleListResponse,
    status_code=status.HTTP_200_OK,
    summary="Platform-wide rental vehicles list with filtering and pagination",
    dependencies=[Depends(require_operations_admin)],
)
def list_admin_rental_vehicles(
    db: Annotated[Session, Depends(get_db)],
    approval_status: Annotated[str | None, Query(description="Filter by approval status: pending, approved, rejected, suspended")] = None,
    verification_status: Annotated[str | None, Query(description="Filter by verification status: unverified, pending, verified, rejected")] = None,
    vehicle_type: Annotated[str | None, Query(description="Filter by category: bike, bicycle, tuk_tuk")] = None,
    provider_id: Annotated[int | None, Query(description="Filter by provider profile ID")] = None,
    district: Annotated[str | None, Query(description="Filter by district")] = None,
    search: Annotated[str | None, Query(description="Search by vehicle name or license plate")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
):
    query = (
        db.query(RentalVehicle, ProviderProfile.business_name)
        .join(ProviderProfile, RentalVehicle.provider_id == ProviderProfile.id)
    )

    if approval_status:
        query = query.filter(RentalVehicle.approval_status == approval_status.strip().lower())
    if verification_status:
        query = query.filter(RentalVehicle.verification_status == verification_status.strip().lower())
    if vehicle_type:
        query = query.filter(RentalVehicle.vehicle_type == vehicle_type.strip().lower())
    if provider_id:
        query = query.filter(RentalVehicle.provider_id == provider_id)
    if district:
        query = query.filter(RentalVehicle.district == district.strip())
    if search:
        term = f"%{search.strip().lower()}%"
        query = query.filter(
            or_(
                func.lower(RentalVehicle.vehicle_name).like(term),
                func.lower(RentalVehicle.license_plate).like(term),
                func.lower(ProviderProfile.business_name).like(term),
            )
        )

    total = query.count()
    total_pages = max(1, math.ceil(total / page_size)) if total > 0 else 0

    results = (
        query.order_by(RentalVehicle.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    items = []
    for vehicle, b_name in results:
        primary_img = (
            db.query(RentalVehicleImage.image_url)
            .filter(
                RentalVehicleImage.vehicle_id == vehicle.id,
                RentalVehicleImage.is_primary == True,
            )
            .first()
        )
        items.append(
            AdminVehicleListItem(
                id=vehicle.id,
                provider_id=vehicle.provider_id,
                provider_business_name=b_name or "Unknown Provider",
                vehicle_name=vehicle.vehicle_name,
                vehicle_type=vehicle.vehicle_type,
                license_plate=vehicle.license_plate,
                price_per_day=vehicle.price_per_day,
                district=vehicle.district,
                status=vehicle.status,
                approval_status=vehicle.approval_status,
                verification_status=vehicle.verification_status,
                primary_image_url=primary_img[0] if primary_img else None,
                created_at=vehicle.created_at,
            )
        )

    return AdminVehicleListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.get(
    "/vehicles/pending",
    response_model=AdminVehicleListResponse,
    status_code=status.HTTP_200_OK,
    summary="Convenience queue for vehicle listings awaiting administrative approval",
    dependencies=[Depends(require_operations_admin)],
)
def list_pending_rental_vehicles(
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
):
    return list_admin_rental_vehicles(
        db=db,
        approval_status="pending",
        verification_status=None,
        vehicle_type=None,
        provider_id=None,
        district=None,
        search=None,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/vehicles/{vehicle_id}",
    response_model=AdminVehicleDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Detailed vehicle inspection view for administrative review",
    dependencies=[Depends(require_operations_admin)],
)
def get_admin_vehicle_detail(
    vehicle_id: int,
    db: Annotated[Session, Depends(get_db)],
):
    vehicle = (
        db.query(RentalVehicle)
        .filter(RentalVehicle.id == vehicle_id)
        .first()
    )
    if not vehicle:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Rental vehicle not found.",
        )

    provider = (
        db.query(ProviderProfile)
        .filter(ProviderProfile.id == vehicle.provider_id)
        .first()
    )

    owner_user = (
        db.query(User)
        .filter(User.id == provider.user_id)
        .first()
        if provider
        else None
    )

    loc = (
        db.query(RentalLocation.location_name)
        .filter(RentalLocation.id == vehicle.location_id)
        .first()
        if vehicle.location_id
        else None
    )

    primary_img = (
        db.query(RentalVehicleImage.image_url)
        .filter(
            RentalVehicleImage.vehicle_id == vehicle.id,
            RentalVehicleImage.is_primary == True,
        )
        .first()
    )

    img_count = db.query(RentalVehicleImage).filter(RentalVehicleImage.vehicle_id == vehicle.id).count()
    doc_count = db.query(RentalVehicleDocument).filter(RentalVehicleDocument.vehicle_id == vehicle.id).count()
    verified_doc_count = (
        db.query(RentalVehicleDocument)
        .filter(
            RentalVehicleDocument.vehicle_id == vehicle.id,
            RentalVehicleDocument.is_verified == True,
        )
        .count()
    )
    rate_plans_count = db.query(RentalRatePlan).filter(RentalRatePlan.vehicle_id == vehicle.id).count()

    return AdminVehicleDetailResponse(
        id=vehicle.id,
        provider_id=vehicle.provider_id,
        provider_business_name=provider.business_name if provider else "Unknown",
        provider_name=owner_user.full_name if owner_user else "Unknown Owner",
        provider_phone=provider.phone if provider else "",
        provider_approval_status=provider.approval_status if provider else "unknown",
        vehicle_name=vehicle.vehicle_name,
        vehicle_type=vehicle.vehicle_type,
        model_year=vehicle.model_year,
        license_plate=vehicle.license_plate,
        seating_capacity=vehicle.seating_capacity,
        transmission=vehicle.transmission,
        fuel_type=vehicle.fuel_type,
        price_per_day=vehicle.price_per_day,
        district=vehicle.district,
        city=vehicle.city,
        location_name=loc[0] if loc else None,
        status=vehicle.status,
        approval_status=vehicle.approval_status,
        verification_status=vehicle.verification_status,
        reviewed_at=vehicle.reviewed_at,
        reviewed_by=vehicle.reviewed_by,
        rejection_reason=vehicle.rejection_reason,
        cancellation_policy=vehicle.cancellation_policy,
        free_cancellation_hours=vehicle.free_cancellation_hours,
        images_count=img_count,
        primary_image_url=primary_img[0] if primary_img else None,
        documents_count=doc_count,
        verified_documents_count=verified_doc_count,
        rate_plans_count=rate_plans_count,
        created_at=vehicle.created_at,
        updated_at=vehicle.updated_at,
    )


@router.post(
    "/vehicles/{vehicle_id}/review",
    response_model=AdminVehicleReviewResponse,
    status_code=status.HTTP_200_OK,
    summary="Approve, reject, or suspend a rental vehicle listing",
)
def review_rental_vehicle(
    vehicle_id: int,
    payload: AdminVehicleReviewRequest,
    admin_user: Annotated[User, Depends(require_operations_admin)],
    request: Request,
    db: Annotated[Session, Depends(get_db)],
):
    now = utc_now()
    vehicle = (
        db.query(RentalVehicle)
        .filter(RentalVehicle.id == vehicle_id)
        .first()
    )
    if not vehicle:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Rental vehicle not found.",
        )

    provider = (
        db.query(ProviderProfile)
        .filter(ProviderProfile.id == vehicle.provider_id)
        .first()
    )

    prev_status = vehicle.approval_status
    client_ip = request.client.host if request.client else None

    # 1. APPROVAL WORKFLOW
    if payload.decision == "approved":
        if prev_status == "approved":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Vehicle listing is already approved.",
            )
        if prev_status == "rejected":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot approve a rejected listing directly. The provider must update the listing specifications or documents first.",
            )

        # Requirement 10: Verify required documents for motorized vehicles according to development spec
        if vehicle.vehicle_type in ("bike", "tuk_tuk"):
            # Check for verified revenue license and insurance certificate
            verified_doc_types = {
                d.document_type
                for d in db.query(RentalVehicleDocument.document_type)
                .filter(
                    RentalVehicleDocument.vehicle_id == vehicle.id,
                    RentalVehicleDocument.is_verified == True,
                )
                .all()
            }
            if "revenue_license" not in verified_doc_types or "insurance_certificate" not in verified_doc_types:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Cannot approve motorized vehicle listing: verified revenue_license and insurance_certificate documents are required.",
                )

        vehicle.approval_status = "approved"
        vehicle.reviewed_by = admin_user.id
        vehicle.reviewed_at = now
        vehicle.rejection_reason = None
        # Ensure status is available when approved
        if vehicle.status == "retired":
            vehicle.status = "available"

        # Sync central ListingReference if exists
        listing_ref = (
            db.query(ListingReference)
            .filter(
                ListingReference.module_type == "rental_vehicle",
                ListingReference.target_id == vehicle.id,
            )
            .first()
        )
        if listing_ref:
            listing_ref.is_active = True

        log_admin_audit(
            db=db,
            admin_id=admin_user.id,
            action="approve_rental_vehicle",
            entity_name="rental_vehicles",
            entity_id=vehicle.id,
            details=f"Approved vehicle '{vehicle.vehicle_name}' (ID #{vehicle.id})",
            ip_address=client_ip,
        )

        if provider:
            create_notification(
                db=db,
                user_id=provider.user_id,
                title="Vehicle Listing Approved",
                message=f"Congratulations! Your rental vehicle '{vehicle.vehicle_name}' has been approved by admin and is now publicly discoverable.",
                notification_type="vehicle_listing_approved",
                link_url=f"/provider/rentals/vehicles/{vehicle.id}",
            )

        msg = f"Vehicle #{vehicle.id} has been approved successfully."

    # 2. REJECTION WORKFLOW
    elif payload.decision == "rejected":
        if not payload.reason or not payload.reason.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Rejection reason is mandatory when rejecting a vehicle listing.",
            )
        if prev_status == "rejected":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Vehicle listing is already rejected.",
            )

        vehicle.approval_status = "rejected"
        vehicle.reviewed_by = admin_user.id
        vehicle.reviewed_at = now
        vehicle.rejection_reason = payload.reason.strip()

        # Sync central ListingReference
        listing_ref = (
            db.query(ListingReference)
            .filter(
                ListingReference.module_type == "rental_vehicle",
                ListingReference.target_id == vehicle.id,
            )
            .first()
        )
        if listing_ref:
            listing_ref.is_active = False

        log_admin_audit(
            db=db,
            admin_id=admin_user.id,
            action="reject_rental_vehicle",
            entity_name="rental_vehicles",
            entity_id=vehicle.id,
            details=f"Rejected vehicle '{vehicle.vehicle_name}' (ID #{vehicle.id}). Reason: {payload.reason.strip()}",
            ip_address=client_ip,
        )

        if provider:
            create_notification(
                db=db,
                user_id=provider.user_id,
                title="Vehicle Listing Rejected",
                message=f"Your rental vehicle '{vehicle.vehicle_name}' was rejected by admin. Reason: {payload.reason.strip()}",
                notification_type="vehicle_listing_rejected",
                link_url=f"/provider/rentals/vehicles/{vehicle.id}",
            )

        msg = f"Vehicle #{vehicle.id} has been rejected."

    # 3. SUSPENSION WORKFLOW
    elif payload.decision == "suspended":
        if not payload.reason or not payload.reason.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Suspension reason is mandatory when suspending a vehicle listing.",
            )
        if prev_status == "suspended":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Vehicle listing is already suspended.",
            )

        vehicle.approval_status = "suspended"
        vehicle.status = "unavailable"
        vehicle.reviewed_by = admin_user.id
        vehicle.reviewed_at = now
        vehicle.rejection_reason = payload.reason.strip()

        # Sync central ListingReference
        listing_ref = (
            db.query(ListingReference)
            .filter(
                ListingReference.module_type == "rental_vehicle",
                ListingReference.target_id == vehicle.id,
            )
            .first()
        )
        if listing_ref:
            listing_ref.is_active = False

        log_admin_audit(
            db=db,
            admin_id=admin_user.id,
            action="suspend_rental_vehicle",
            entity_name="rental_vehicles",
            entity_id=vehicle.id,
            details=f"Suspended vehicle '{vehicle.vehicle_name}' (ID #{vehicle.id}). Reason: {payload.reason.strip()}",
            ip_address=client_ip,
        )

        if provider:
            create_notification(
                db=db,
                user_id=provider.user_id,
                title="Vehicle Listing Suspended",
                message=f"Your rental vehicle '{vehicle.vehicle_name}' has been suspended by admin. Reason: {payload.reason.strip()}",
                notification_type="vehicle_listing_suspended",
                link_url=f"/provider/rentals/vehicles/{vehicle.id}",
            )

        msg = f"Vehicle #{vehicle.id} has been suspended."

    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid review decision '{payload.decision}'. Allowed: approved, rejected, suspended.",
        )

    db.commit()
    db.refresh(vehicle)

    return AdminVehicleReviewResponse(
        vehicle_id=vehicle.id,
        vehicle_name=vehicle.vehicle_name,
        previous_approval_status=prev_status,
        new_approval_status=vehicle.approval_status,
        reviewed_at=vehicle.reviewed_at,
        reviewed_by=admin_user.id,
        reviewer_name=admin_user.full_name,
        rejection_reason=vehicle.rejection_reason,
        message=msg,
    )


# ==============================================================================
# AD-03: VEHICLE DOCUMENT VERIFICATION
# ==============================================================================

@router.get(
    "/documents/pending",
    response_model=AdminPendingDocumentsResponse,
    status_code=status.HTTP_200_OK,
    summary="Queue of pending vehicle compliance documents awaiting verification",
    dependencies=[Depends(require_operations_admin)],
)
def list_pending_vehicle_documents(
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
):
    query = (
        db.query(
            RentalVehicleDocument,
            RentalVehicle.vehicle_name,
            RentalVehicle.provider_id,
            ProviderProfile.business_name,
        )
        .join(RentalVehicle, RentalVehicleDocument.vehicle_id == RentalVehicle.id)
        .join(ProviderProfile, RentalVehicle.provider_id == ProviderProfile.id)
        .filter(RentalVehicleDocument.is_verified == False)
    )

    total = query.count()
    total_pages = max(1, math.ceil(total / page_size)) if total > 0 else 0

    results = (
        query.order_by(RentalVehicleDocument.created_at.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    items = [
        AdminVehicleDocumentItem(
            id=doc.id,
            vehicle_id=doc.vehicle_id,
            vehicle_name=v_name,
            provider_id=p_id,
            provider_business_name=b_name,
            document_type=doc.document_type,
            document_number=doc.document_number,
            file_reference=doc.file_reference,
            expiry_date=doc.expiry_date,
            is_verified=doc.is_verified,
            created_at=doc.created_at,
        )
        for doc, v_name, p_id, b_name in results
    ]

    return AdminPendingDocumentsResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.get(
    "/vehicles/{vehicle_id}/documents",
    response_model=list[AdminVehicleDocumentItem],
    status_code=status.HTTP_200_OK,
    summary="List all uploaded compliance documents for a specific vehicle",
    dependencies=[Depends(require_operations_admin)],
)
def list_vehicle_documents(
    vehicle_id: int,
    db: Annotated[Session, Depends(get_db)],
):
    vehicle = db.query(RentalVehicle).filter(RentalVehicle.id == vehicle_id).first()
    if not vehicle:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Rental vehicle not found.",
        )

    provider = db.query(ProviderProfile).filter(ProviderProfile.id == vehicle.provider_id).first()
    b_name = provider.business_name if provider else "Unknown"

    docs = (
        db.query(RentalVehicleDocument)
        .filter(RentalVehicleDocument.vehicle_id == vehicle_id)
        .order_by(RentalVehicleDocument.created_at.asc())
        .all()
    )

    return [
        AdminVehicleDocumentItem(
            id=d.id,
            vehicle_id=d.vehicle_id,
            vehicle_name=vehicle.vehicle_name,
            provider_id=vehicle.provider_id,
            provider_business_name=b_name,
            document_type=d.document_type,
            document_number=d.document_number,
            file_reference=d.file_reference,
            expiry_date=d.expiry_date,
            is_verified=d.is_verified,
            created_at=d.created_at,
        )
        for d in docs
    ]


@router.get(
    "/documents/{document_id}",
    response_model=AdminVehicleDocumentItem,
    status_code=status.HTTP_200_OK,
    summary="Get single vehicle document details",
    dependencies=[Depends(require_operations_admin)],
)
def get_vehicle_document_detail(
    document_id: int,
    db: Annotated[Session, Depends(get_db)],
):
    doc = db.query(RentalVehicleDocument).filter(RentalVehicleDocument.id == document_id).first()
    if not doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Vehicle document not found.",
        )

    vehicle = db.query(RentalVehicle).filter(RentalVehicle.id == doc.vehicle_id).first()
    provider = (
        db.query(ProviderProfile).filter(ProviderProfile.id == vehicle.provider_id).first()
        if vehicle
        else None
    )

    return AdminVehicleDocumentItem(
        id=doc.id,
        vehicle_id=doc.vehicle_id,
        vehicle_name=vehicle.vehicle_name if vehicle else "Unknown",
        provider_id=vehicle.provider_id if vehicle else 0,
        provider_business_name=provider.business_name if provider else "Unknown",
        document_type=doc.document_type,
        document_number=doc.document_number,
        file_reference=doc.file_reference,
        expiry_date=doc.expiry_date,
        is_verified=doc.is_verified,
        created_at=doc.created_at,
    )


@router.get(
    "/documents/{document_id}/file",
    response_class=FileResponse,
    summary="Securely stream a vehicle compliance document file to authenticated Admins",
    dependencies=[Depends(require_operations_admin)],
)
def view_vehicle_document_file(
    document_id: int,
    db: Annotated[Session, Depends(get_db)],
):
    """
    Securely stream an uploaded vehicle document file to authenticated Admins.
    Guards against path traversal, checks MIME types, and sets anti-caching headers.
    """
    doc = db.query(RentalVehicleDocument).filter(RentalVehicleDocument.id == document_id).first()
    if not doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Vehicle document not found.",
        )

    upload_dir = get_rental_documents_directory()
    clean_filename = os.path.basename(doc.file_reference)
    file_path = os.path.abspath(os.path.join(upload_dir, clean_filename))

    # Path traversal protection
    if not file_path.startswith(os.path.abspath(upload_dir)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid document path traversal attempt detected.",
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
        elif ext == ".pdf":
            media_type = "application/pdf"
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
    "/documents/{document_id}/verify",
    response_model=AdminDocumentVerifyResponse,
    status_code=status.HTTP_200_OK,
    summary="Verify or reject a vehicle compliance document",
)
def verify_vehicle_document(
    document_id: int,
    payload: AdminDocumentVerifyRequest,
    admin_user: Annotated[User, Depends(require_operations_admin)],
    request: Request,
    db: Annotated[Session, Depends(get_db)],
):
    now = utc_now()
    doc = db.query(RentalVehicleDocument).filter(RentalVehicleDocument.id == document_id).first()
    if not doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Vehicle document not found.",
        )

    vehicle = db.query(RentalVehicle).filter(RentalVehicle.id == doc.vehicle_id).first()
    client_ip = request.client.host if request.client else None

    is_verified = (payload.decision == "approved")
    doc.is_verified = is_verified

    action = "verify_rental_document" if is_verified else "reject_rental_document"
    log_admin_audit(
        db=db,
        admin_id=admin_user.id,
        action=action,
        entity_name="rental_vehicle_documents",
        entity_id=doc.id,
        details=f"{payload.decision.capitalize()} document #{doc.id} ({doc.document_type}) for vehicle #{doc.vehicle_id}. Notes: {payload.notes or 'None'}",
        ip_address=client_ip,
    )

    db.commit()
    db.refresh(doc)

    return AdminDocumentVerifyResponse(
        document_id=doc.id,
        vehicle_id=doc.vehicle_id,
        document_type=doc.document_type,
        is_verified=doc.is_verified,
        reviewed_at=now,
        reviewed_by=admin_user.id,
        notes=payload.notes,
        message=f"Document #{doc.id} ({doc.document_type}) has been marked as {'verified' if is_verified else 'unverified'}.",
    )


@router.post(
    "/vehicles/{vehicle_id}/verify-status",
    response_model=AdminVehicleVerifyStatusResponse,
    status_code=status.HTTP_200_OK,
    summary="Update overall vehicle verification status based on document compliance",
)
def update_vehicle_verification_status(
    vehicle_id: int,
    payload: AdminVehicleVerifyStatusRequest,
    admin_user: Annotated[User, Depends(require_operations_admin)],
    request: Request,
    db: Annotated[Session, Depends(get_db)],
):
    vehicle = db.query(RentalVehicle).filter(RentalVehicle.id == vehicle_id).first()
    if not vehicle:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Rental vehicle not found.",
        )

    prev_status = vehicle.verification_status
    client_ip = request.client.host if request.client else None

    # Requirement 15: Implement overall vehicle verification status updates only when supported by verified document states
    docs = db.query(RentalVehicleDocument).filter(RentalVehicleDocument.vehicle_id == vehicle.id).all()
    verified_doc_types = {d.document_type for d in docs if d.is_verified}

    if payload.decision == "verified":
        if vehicle.vehicle_type in ("bike", "tuk_tuk"):
            if "revenue_license" not in verified_doc_types or "insurance_certificate" not in verified_doc_types:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Cannot verify vehicle: required compliance documents (revenue_license, insurance_certificate) must be verified first.",
                )
        vehicle.verification_status = "verified"

    elif payload.decision == "rejected":
        if not payload.reason or not payload.reason.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Rejection reason is mandatory when marking vehicle verification as rejected.",
            )
        vehicle.verification_status = "rejected"

    elif payload.decision == "unverified":
        vehicle.verification_status = "unverified"

    elif payload.decision == "suspended":
        vehicle.verification_status = "suspended"

    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid verification decision '{payload.decision}'. Allowed: verified, rejected, unverified, suspended.",
        )

    log_admin_audit(
        db=db,
        admin_id=admin_user.id,
        action="update_vehicle_verification_status",
        entity_name="rental_vehicles",
        entity_id=vehicle.id,
        details=f"Updated verification_status to '{payload.decision}' for vehicle #{vehicle.id}. Reason: {payload.reason or 'None'}",
        ip_address=client_ip,
    )

    db.commit()
    db.refresh(vehicle)

    return AdminVehicleVerifyStatusResponse(
        vehicle_id=vehicle.id,
        previous_verification_status=prev_status,
        new_verification_status=vehicle.verification_status,
        verified_documents_count=len(verified_doc_types),
        total_documents_count=len(docs),
        message=f"Vehicle #{vehicle.id} verification status updated to '{vehicle.verification_status}'.",
    )


# ==============================================================================
# AD-05: RENTAL BOOKING & INCIDENT MONITORING
# ==============================================================================

@router.get(
    "/bookings",
    response_model=AdminRentalBookingListResponse,
    status_code=status.HTTP_200_OK,
    summary="Platform-wide rental booking monitoring with filtering and pagination",
    dependencies=[Depends(require_operations_or_support_read)],
)
def list_admin_rental_bookings(
    db: Annotated[Session, Depends(get_db)],
    status_filter: Annotated[str | None, Query(alias="status", description="pending, confirmed, active, completed, cancelled, rejected, expired")] = None,
    payment_status: Annotated[str | None, Query(description="unpaid, paid, refunded, partially_refunded")] = None,
    provider_id: Annotated[int | None, Query(description="Filter by provider profile ID")] = None,
    vehicle_id: Annotated[int | None, Query(description="Filter by vehicle ID")] = None,
    pickup_date_from: Annotated[date | None, Query(description="Filter by pickup date from")] = None,
    pickup_date_to: Annotated[date | None, Query(description="Filter by pickup date to")] = None,
    search: Annotated[str | None, Query(description="Search booking reference, customer name, vehicle name, plate")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
):
    query = (
        db.query(RentalBooking, RentalVehicle, ProviderProfile, User)
        .join(RentalVehicle, RentalBooking.vehicle_id == RentalVehicle.id)
        .join(ProviderProfile, RentalVehicle.provider_id == ProviderProfile.id)
        .join(User, RentalBooking.user_id == User.id)
    )

    if status_filter:
        query = query.filter(func.lower(RentalBooking.status) == status_filter.strip().lower())
    if payment_status:
        query = query.filter(func.lower(RentalBooking.payment_status) == payment_status.strip().lower())
    if provider_id:
        query = query.filter(RentalVehicle.provider_id == provider_id)
    if vehicle_id:
        query = query.filter(RentalBooking.vehicle_id == vehicle_id)
    if pickup_date_from:
        start_dt = datetime.combine(pickup_date_from, datetime.min.time())
        query = query.filter(RentalBooking.pickup_at >= start_dt)
    if pickup_date_to:
        end_dt = datetime.combine(pickup_date_to, datetime.max.time())
        query = query.filter(RentalBooking.pickup_at <= end_dt)
    if search:
        term = f"%{search.strip().lower()}%"
        # Subquery or join for booking reference
        query = query.filter(
            or_(
                func.lower(RentalVehicle.vehicle_name).like(term),
                func.lower(RentalVehicle.license_plate).like(term),
                func.lower(User.full_name).like(term),
                func.lower(User.email).like(term),
            )
        )

    total = query.count()
    total_pages = max(1, math.ceil(total / page_size)) if total > 0 else 0

    results = (
        query.order_by(RentalBooking.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    items = []
    for b, v, p, u in results:
        # Resolve booking reference
        b_ref = (
            db.query(BookingReference)
            .filter(
                BookingReference.module_type == "rental",
                BookingReference.target_booking_id == b.id,
            )
            .first()
        )
        ref_str = b_ref.booking_reference if b_ref else f"RB-{b.id:06d}"

        # Incidents count
        inc_count = (
            db.query(func.count(RentalIncident.id))
            .filter(RentalIncident.booking_id == b.id)
            .scalar() or 0
        )

        items.append(
            AdminRentalBookingListItem(
                id=b.id,
                booking_reference=ref_str,
                vehicle_id=v.id,
                vehicle_name=v.vehicle_name,
                license_plate=v.license_plate,
                vehicle_type=v.vehicle_type,
                provider_id=p.id,
                provider_business_name=p.business_name,
                user_id=u.id,
                customer_name=u.full_name,
                customer_email=u.email,
                customer_phone=None,
                pickup_at=b.pickup_at,
                return_at=b.return_at,
                total_price=b.total_price,
                currency=b.currency,
                status=b.status,
                payment_method=b.payment_method,
                payment_status=b.payment_status,
                has_incidents=inc_count > 0,
                incident_count=inc_count,
                created_at=b.created_at,
            )
        )

    return AdminRentalBookingListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.get(
    "/bookings/{booking_id}",
    response_model=AdminRentalBookingDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Detailed rental booking inspection with lifecycle, inspections, incidents and deposits",
    dependencies=[Depends(require_operations_or_support_read)],
)
def get_admin_rental_booking_detail(
    booking_id: int,
    db: Annotated[Session, Depends(get_db)],
):
    row = (
        db.query(RentalBooking, RentalVehicle, ProviderProfile, User)
        .join(RentalVehicle, RentalBooking.vehicle_id == RentalVehicle.id)
        .join(ProviderProfile, RentalVehicle.provider_id == ProviderProfile.id)
        .join(User, RentalBooking.user_id == User.id)
        .filter(RentalBooking.id == booking_id)
        .first()
    )
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Rental booking with ID {booking_id} not found.",
        )

    b, v, p, u = row

    b_ref = (
        db.query(BookingReference)
        .filter(
            BookingReference.module_type == "rental",
            BookingReference.target_booking_id == b.id,
        )
        .first()
    )
    ref_str = b_ref.booking_reference if b_ref else f"RB-{b.id:06d}"

    # Incidents
    incidents_rows = (
        db.query(RentalIncident)
        .filter(RentalIncident.booking_id == b.id)
        .order_by(RentalIncident.created_at.desc())
        .all()
    )
    incident_items = [
        AdminRentalIncidentItem(
            id=inc.id,
            booking_id=inc.booking_id,
            vehicle_id=v.id,
            vehicle_name=v.vehicle_name,
            incident_type=inc.incident_type,
            incident_datetime=inc.incident_datetime,
            description=inc.description,
            estimated_damage_cost=inc.estimated_damage_cost,
            actual_repair_cost=inc.actual_repair_cost,
            police_report_reference=inc.police_report_reference,
            resolution_status=inc.resolution_status,
            created_at=inc.created_at,
            updated_at=inc.updated_at,
        )
        for inc in incidents_rows
    ]

    # Inspections
    inspections_rows = (
        db.query(RentalInspection)
        .filter(RentalInspection.booking_id == b.id)
        .order_by(RentalInspection.created_at.asc())
        .all()
    )
    inspection_items = []
    for insp in inspections_rows:
        ev_count = (
            db.query(func.count(RentalConditionEvidence.id))
            .filter(RentalConditionEvidence.inspection_id == insp.id)
            .scalar() or 0
        )
        inspection_items.append(
            AdminRentalBookingInspectionItem(
                id=insp.id,
                inspection_type=insp.inspection_type,
                odometer_reading=insp.odometer_reading,
                fuel_level_percent=insp.fuel_level_percent,
                cleanliness_status=insp.cleanliness_status,
                customer_signature=insp.customer_signature,
                notes=insp.notes,
                created_at=insp.created_at,
                evidence_count=ev_count,
            )
        )

    # Deposits
    deposit_rows = (
        db.query(RentalDeposit)
        .filter(RentalDeposit.booking_id == b.id)
        .order_by(RentalDeposit.held_at.desc())
        .all()
    )
    deposit_items = [
        AdminRentalDepositItem(
            id=dep.id,
            booking_id=dep.booking_id,
            deposit_amount=dep.deposit_amount,
            currency=dep.currency,
            deposit_status=dep.deposit_status,
            deducted_amount=dep.deducted_amount,
            deduction_reason=dep.deduction_reason,
            refunded_amount=dep.refunded_amount,
            held_at=dep.held_at,
            released_at=dep.released_at,
        )
        for dep in deposit_rows
    ]

    # Status History
    history_items = []
    if b_ref:
        hist_rows = (
            db.query(BookingStatusHistory)
            .filter(BookingStatusHistory.booking_reference_id == b_ref.id)
            .order_by(BookingStatusHistory.created_at.asc())
            .all()
        )
        history_items = [
            AdminBookingStatusHistoryItem(
                id=h.id,
                from_status=h.from_status,
                to_status=h.to_status,
                changed_by=h.changed_by,
                notes=h.notes,
                created_at=h.created_at,
            )
            for h in hist_rows
        ]

    # Payment calculations
    online_paid = Decimal("0.00")
    cash_paid = Decimal("0.00")
    if b_ref:
        tx_rows = (
            db.query(PaymentTransaction)
            .filter(
                PaymentTransaction.booking_reference_id == b_ref.id,
                PaymentTransaction.payment_status.in_(["success", "paid"]),
            )
            .all()
        )
        for tx in tx_rows:
            if tx.gateway_name == "cash_on_pickup":
                cash_paid += tx.amount
            else:
                online_paid += tx.amount

    # Locations
    p_loc = db.query(RentalLocation).filter(RentalLocation.id == b.pickup_location_id).first() if b.pickup_location_id else None
    r_loc = db.query(RentalLocation).filter(RentalLocation.id == b.return_location_id).first() if b.return_location_id else None

    booking_item = AdminRentalBookingListItem(
        id=b.id,
        booking_reference=ref_str,
        vehicle_id=v.id,
        vehicle_name=v.vehicle_name,
        license_plate=v.license_plate,
        vehicle_type=v.vehicle_type,
        provider_id=p.id,
        provider_business_name=p.business_name,
        user_id=u.id,
        customer_name=u.full_name,
        customer_email=u.email,
        customer_phone=None,
        pickup_at=b.pickup_at,
        return_at=b.return_at,
        total_price=b.total_price,
        currency=b.currency,
        status=b.status,
        payment_method=b.payment_method,
        payment_status=b.payment_status,
        has_incidents=len(incident_items) > 0,
        incident_count=len(incident_items),
        created_at=b.created_at,
    )

    return AdminRentalBookingDetailResponse(
        booking=booking_item,
        pickup_location=p_loc.location_name if p_loc else None,
        return_location=r_loc.location_name if r_loc else None,
        daily_rate_snapshot=b.daily_rate_snapshot,
        cancellation_policy_snapshot=b.cancellation_policy_snapshot,
        free_cancellation_hours_snapshot=b.free_cancellation_hours_snapshot,
        cancellation_reason=b.cancellation_reason,
        cancelled_at=b.cancelled_at,
        cancelled_by=b.cancelled_by,
        inspections=inspection_items,
        incidents=incident_items,
        deposits=deposit_items,
        status_history=history_items,
        paid_online_amount=online_paid,
        cash_collected_amount=cash_paid,
    )


@router.get(
    "/incidents",
    response_model=AdminIncidentListResponse,
    status_code=status.HTTP_200_OK,
    summary="Platform-wide rental incident and damage report monitoring",
    dependencies=[Depends(require_operations_or_support_read)],
)
def list_admin_rental_incidents(
    db: Annotated[Session, Depends(get_db)],
    resolution_status: Annotated[str | None, Query(description="reported, under_investigation, insurance_claimed, customer_billed, resolved, dismissed")] = None,
    incident_type: Annotated[str | None, Query(description="scratch, dent, accident, mechanical_failure, theft, late_return, traffic_fine")] = None,
    booking_id: Annotated[int | None, Query()] = None,
    vehicle_id: Annotated[int | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
):
    query = (
        db.query(RentalIncident, RentalBooking, RentalVehicle)
        .join(RentalBooking, RentalIncident.booking_id == RentalBooking.id)
        .join(RentalVehicle, RentalBooking.vehicle_id == RentalVehicle.id)
    )

    if resolution_status:
        query = query.filter(func.lower(RentalIncident.resolution_status) == resolution_status.strip().lower())
    if incident_type:
        query = query.filter(func.lower(RentalIncident.incident_type) == incident_type.strip().lower())
    if booking_id:
        query = query.filter(RentalIncident.booking_id == booking_id)
    if vehicle_id:
        query = query.filter(RentalBooking.vehicle_id == vehicle_id)

    total = query.count()
    total_pages = max(1, math.ceil(total / page_size)) if total > 0 else 0

    results = (
        query.order_by(RentalIncident.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    items = [
        AdminRentalIncidentItem(
            id=inc.id,
            booking_id=inc.booking_id,
            vehicle_id=veh.id,
            vehicle_name=veh.vehicle_name,
            incident_type=inc.incident_type,
            incident_datetime=inc.incident_datetime,
            description=inc.description,
            estimated_damage_cost=inc.estimated_damage_cost,
            actual_repair_cost=inc.actual_repair_cost,
            police_report_reference=inc.police_report_reference,
            resolution_status=inc.resolution_status,
            created_at=inc.created_at,
            updated_at=inc.updated_at,
        )
        for inc, _, veh in results
    ]

    return AdminIncidentListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.get(
    "/incidents/{incident_id}",
    response_model=AdminRentalIncidentItem,
    status_code=status.HTTP_200_OK,
    summary="Get single rental incident details",
    dependencies=[Depends(require_operations_or_support_read)],
)
def get_admin_rental_incident_detail(
    incident_id: int,
    db: Annotated[Session, Depends(get_db)],
):
    row = (
        db.query(RentalIncident, RentalBooking, RentalVehicle)
        .join(RentalBooking, RentalIncident.booking_id == RentalBooking.id)
        .join(RentalVehicle, RentalBooking.vehicle_id == RentalVehicle.id)
        .filter(RentalIncident.id == incident_id)
        .first()
    )
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Rental incident #{incident_id} not found.",
        )

    inc, _, veh = row
    return AdminRentalIncidentItem(
        id=inc.id,
        booking_id=inc.booking_id,
        vehicle_id=veh.id,
        vehicle_name=veh.vehicle_name,
        incident_type=inc.incident_type,
        incident_datetime=inc.incident_datetime,
        description=inc.description,
        estimated_damage_cost=inc.estimated_damage_cost,
        actual_repair_cost=inc.actual_repair_cost,
        police_report_reference=inc.police_report_reference,
        resolution_status=inc.resolution_status,
        created_at=inc.created_at,
        updated_at=inc.updated_at,
    )


@router.patch(
    "/incidents/{incident_id}",
    response_model=AdminIncidentUpdateResponse,
    status_code=status.HTTP_200_OK,
    summary="Update rental incident resolution status and repair costs with audit logging",
)
def update_admin_rental_incident(
    incident_id: int,
    payload: AdminIncidentUpdateRequest,
    admin_user: Annotated[User, Depends(require_support_or_operations_admin)],
    db: Annotated[Session, Depends(get_db)],
    request: Request,
):
    client_ip = request.client.host if request.client else None
    incident = db.query(RentalIncident).filter(RentalIncident.id == incident_id).first()
    if not incident:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Rental incident #{incident_id} not found.",
        )

    valid_statuses = [
        "reported",
        "under_investigation",
        "insurance_claimed",
        "customer_billed",
        "resolved",
        "dismissed",
    ]
    norm_status = payload.resolution_status.strip().lower()
    if norm_status not in valid_statuses:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid resolution status '{payload.resolution_status}'. Allowed: {', '.join(valid_statuses)}.",
        )

    prev_status = incident.resolution_status
    if prev_status in ("resolved", "dismissed") and norm_status != prev_status:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Incident #{incident.id} is already in a terminal status ('{prev_status}') and cannot be modified.",
        )
    incident.resolution_status = norm_status
    if payload.actual_repair_cost is not None:
        incident.actual_repair_cost = payload.actual_repair_cost
    if payload.police_report_reference is not None:
        incident.police_report_reference = payload.police_report_reference.strip()

    log_admin_audit(
        db=db,
        admin_id=admin_user.id,
        action="update_rental_incident",
        entity_name="rental_incidents",
        entity_id=incident.id,
        details=f"Updated incident #{incident.id} status from '{prev_status}' to '{norm_status}'. Actual repair cost: {incident.actual_repair_cost}. Notes: {payload.admin_notes or 'None'}",
        ip_address=client_ip,
    )

    db.commit()
    db.refresh(incident)

    return AdminIncidentUpdateResponse(
        incident_id=incident.id,
        previous_status=prev_status,
        new_status=incident.resolution_status,
        actual_repair_cost=incident.actual_repair_cost,
        police_report_reference=incident.police_report_reference,
        message=f"Incident #{incident.id} updated successfully.",
    )


# ==============================================================================
# AD-06: PAYMENT, SETTLEMENT & REFUND MONITORING
# ==============================================================================

@router.get(
    "/payments",
    response_model=AdminRentalPaymentListResponse,
    status_code=status.HTTP_200_OK,
    summary="Platform-wide rental payment transaction monitoring",
    dependencies=[Depends(require_finance_admin)],
)
def list_admin_rental_payments(
    db: Annotated[Session, Depends(get_db)],
    payment_status: Annotated[str | None, Query(description="pending, success, paid, failed, refunded")] = None,
    payment_method: Annotated[str | None, Query()] = None,
    provider_id: Annotated[int | None, Query()] = None,
    date_from: Annotated[date | None, Query()] = None,
    date_to: Annotated[date | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
):
    query = (
        db.query(PaymentTransaction, BookingReference, ProviderProfile, User)
        .join(BookingReference, PaymentTransaction.booking_reference_id == BookingReference.id)
        .join(ProviderProfile, BookingReference.provider_profile_id == ProviderProfile.id)
        .join(User, PaymentTransaction.user_id == User.id)
        .filter(BookingReference.module_type == "rental")
    )

    if payment_status:
        query = query.filter(func.lower(PaymentTransaction.payment_status) == payment_status.strip().lower())
    if payment_method:
        query = query.filter(func.lower(PaymentTransaction.payment_method) == payment_method.strip().lower())
    if provider_id:
        query = query.filter(BookingReference.provider_profile_id == provider_id)
    if date_from:
        start_dt = datetime.combine(date_from, datetime.min.time())
        query = query.filter(PaymentTransaction.created_at >= start_dt)
    if date_to:
        end_dt = datetime.combine(date_to, datetime.max.time())
        query = query.filter(PaymentTransaction.created_at <= end_dt)

    total = query.count()
    total_pages = max(1, math.ceil(total / page_size)) if total > 0 else 0

    results = (
        query.order_by(PaymentTransaction.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    items = [
        AdminRentalPaymentListItem(
            id=tx.id,
            booking_reference_id=b_ref.id,
            booking_reference=b_ref.booking_reference,
            target_booking_id=b_ref.target_booking_id,
            gateway_name=tx.gateway_name,
            gateway_transaction_id=tx.gateway_transaction_id,
            amount=tx.amount,
            currency=tx.currency,
            payment_status=tx.payment_status,
            payment_method=tx.payment_method,
            paid_at=tx.paid_at,
            created_at=tx.created_at,
            customer_name=u.full_name,
            provider_business_name=p.business_name,
        )
        for tx, b_ref, p, u in results
    ]

    return AdminRentalPaymentListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.get(
    "/settlements",
    response_model=AdminRentalSettlementListResponse,
    status_code=status.HTTP_200_OK,
    summary="Platform-wide rental provider settlements monitoring",
    dependencies=[Depends(require_finance_admin)],
)
def list_admin_rental_settlements(
    db: Annotated[Session, Depends(get_db)],
    settlement_status: Annotated[str | None, Query(description="pending, completed, processing")] = None,
    provider_id: Annotated[int | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
):
    query = (
        db.query(ProviderSettlement, ProviderProfile)
        .join(ProviderProfile, ProviderSettlement.provider_profile_id == ProviderProfile.id)
        .filter(ProviderProfile.provider_type == "rental")
    )

    if settlement_status:
        query = query.filter(func.lower(ProviderSettlement.settlement_status) == settlement_status.strip().lower())
    if provider_id:
        query = query.filter(ProviderSettlement.provider_profile_id == provider_id)

    total = query.count()
    total_pages = max(1, math.ceil(total / page_size)) if total > 0 else 0

    results = (
        query.order_by(ProviderSettlement.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    items = [
        AdminRentalSettlementListItem(
            id=s.id,
            provider_profile_id=s.provider_profile_id,
            provider_business_name=p.business_name,
            period_start=s.period_start,
            period_end=s.period_end,
            gross_amount=s.gross_amount,
            platform_fee=s.platform_fee,
            net_payable=s.net_payable,
            currency=s.currency,
            settlement_status=s.settlement_status,
            payout_reference=s.payout_reference,
            paid_at=s.paid_at,
            created_at=s.created_at,
        )
        for s, p in results
    ]

    return AdminRentalSettlementListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.get(
    "/deposits",
    response_model=AdminRentalDepositListResponse,
    status_code=status.HTTP_200_OK,
    summary="Platform-wide rental security deposits monitoring",
    dependencies=[Depends(require_finance_or_support_read)],
)
def list_admin_rental_deposits(
    db: Annotated[Session, Depends(get_db)],
    deposit_status: Annotated[str | None, Query(description="held, partially_refunded, fully_refunded, forfeited, deducted")] = None,
    booking_id: Annotated[int | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
):
    query = db.query(RentalDeposit)
    if deposit_status:
        query = query.filter(func.lower(RentalDeposit.deposit_status) == deposit_status.strip().lower())
    if booking_id:
        query = query.filter(RentalDeposit.booking_id == booking_id)

    total = query.count()
    total_pages = max(1, math.ceil(total / page_size)) if total > 0 else 0

    results = (
        query.order_by(RentalDeposit.held_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    items = [
        AdminRentalDepositItem(
            id=d.id,
            booking_id=d.booking_id,
            deposit_amount=d.deposit_amount,
            currency=d.currency,
            deposit_status=d.deposit_status,
            deducted_amount=d.deducted_amount,
            deduction_reason=d.deduction_reason,
            refunded_amount=d.refunded_amount,
            held_at=d.held_at,
            released_at=d.released_at,
        )
        for d in results
    ]

    return AdminRentalDepositListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.get(
    "/refunds",
    response_model=AdminRentalRefundListResponse,
    status_code=status.HTTP_200_OK,
    summary="Platform-wide rental refund request monitoring",
    dependencies=[Depends(require_finance_or_support_read)],
)
def list_admin_rental_refunds(
    db: Annotated[Session, Depends(get_db)],
    refund_status: Annotated[str | None, Query(description="pending, approved, rejected, processed")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
):
    query = (
        db.query(PaymentRefund, PaymentTransaction, BookingReference, User)
        .join(PaymentTransaction, PaymentRefund.payment_transaction_id == PaymentTransaction.id)
        .join(BookingReference, PaymentTransaction.booking_reference_id == BookingReference.id)
        .join(User, PaymentTransaction.user_id == User.id)
        .filter(BookingReference.module_type == "rental")
    )

    if refund_status:
        query = query.filter(func.lower(PaymentRefund.refund_status) == refund_status.strip().lower())

    total = query.count()
    total_pages = max(1, math.ceil(total / page_size)) if total > 0 else 0

    results = (
        query.order_by(PaymentRefund.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    items = [
        AdminRentalRefundListItem(
            id=ref.id,
            payment_transaction_id=tx.id,
            booking_reference=b_ref.booking_reference,
            target_booking_id=b_ref.target_booking_id,
            amount=ref.amount,
            currency=ref.currency,
            refund_reason=ref.refund_reason,
            refund_status=ref.refund_status,
            processed_by=ref.processed_by,
            processed_at=ref.processed_at,
            created_at=ref.created_at,
            customer_name=u.full_name,
        )
        for ref, tx, b_ref, u in results
    ]

    return AdminRentalRefundListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.post(
    "/refunds/{refund_id}/review",
    response_model=AdminRentalRefundReviewResponse,
    status_code=status.HTTP_200_OK,
    summary="Audit-only review and decision for rental refund request",
)
def review_admin_rental_refund(
    refund_id: int,
    payload: AdminRentalRefundReviewRequest,
    admin_user: Annotated[User, Depends(require_finance_admin)],
    db: Annotated[Session, Depends(get_db)],
    request: Request,
):
    client_ip = request.client.host if request.client else None

    # Fetch refund and verify it belongs to rental
    row = (
        db.query(PaymentRefund, PaymentTransaction, BookingReference)
        .join(PaymentTransaction, PaymentRefund.payment_transaction_id == PaymentTransaction.id)
        .join(BookingReference, PaymentTransaction.booking_reference_id == BookingReference.id)
        .filter(
            PaymentRefund.id == refund_id,
            BookingReference.module_type == "rental",
        )
        .first()
    )
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Rental refund request #{refund_id} not found.",
        )

    refund, tx, b_ref = row

    if refund.refund_status != "pending":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Refund request #{refund.id} has already been processed with status '{refund.refund_status}'.",
        )

    prev_status = refund.refund_status
    refund.refund_status = payload.decision
    refund.processed_by = admin_user.id
    refund.processed_at = utc_now()

    # Log audit entry (Audit-only administrative review; NO real money transfer)
    log_admin_audit(
        db=db,
        admin_id=admin_user.id,
        action=f"{payload.decision}_rental_refund",
        entity_name="payment_refunds",
        entity_id=refund.id,
        details=f"Admin {payload.decision} refund #{refund.id} for transaction #{tx.id} ({refund.amount} {refund.currency}). Notes: {payload.admin_notes or 'None'}",
        ip_address=client_ip,
    )

    # In-app notification to traveler
    create_notification(
        db=db,
        user_id=tx.user_id,
        title=f"Rental Refund {payload.decision.capitalize()}",
        message=f"Your refund request for {refund.amount} {refund.currency} on booking #{b_ref.target_booking_id} has been {payload.decision}.",
        notification_type="refund_review",
        link_url=f"/bookings/{b_ref.target_booking_id}",
    )

    db.commit()
    db.refresh(refund)

    return AdminRentalRefundReviewResponse(
        refund_id=refund.id,
        previous_status=prev_status,
        new_status=refund.refund_status,
        processed_by=refund.processed_by,
        processed_at=refund.processed_at,
        message=f"Refund request #{refund.id} successfully marked as {refund.refund_status} (audit-only recorded).",
    )


# ==============================================================================
# AD-07: SPONSORED PROMOTION MANAGEMENT
# ==============================================================================

@router.get(
    "/promotions",
    response_model=AdminRentalPromotionListResponse,
    status_code=status.HTTP_200_OK,
    summary="Platform-wide rental sponsored promotion campaign monitoring",
    dependencies=[Depends(require_operations_or_finance_admin)],
)
def list_admin_rental_promotions(
    db: Annotated[Session, Depends(get_db)],
    status_filter: Annotated[str | None, Query(alias="status", description="pending_approval, approved, active, paused, rejected, completed")] = None,
    provider_id: Annotated[int | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
):
    query = (
        db.query(PromotionCampaign, ProviderProfile)
        .join(ProviderProfile, PromotionCampaign.provider_profile_id == ProviderProfile.id)
        .filter(PromotionCampaign.target_module.in_(["rental", "rental_vehicle"]))
    )

    if status_filter:
        query = query.filter(func.lower(PromotionCampaign.status) == status_filter.strip().lower())
    if provider_id:
        query = query.filter(PromotionCampaign.provider_profile_id == provider_id)

    total = query.count()
    total_pages = max(1, math.ceil(total / page_size)) if total > 0 else 0

    results = (
        query.order_by(PromotionCampaign.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    items = []
    for c, p in results:
        imp_count = (
            db.query(func.count(PromotionEvent.id))
            .filter(PromotionEvent.campaign_id == c.id, PromotionEvent.event_type == "impression")
            .scalar() or 0
        )
        clk_count = (
            db.query(func.count(PromotionEvent.id))
            .filter(PromotionEvent.campaign_id == c.id, PromotionEvent.event_type == "click")
            .scalar() or 0
        )
        # Paid status: status is active, approved with payment, or completed
        is_paid = c.status in ["active", "completed"]

        items.append(
            AdminRentalPromotionListItem(
                id=c.id,
                provider_profile_id=c.provider_profile_id,
                provider_business_name=p.business_name,
                campaign_name=c.campaign_name,
                target_module=c.target_module,
                budget=c.budget,
                spent_amount=c.spent_amount,
                currency=c.currency,
                status=c.status,
                starts_at=c.starts_at,
                ends_at=c.ends_at,
                is_paid=is_paid,
                impressions_count=imp_count,
                clicks_count=clk_count,
                created_at=c.created_at,
            )
        )

    return AdminRentalPromotionListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.get(
    "/promotions/{campaign_id}",
    response_model=AdminRentalPromotionListItem,
    status_code=status.HTTP_200_OK,
    summary="Get single rental sponsored promotion campaign details",
    dependencies=[Depends(require_operations_or_finance_admin)],
)
def get_admin_rental_promotion_detail(
    campaign_id: int,
    db: Annotated[Session, Depends(get_db)],
):
    row = (
        db.query(PromotionCampaign, ProviderProfile)
        .join(ProviderProfile, PromotionCampaign.provider_profile_id == ProviderProfile.id)
        .filter(
            PromotionCampaign.id == campaign_id,
            PromotionCampaign.target_module.in_(["rental", "rental_vehicle"]),
        )
        .first()
    )
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Rental promotion campaign #{campaign_id} not found.",
        )

    c, p = row
    imp_count = (
        db.query(func.count(PromotionEvent.id))
        .filter(PromotionEvent.campaign_id == c.id, PromotionEvent.event_type == "impression")
        .scalar() or 0
    )
    clk_count = (
        db.query(func.count(PromotionEvent.id))
        .filter(PromotionEvent.campaign_id == c.id, PromotionEvent.event_type == "click")
        .scalar() or 0
    )

    return AdminRentalPromotionListItem(
        id=c.id,
        provider_profile_id=c.provider_profile_id,
        provider_business_name=p.business_name,
        campaign_name=c.campaign_name,
        target_module=c.target_module,
        budget=c.budget,
        spent_amount=c.spent_amount,
        currency=c.currency,
        status=c.status,
        starts_at=c.starts_at,
        ends_at=c.ends_at,
        is_paid=c.status in ["active", "completed"],
        impressions_count=imp_count,
        clicks_count=clk_count,
        created_at=c.created_at,
    )


@router.post(
    "/promotions/{campaign_id}/review",
    response_model=AdminRentalPromotionReviewResponse,
    status_code=status.HTTP_200_OK,
    summary="Admin review, approval, rejection, and payment-verified activation of sponsored campaign",
)
def review_admin_rental_promotion(
    campaign_id: int,
    payload: AdminRentalPromotionReviewRequest,
    admin_user: Annotated[User, Depends(require_operations_or_finance_admin)],
    db: Annotated[Session, Depends(get_db)],
    request: Request,
):
    # Enforce action-level RBAC BEFORE any DB query, mutation, or side effect:
    # - Editorial / operational review actions ('approve', 'reject', 'pause') require super_admin (or legacy admin) or operations_admin.
    # - Financial payment-verified activation ('activate') requires super_admin (or legacy admin) or finance_admin.
    # - Unknown decisions are rejected immediately (deny-by-default).
    decision = payload.decision.strip().lower() if isinstance(payload.decision, str) else ""
    canonical_role = resolve_canonical_admin_role(admin_user.role)

    if decision in ("approve", "reject", "pause"):
        if canonical_role not in ("super_admin", "operations_admin"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Access forbidden: action '{decision}' requires one of "
                    f"[operations_admin, super_admin] (current role: '{canonical_role}')"
                ),
            )
    elif decision == "activate":
        if canonical_role not in ("super_admin", "finance_admin"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Access forbidden: payment-verified action 'activate' requires one of "
                    f"[finance_admin, super_admin] (current role: '{canonical_role}')"
                ),
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid review decision '{payload.decision}'. Allowed: approve, reject, activate, pause.",
        )

    client_ip = request.client.host if request.client else None
    row = (
        db.query(PromotionCampaign, ProviderProfile)
        .join(ProviderProfile, PromotionCampaign.provider_profile_id == ProviderProfile.id)
        .filter(
            PromotionCampaign.id == campaign_id,
            PromotionCampaign.target_module.in_(["rental", "rental_vehicle"]),
        )
        .first()
    )
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Rental promotion campaign #{campaign_id} not found.",
        )

    campaign, provider = row
    prev_status = campaign.status

    if payload.decision == "approve":
        campaign.status = "approved"
        msg = f"Campaign '{campaign.campaign_name}' approved. Awaiting payment before activation."
        log_admin_audit(
            db=db,
            admin_id=admin_user.id,
            action="approve_rental_promotion",
            entity_name="promotion_campaigns",
            entity_id=campaign.id,
            details=f"Approved promotion campaign '{campaign.campaign_name}' (ID #{campaign.id}).",
            ip_address=client_ip,
        )
        create_notification(
            db=db,
            user_id=provider.user_id,
            title="Rental Promotion Approved",
            message=f"Your campaign '{campaign.campaign_name}' has been approved by admin.",
            notification_type="promotion_approved",
            link_url=f"/provider/rentals/promotions",
        )

    elif payload.decision == "reject":
        if not payload.reason or not payload.reason.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Rejection reason is mandatory when rejecting a promotion campaign.",
            )
        campaign.status = "rejected"
        msg = f"Campaign '{campaign.campaign_name}' rejected."
        log_admin_audit(
            db=db,
            admin_id=admin_user.id,
            action="reject_rental_promotion",
            entity_name="promotion_campaigns",
            entity_id=campaign.id,
            details=f"Rejected promotion campaign '{campaign.campaign_name}' (ID #{campaign.id}). Reason: {payload.reason.strip()}",
            ip_address=client_ip,
        )
        create_notification(
            db=db,
            user_id=provider.user_id,
            title="Rental Promotion Rejected",
            message=f"Your campaign '{campaign.campaign_name}' was rejected by admin. Reason: {payload.reason.strip()}",
            notification_type="promotion_rejected",
            link_url=f"/provider/rentals/promotions",
        )

    elif payload.decision == "activate":
        # Requirement 12 & 13: Require valid promotion payment verification before activating
        # Prevent activation of unapproved or unpaid promotions
        if campaign.status not in ["approved", "paused"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot activate campaign: campaign must be approved first. Current status: '{campaign.status}'.",
            )
        if not payload.payment_reference or not payload.payment_reference.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot activate campaign: valid promotion payment verification required. Provide payment_reference.",
            )

        campaign.status = "active"
        msg = f"Campaign '{campaign.campaign_name}' activated with verified payment ref '{payload.payment_reference.strip()}'."
        log_admin_audit(
            db=db,
            admin_id=admin_user.id,
            action="activate_rental_promotion",
            entity_name="promotion_campaigns",
            entity_id=campaign.id,
            details=f"Activated promotion campaign '{campaign.campaign_name}' (ID #{campaign.id}).",
            ip_address=client_ip,
        )
        create_notification(
            db=db,
            user_id=provider.user_id,
            title="Rental Promotion Activated",
            message=f"Your campaign '{campaign.campaign_name}' is now active!",
            notification_type="promotion_activated",
            link_url=f"/provider/rentals/promotions",
        )

    elif payload.decision == "pause":
        if campaign.status != "active":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Only active campaigns can be paused. Current status: '{campaign.status}'.",
            )
        campaign.status = "paused"
        msg = f"Campaign '{campaign.campaign_name}' paused."
        log_admin_audit(
            db=db,
            admin_id=admin_user.id,
            action="pause_rental_promotion",
            entity_name="promotion_campaigns",
            entity_id=campaign.id,
            details=f"Paused promotion campaign '{campaign.campaign_name}' (ID #{campaign.id}).",
            ip_address=client_ip,
        )

    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid review decision '{payload.decision}'. Allowed: approve, reject, activate, pause.",
        )

    db.commit()
    db.refresh(campaign)

    return AdminRentalPromotionReviewResponse(
        campaign_id=campaign.id,
        campaign_name=campaign.campaign_name,
        previous_status=prev_status,
        new_status=campaign.status,
        is_paid=campaign.status in ["active", "completed"],
        message=msg,
    )


# ==============================================================================
# AD-08: RENTAL REPORTS & ANALYTICS
# ==============================================================================

@router.get(
    "/reports/fleet-summary",
    response_model=AdminRentalFleetSummaryResponse,
    status_code=status.HTTP_200_OK,
    summary="Fleet inventory analytics across approval states, verification, categories and availability",
    dependencies=[Depends(require_operations_admin)],
)
def get_admin_rental_fleet_summary(
    db: Annotated[Session, Depends(get_db)],
):
    total = db.query(RentalVehicle).count()
    approved = db.query(RentalVehicle).filter(RentalVehicle.approval_status == "approved").count()
    pending = db.query(RentalVehicle).filter(RentalVehicle.approval_status == "pending").count()
    rejected = db.query(RentalVehicle).filter(RentalVehicle.approval_status == "rejected").count()
    suspended = db.query(RentalVehicle).filter(RentalVehicle.approval_status == "suspended").count()

    verified = db.query(RentalVehicle).filter(RentalVehicle.verification_status == "verified").count()
    unverified = db.query(RentalVehicle).filter(RentalVehicle.verification_status.in_(["unverified", "pending"])).count()

    available = db.query(RentalVehicle).filter(RentalVehicle.status == "available").count()
    maintenance = db.query(RentalVehicle).filter(RentalVehicle.status == "maintenance").count()

    # Active rentals currently rented
    now = utc_now()
    rented_count = (
        db.query(func.count(func.distinct(RentalBooking.vehicle_id)))
        .filter(
            RentalBooking.status == "active",
            RentalBooking.pickup_at <= now,
            RentalBooking.return_at >= now,
        )
        .scalar() or 0
    )

    # Category distribution
    cat_rows = (
        db.query(RentalVehicle.vehicle_type, func.count(RentalVehicle.id))
        .group_by(RentalVehicle.vehicle_type)
        .all()
    )
    cat_dist = {cat: count for cat, count in cat_rows}
    # Ensure all 3 canonical categories are present
    for k in ["bike", "bicycle", "tuk_tuk"]:
        if k not in cat_dist:
            cat_dist[k] = 0

    # District distribution
    dist_rows = (
        db.query(RentalVehicle.district, func.count(RentalVehicle.id))
        .group_by(RentalVehicle.district)
        .all()
    )
    district_dist = {dist: count for dist, count in dist_rows if dist}

    return AdminRentalFleetSummaryResponse(
        total_vehicles=total,
        approved_vehicles=approved,
        pending_approval_vehicles=pending,
        rejected_vehicles=rejected,
        suspended_vehicles=suspended,
        verified_vehicles=verified,
        unverified_vehicles=unverified,
        available_vehicles=available,
        maintenance_vehicles=maintenance,
        currently_rented_vehicles=rented_count,
        category_distribution=cat_dist,
        district_distribution=district_dist,
    )


@router.get(
    "/reports/revenue-analytics",
    response_model=AdminRentalRevenueAnalyticsResponse,
    status_code=status.HTTP_200_OK,
    summary="Financial performance analytics: gross booking value, payments, commission, settlements",
    dependencies=[Depends(require_operations_or_finance_admin)],
)
def get_admin_rental_revenue_analytics(
    db: Annotated[Session, Depends(get_db)],
    start_date: Annotated[date | None, Query()] = None,
    end_date: Annotated[date | None, Query()] = None,
    provider_id: Annotated[int | None, Query()] = None,
):
    b_query = (
        db.query(RentalBooking)
        .join(RentalVehicle, RentalBooking.vehicle_id == RentalVehicle.id)
    )

    if provider_id:
        b_query = b_query.filter(RentalVehicle.provider_id == provider_id)
    if start_date:
        s_dt = datetime.combine(start_date, datetime.min.time())
        b_query = b_query.filter(RentalBooking.pickup_at >= s_dt)
    if end_date:
        e_dt = datetime.combine(end_date, datetime.max.time())
        b_query = b_query.filter(RentalBooking.pickup_at <= e_dt)

    bookings = b_query.all()
    total_bookings = len(bookings)
    confirmed_bookings = sum(1 for b in bookings if b.status == "confirmed")
    completed_bookings = sum(1 for b in bookings if b.status == "completed")
    cancelled_bookings = sum(1 for b in bookings if b.status in ["cancelled", "rejected"])

    # Gross booking value: total_price of non-cancelled, non-rejected bookings
    gross_value = sum(
        (b.total_price for b in bookings if b.status not in ["cancelled", "rejected"]),
        Decimal("0.00"),
    )

    # Payments query (module_type == 'rental')
    p_query = (
        db.query(PaymentTransaction)
        .join(BookingReference, PaymentTransaction.booking_reference_id == BookingReference.id)
        .filter(
            BookingReference.module_type == "rental",
            PaymentTransaction.payment_status.in_(["success", "paid"]),
        )
    )
    if provider_id:
        p_query = p_query.filter(BookingReference.provider_profile_id == provider_id)
    if start_date:
        s_dt = datetime.combine(start_date, datetime.min.time())
        p_query = p_query.filter(PaymentTransaction.created_at >= s_dt)
    if end_date:
        e_dt = datetime.combine(end_date, datetime.max.time())
        p_query = p_query.filter(PaymentTransaction.created_at <= e_dt)

    transactions = p_query.all()
    confirmed_online = Decimal("0.00")
    cash_collected = Decimal("0.00")
    for tx in transactions:
        if tx.gateway_name == "cash_on_pickup":
            cash_collected += tx.amount
        else:
            confirmed_online += tx.amount

    total_collected = confirmed_online + cash_collected

    # Settlements query for rental providers
    s_query = (
        db.query(ProviderSettlement)
        .join(ProviderProfile, ProviderSettlement.provider_profile_id == ProviderProfile.id)
        .filter(ProviderProfile.provider_type == "rental")
    )
    if provider_id:
        s_query = s_query.filter(ProviderSettlement.provider_profile_id == provider_id)
    if start_date:
        s_query = s_query.filter(ProviderSettlement.period_start >= start_date)
    if end_date:
        s_query = s_query.filter(ProviderSettlement.period_end <= end_date)

    settlements = s_query.all()
    # Verified platform fee recorded in database (Requirement 16: Zero hardcoded or invented rates!)
    platform_commission = sum((s.platform_fee for s in settlements), Decimal("0.00"))
    completed_payouts = sum((s.net_payable for s in settlements if s.settlement_status == "completed"), Decimal("0.00"))
    pending_settlements = sum((s.net_payable for s in settlements if s.settlement_status == "pending"), Decimal("0.00"))

    # Security deposits
    dep_query = (
        db.query(RentalDeposit)
        .join(RentalBooking, RentalDeposit.booking_id == RentalBooking.id)
        .join(RentalVehicle, RentalBooking.vehicle_id == RentalVehicle.id)
    )
    if provider_id:
        dep_query = dep_query.filter(RentalVehicle.provider_id == provider_id)
    deposits = dep_query.all()
    deposits_held = sum((d.deposit_amount - d.deducted_amount - d.refunded_amount for d in deposits if d.deposit_status == "held"), Decimal("0.00"))
    deposits_refunded = sum((d.refunded_amount for d in deposits), Decimal("0.00"))

    return AdminRentalRevenueAnalyticsResponse(
        total_bookings=total_bookings,
        confirmed_bookings=confirmed_bookings,
        completed_bookings=completed_bookings,
        cancelled_bookings=cancelled_bookings,
        gross_booking_value=gross_value,
        confirmed_online_payments=confirmed_online,
        cash_collected=cash_collected,
        total_collected_revenue=total_collected,
        platform_commission=platform_commission,
        completed_settlement_payouts=completed_payouts,
        pending_settlements=pending_settlements,
        security_deposits_held=deposits_held,
        security_deposits_refunded=deposits_refunded,
        currency="LKR",
    )

