"""
CeylonGo Academic MVP — Authentication Router
Endpoints for Traveler, Provider, and Admin Registration, Login, Session Invalidation, Email Check, Google OAuth, and Password Reset.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.core.mvp_security import get_current_mvp_user
from app.core.rate_limiter import check_email_limiter, forgot_password_limiter, shared_auth_limiter
from app.core.sensitive_route import SensitiveInputRoute
from app.database.mvp_session import get_mvp_db
from app.models.mvp import MVPUser
from app.schemas.auth import (
    TravelerChallengeRequest,
    TravelerChallengeResponse,
    TravelerVerifiedResponse,
    TravelerVerifyEmailRequest,
)
from app.schemas.mvp import (
    CheckEmailRequest,
    CheckEmailResponse,
    GenericMessageResponse,
    GoogleAuthRequest,
    NonTravelerRegisterRequest,
    PasswordResetConfirm,
    PasswordResetRequest,
    TokenResponse,
    UserLoginRequest,
    TravelerRegisterRequest,
    VerifyResetTokenResponse,
)
from app.schemas.mvp_account import Profile
from app.services import traveler_email_verification as traveler_verification
from app.services.mvp_account import profile
from app.services.mvp_services import MVPAuthService

router = APIRouter(prefix="/auth", tags=["MVP Authentication"], route_class=SensitiveInputRoute)


def rate_limit_check_email(request: Request) -> None:
    check_email_limiter.check(request)


def rate_limit_forgot_password(request: Request) -> None:
    forgot_password_limiter.check(request)


def rate_limit_shared_auth(request: Request) -> None:
    shared_auth_limiter.check(request)


# ============================================================================
# 1. Email Account Detection (1:1 with Approved Popup Workflow)
# ============================================================================
@router.post(
    "/check-email",
    response_model=CheckEmailResponse,
    dependencies=[Depends(rate_limit_check_email)],
    status_code=status.HTTP_200_OK,
)
def check_email(req: CheckEmailRequest, db: Session = Depends(get_mvp_db)):
    """
    Check whether an email address is already registered in CeylonGo.
    Returns: {"email": str, "is_registered": bool}
    Rate limited to protect against account enumeration.
    """
    normalized_email = req.email.strip().lower()
    is_registered = MVPAuthService.check_email(db, normalized_email)
    return CheckEmailResponse(email=normalized_email, is_registered=is_registered)


# ============================================================================
# 2. Google OAuth Authentication (GIS Token Verification)
# ============================================================================
@router.post(
    "/google",
    response_model=TokenResponse,
    dependencies=[Depends(rate_limit_shared_auth)],
    status_code=status.HTTP_200_OK,
)
def google_auth(req: GoogleAuthRequest, db: Session = Depends(get_mvp_db)):
    """
    Authenticate traveler using a verified Google Identity Services ID token.
    Automatically provisions new traveler account if email is new.
    """
    token, user = MVPAuthService.login_with_google(db, req.id_token)
    return {"access_token": token, "token_type": "bearer", "user": user}


# ============================================================================
# 3. Standard Registration, Login & Session Management
# ============================================================================
@router.post(
    "/register",
    response_model=TravelerChallengeResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def register(
    req: TravelerRegisterRequest | NonTravelerRegisterRequest,
    request: Request,
    db: Session = Depends(get_mvp_db),
):
    if isinstance(req, NonTravelerRegisterRequest):
        raise HTTPException(403, "Use the dedicated provider registration workflow")
    traveler_verification.rate_limit_send(request)
    return traveler_verification.start_registration(
        db,
        MVPUser,
        email=req.email,
        full_name=req.full_name,
        password=req.password,
        phone=req.phone.strip() if req.phone else None,
    )


@router.post(
    "/verify-email",
    response_model=TravelerVerifiedResponse,
    dependencies=[Depends(traveler_verification.rate_limit_verify)],
)
def verify_traveler_email(
    req: TravelerVerifyEmailRequest, db: Session = Depends(get_mvp_db)
):
    return traveler_verification.verify_registration(db, MVPUser, req)


@router.post(
    "/resend-email-otp",
    response_model=TravelerChallengeResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(traveler_verification.rate_limit_send)],
)
def resend_traveler_email_otp(
    req: TravelerChallengeRequest, db: Session = Depends(get_mvp_db)
):
    return traveler_verification.resend_verification(db, MVPUser, req)


@router.post(
    "/login",
    response_model=TokenResponse,
    dependencies=[Depends(rate_limit_shared_auth)],
    status_code=status.HTTP_200_OK,
)
def login(req: UserLoginRequest, db: Session = Depends(get_mvp_db)):
    token, user = MVPAuthService.login_user(db, req)
    return {"access_token": token, "token_type": "bearer", "user": user}


@router.get("/me", response_model=Profile)
def get_current_profile(
    current_user: MVPUser = Depends(get_current_mvp_user),
    db: Session = Depends(get_mvp_db),
):
    return profile(db, current_user)


@router.post("/logout", status_code=status.HTTP_200_OK)
def logout(current_user: MVPUser = Depends(get_current_mvp_user), db: Session = Depends(get_mvp_db)):
    MVPAuthService.invalidate_sessions(db, current_user)
    return {"message": "Successfully logged out. All existing sessions invalidated."}


# ============================================================================
# 4. Password Recovery & Reset Flow (Email-based, Single-Use Expiring Tokens)
# ============================================================================
@router.post(
    "/forgot-password",
    response_model=GenericMessageResponse,
    dependencies=[Depends(rate_limit_forgot_password)],
    status_code=status.HTTP_200_OK,
)
@router.post(
    "/password-reset/request",
    response_model=GenericMessageResponse,
    dependencies=[Depends(rate_limit_forgot_password)],
    status_code=status.HTTP_200_OK,
)
def request_password_reset(req: PasswordResetRequest, db: Session = Depends(get_mvp_db)):
    """
    Initiates password recovery. Dispatches reset link via SMTP when configured.
    Returns generic anti-enumeration response and never exposes tokens in production.
    """
    MVPAuthService.request_password_reset(db, req, send_email=True)
    return {
        "message": "If an account exists with this email address, password reset instructions have been sent."
    }


@router.get(
    "/verify-reset-token",
    response_model=VerifyResetTokenResponse,
    status_code=status.HTTP_200_OK,
)
def verify_reset_token(token: str = Query(..., min_length=10), db: Session = Depends(get_mvp_db)):
    """
    Verify single-use reset token validity and expiration without consuming it.
    """
    user = MVPAuthService.verify_reset_token(db, token)
    return {
        "valid": True,
        "email": user.email,
        "message": "Password reset token is valid.",
    }


@router.post(
    "/reset-password",
    response_model=GenericMessageResponse,
    dependencies=[Depends(rate_limit_shared_auth)],
    status_code=status.HTTP_200_OK,
)
@router.post(
    "/password-reset/confirm",
    response_model=GenericMessageResponse,
    dependencies=[Depends(rate_limit_shared_auth)],
    status_code=status.HTTP_200_OK,
)
def confirm_password_reset(req: PasswordResetConfirm, db: Session = Depends(get_mvp_db)):
    """
    Resets user password, increments credential_version and auth_version to invalidate prior sessions.
    """
    MVPAuthService.confirm_password_reset(db, req)
    return {
        "message": "Password successfully updated. Please log in with your new password."
    }
