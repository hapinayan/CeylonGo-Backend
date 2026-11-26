"""
CeylonGo Academic MVP — Provider Onboarding & Compliance Router
Endpoints for Provider Profile Registration, Document Uploads, and Email Verification Challenges.
"""

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.mvp_security import require_mvp_role
from app.core.security import hash_password
from app.core.rate_limiter import (
    provider_login_limiter, provider_otp_send_limiter, provider_otp_verify_limiter,
    provider_password_setup_limiter,
)
from app.core.sensitive_route import SensitiveInputRoute
from app.database.mvp_session import get_mvp_db
from app.models.mvp import MVPProviderProfile, MVPUser
from app.schemas.mvp import (
    EmailChallengeVerify,
    GenericMessageResponse,
    ProviderDocumentResponse,
    ProviderDocumentUpload,
    ProviderRegisterRequest,
    ProviderResponse,
)
from app.services.mvp_services import MVPAuthService, MVPProviderService
from app.schemas.provider_otp import (
    SendOtpRequest, SendOtpResponse, VerifyOtpRequest, VerifyOtpResponse,
)
from app.schemas.provider_auth import (
    ProviderLoginRequest, ProviderLoginResponse, ProviderPasswordSetupRequest,
    ProviderPasswordSetupResponse,
)
from app.schemas.provider_registration import (
    BusinessDetailsRequest, DraftResponse, PersonalDetailsRequest, RegistrationSubmitResponse,
)
from app.services import mvp_provider_otp, mvp_provider_registration as registration

router = APIRouter(
    prefix="/providers", tags=["MVP Providers"], route_class=SensitiveInputRoute
)
registration_bearer = HTTPBearer(auto_error=False)


@router.post("/login", response_model=ProviderLoginResponse)
def provider_login(req: ProviderLoginRequest, request: Request, db: Session = Depends(get_mvp_db)):
    provider_login_limiter.check_peer(request, "mvp-provider-login")
    token, user = MVPAuthService.login_user(db, req, provider_only=True)
    prof = db.query(MVPProviderProfile).filter(MVPProviderProfile.user_id == user.id).first()
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": user.id,
            "email": user.email,
            "full_name": user.full_name,
            "role": user.role,
            "is_active": user.is_active,
            "business_name": prof.business_name if prof else user.full_name,
            "provider_type": "mvp_provider",
            "district": prof.district if prof else "",
            "approval_status": prof.verification_status if prof else "pending",
        },
    }


@router.post("/register", response_model=ProviderResponse, status_code=status.HTTP_201_CREATED)
def register_provider_profile(
    req: ProviderRegisterRequest,
    current_user: MVPUser = Depends(require_mvp_role("provider")),
    db: Session = Depends(get_mvp_db),
):
    profile = MVPProviderService.register_profile(db, current_user, req)
    return profile


@router.get("/me", response_model=ProviderResponse)
def get_my_provider_profile(
    current_user: MVPUser = Depends(require_mvp_role("provider")),
    db: Session = Depends(get_mvp_db),
):
    profile = db.query(MVPProviderProfile).filter(MVPProviderProfile.user_id == current_user.id).first()
    if not profile:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No provider profile registered.")
    return profile


@router.post("/logout", response_model=GenericMessageResponse)
def provider_logout(
    current_user: MVPUser = Depends(require_mvp_role("provider")),
    db: Session = Depends(get_mvp_db),
):
    MVPAuthService.invalidate_sessions(db, current_user)
    return {"message": "Successfully logged out. All existing sessions invalidated."}


@router.post("/documents", response_model=ProviderDocumentResponse, status_code=status.HTTP_201_CREATED)
def upload_document(
    req: ProviderDocumentUpload,
    current_user: MVPUser = Depends(require_mvp_role("provider")),
    db: Session = Depends(get_mvp_db),
):
    doc = MVPProviderService.upload_document(db, current_user, req)
    return doc


@router.post("/otp/send", response_model=SendOtpResponse)
@router.post("/email-challenge/request", response_model=SendOtpResponse, include_in_schema=False)
def request_email_challenge(req: SendOtpRequest, request: Request,
                            db: Session = Depends(get_mvp_db)):
    provider_otp_send_limiter.check_peer(request, "mvp-provider-otp-send")
    return mvp_provider_otp.send(db, req.email.lower())


@router.post("/otp/verify", response_model=VerifyOtpResponse)
def verify_email_challenge(req: VerifyOtpRequest, request: Request,
                           db: Session = Depends(get_mvp_db)):
    provider_otp_verify_limiter.check_peer(request, "mvp-provider-otp-verify")
    return mvp_provider_otp.verify(db, req.email.lower(), req.otp)


@router.post("/email-challenge/verify", response_model=VerifyOtpResponse, include_in_schema=False)
def verify_previous_email_challenge(req: EmailChallengeVerify, request: Request,
                                    db: Session = Depends(get_mvp_db)):
    return verify_email_challenge(
        VerifyOtpRequest(email=req.email, otp=req.code), request, db
    )


def registration_context(
    db: Session = Depends(get_mvp_db),
    auth: HTTPAuthorizationCredentials | None = Depends(registration_bearer),
):
    if auth is None:
        raise HTTPException(401, "Provider registration authorization is required")
    return mvp_provider_otp.authorize(db, auth.credentials, lock=True)


@router.get("/registration/draft", response_model=DraftResponse)
def get_draft(context=Depends(registration_context), db: Session = Depends(get_mvp_db)):
    registration.require_draft_storage(db)
    challenge, _, profile = context
    registration.editable(profile)
    return registration.draft_response(challenge.email, profile)


@router.put("/registration/personal", response_model=DraftResponse)
def save_personal(req: PersonalDetailsRequest, context=Depends(registration_context),
                  db: Session = Depends(get_mvp_db)):
    registration.require_draft_storage(db)
    return registration.save_personal(db, *context, req)


@router.put("/registration/business", response_model=DraftResponse)
def save_business(req: BusinessDetailsRequest, context=Depends(registration_context),
                  db: Session = Depends(get_mvp_db)):
    registration.require_draft_storage(db)
    challenge, _, profile = context
    return registration.save_business(db, challenge, profile, req)


@router.post("/registration/identity", response_model=DraftResponse)
def upload_identity(
    selfie: UploadFile | None = File(None),
    nic_front: UploadFile | None = File(None),
    nic_back: UploadFile | None = File(None),
    context=Depends(registration_context),
    db: Session = Depends(get_mvp_db),
):
    registration.require_draft_storage(db)
    challenge, _, profile = context
    return registration.save_documents(db, challenge, profile, {
        "selfie": selfie, "nic_front": nic_front, "nic_back": nic_back,
    })


@router.post("/registration/submit", response_model=RegistrationSubmitResponse)
def submit(context=Depends(registration_context), db: Session = Depends(get_mvp_db)):
    registration.require_draft_storage(db)
    challenge, _, profile = context
    return registration.submit(db, challenge, profile)


@router.post("/password/setup", response_model=ProviderPasswordSetupResponse)
def setup_password(
    req: ProviderPasswordSetupRequest,
    request: Request,
    context=Depends(registration_context),
    db: Session = Depends(get_mvp_db),
):
    provider_password_setup_limiter.check_peer(request, "mvp-provider-password-setup")
    challenge, user, profile = context
    if (not profile or profile.verification_status != "approved"
            or user.hashed_password != mvp_provider_otp.PASSWORD_UNSET
            or user.is_active or not profile.decided_at
            or challenge.verified_at < profile.decided_at):
        raise HTTPException(403, "Approved providers must verify a fresh code to set a password")
    mvp_provider_otp.consume(db, challenge)
    user.hashed_password = hash_password(req.password)
    user.is_active = True
    user.credential_version += 1
    user.updated_at = mvp_provider_otp.utc_now()
    db.commit()
    return ProviderPasswordSetupResponse()
