"""
CeylonGo Academic MVP — Provider Onboarding & Compliance Router
Endpoints for Provider Profile Registration, Document Uploads, and Email Verification Challenges.
"""

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.mvp_security import get_current_mvp_user, require_mvp_role
from app.database.mvp_session import get_mvp_db
from app.models.mvp import MVPProviderProfile, MVPUser
from app.schemas.mvp import (
    EmailChallengeRequest,
    EmailChallengeVerify,
    ProviderDocumentResponse,
    ProviderDocumentUpload,
    ProviderRegisterRequest,
    ProviderResponse,
    UserLoginRequest,
)
from app.services.mvp_services import MVPAuthService, MVPProviderService

router = APIRouter(prefix="/providers", tags=["MVP Providers"])


@router.post("/login")
def provider_login(req: UserLoginRequest, db: Session = Depends(get_mvp_db)):
    token, user = MVPAuthService.login_user(db, req)
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
    current_user: MVPUser = Depends(get_current_mvp_user),
    db: Session = Depends(get_mvp_db),
):
    profile = MVPProviderService.register_profile(db, current_user, req)
    return profile


@router.get("/me", response_model=ProviderResponse)
def get_my_provider_profile(
    current_user: MVPUser = Depends(get_current_mvp_user),
    db: Session = Depends(get_mvp_db),
):
    profile = db.query(MVPProviderProfile).filter(MVPProviderProfile.user_id == current_user.id).first()
    if not profile:
        from fastapi import HTTPException
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No provider profile registered.")
    return profile


@router.post("/documents", response_model=ProviderDocumentResponse, status_code=status.HTTP_201_CREATED)
def upload_document(
    req: ProviderDocumentUpload,
    current_user: MVPUser = Depends(get_current_mvp_user),
    db: Session = Depends(get_mvp_db),
):
    doc = MVPProviderService.upload_document(db, current_user, req)
    return doc


@router.post("/email-challenge/request", status_code=status.HTTP_200_OK)
def request_email_challenge(req: EmailChallengeRequest, db: Session = Depends(get_mvp_db)):
    code = MVPProviderService.create_email_challenge(db, req)
    return {"message": "Verification challenge sent.", "challenge_code_demo": code}


@router.post("/email-challenge/verify", status_code=status.HTTP_200_OK)
def verify_email_challenge(req: EmailChallengeVerify, db: Session = Depends(get_mvp_db)):
    MVPProviderService.verify_email_challenge(db, req)
    return {"message": "Email challenge successfully verified."}
