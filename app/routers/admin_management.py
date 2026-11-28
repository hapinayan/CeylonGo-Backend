from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.dependencies import require_super_admin
from app.database.connection import get_db
from app.schemas.admin_management import (
    AdminAccountItem,
    AdminAccountsListResponse,
    AdminActivationRequest,
    AdminInvitationCreate,
    AdminInvitationResponse,
    AdminInvitationVerifyResponse,
    AdminStatusToggleRequest,
)
from app.services.admin_invitation_service import AdminInvitationService

router = APIRouter(
    prefix="/api/v1/admin/management",
    tags=["Admin Management & Invitations"],
)

# Public router for activation flows without super admin credentials
public_router = APIRouter(
    prefix="/api/v1/admin/auth/invitations",
    tags=["Admin Account Activation"],
)


# ============================================================================
# SUPER ADMIN RESTRICTED ENDPOINTS
# ============================================================================


@router.get(
    "/admins",
    response_model=AdminAccountsListResponse,
    status_code=status.HTTP_200_OK,
    summary="List administrator accounts and pending invitations",
)
def list_admin_accounts(
    role: Optional[str] = Query(None, description="Filter by admin role"),
    status: Optional[str] = Query(None, description="Filter by status (active, inactive, invited)"),
    search: Optional[str] = Query(None, description="Search term for full name or email"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_admin: Any = Depends(require_super_admin),
    db: Session = Depends(get_db),
) -> AdminAccountsListResponse:
    return AdminInvitationService.list_admin_accounts(
        db=db,
        current_admin=current_admin,
        role=role,
        status_filter=status,
        search=search,
        page=page,
        page_size=page_size,
    )


@router.post(
    "/invitations",
    response_model=AdminInvitationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Issue an administrator invitation email",
)
def create_admin_invitation(
    payload: AdminInvitationCreate,
    current_admin: Any = Depends(require_super_admin),
    db: Session = Depends(get_db),
) -> AdminInvitationResponse:
    return AdminInvitationService.create_invitation(
        db=db,
        current_admin=current_admin,
        payload=payload,
    )


@router.get(
    "/invitations",
    response_model=list[AdminInvitationResponse],
    status_code=status.HTTP_200_OK,
    summary="List all issued administrator invitations",
)
def list_admin_invitations(
    status: Optional[str] = Query(None, description="Filter by status: pending, accepted, revoked, expired"),
    current_admin: Any = Depends(require_super_admin),
    db: Session = Depends(get_db),
) -> list[AdminInvitationResponse]:
    return AdminInvitationService.list_invitations(db=db, status_filter=status)


@router.post(
    "/invitations/{invitation_id}/resend",
    response_model=AdminInvitationResponse,
    status_code=status.HTTP_200_OK,
    summary="Resend an administrator invitation with a fresh single-use token",
)
def resend_admin_invitation(
    invitation_id: int,
    current_admin: Any = Depends(require_super_admin),
    db: Session = Depends(get_db),
) -> AdminInvitationResponse:
    return AdminInvitationService.resend_invitation(
        db=db,
        invitation_id=invitation_id,
        current_admin=current_admin,
    )


@router.post(
    "/invitations/{invitation_id}/revoke",
    status_code=status.HTTP_200_OK,
    summary="Revoke a pending administrator invitation",
)
def revoke_admin_invitation(
    invitation_id: int,
    current_admin: Any = Depends(require_super_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return AdminInvitationService.revoke_invitation(
        db=db,
        invitation_id=invitation_id,
        current_admin=current_admin,
    )


@router.post(
    "/admins/{admin_id}/status",
    response_model=AdminAccountItem,
    status_code=status.HTTP_200_OK,
    summary="Toggle active/inactive status of an administrator",
)
def toggle_admin_status(
    admin_id: int,
    payload: AdminStatusToggleRequest,
    current_admin: Any = Depends(require_super_admin),
    db: Session = Depends(get_db),
) -> AdminAccountItem:
    return AdminInvitationService.toggle_status(
        db=db,
        current_admin=current_admin,
        admin_id=admin_id,
        is_active=payload.is_active,
    )


# ============================================================================
# PUBLIC INVITATION VERIFICATION & ACTIVATION ENDPOINTS
# ============================================================================


@public_router.get(
    "/verify",
    response_model=AdminInvitationVerifyResponse,
    status_code=status.HTTP_200_OK,
    summary="Verify administrator invitation token validity",
)
def verify_invitation_token(
    token: str = Query(..., min_length=16, description="Invitation token"),
    db: Session = Depends(get_db),
) -> AdminInvitationVerifyResponse:
    return AdminInvitationService.verify_token(db=db, raw_token=token)


@public_router.post(
    "/activate",
    status_code=status.HTTP_200_OK,
    summary="Activate administrator account by setting password",
)
def activate_admin_account(
    payload: AdminActivationRequest,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return AdminInvitationService.activate_account(
        db=db,
        raw_token=payload.token,
        password=payload.password,
    )
