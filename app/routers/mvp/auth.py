"""
CeylonGo Academic MVP — Authentication Router
Endpoints for Traveler, Provider, and Admin Registration, Login, Session Invalidation, and Password Reset.
"""

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.mvp_security import get_current_mvp_user
from app.database.mvp_session import get_mvp_db
from app.models.mvp import MVPUser
from app.schemas.mvp import (
    PasswordResetConfirm,
    PasswordResetRequest,
    TokenResponse,
    UserLoginRequest,
    UserRegisterRequest,
    UserResponse,
)
from app.services.mvp_services import MVPAuthService

router = APIRouter(prefix="/auth", tags=["MVP Authentication"])


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def register(req: UserRegisterRequest, db: Session = Depends(get_mvp_db)):
    user = MVPAuthService.register_user(db, req)
    return user


@router.post("/login", response_model=TokenResponse)
def login(req: UserLoginRequest, db: Session = Depends(get_mvp_db)):
    token, user = MVPAuthService.login_user(db, req)
    return {"access_token": token, "token_type": "bearer", "user": user}


@router.get("/me", response_model=UserResponse)
def get_current_profile(current_user: MVPUser = Depends(get_current_mvp_user)):
    return current_user


@router.post("/logout", status_code=status.HTTP_200_OK)
def logout(current_user: MVPUser = Depends(get_current_mvp_user), db: Session = Depends(get_mvp_db)):
    MVPAuthService.invalidate_sessions(db, current_user)
    return {"message": "Successfully logged out. All existing sessions invalidated."}


@router.post("/password-reset/request", status_code=status.HTTP_200_OK)
def request_password_reset(req: PasswordResetRequest, db: Session = Depends(get_mvp_db)):
    token = MVPAuthService.request_password_reset(db, req)
    return {"message": "Password reset instructions generated.", "reset_token_demo": token}


@router.post("/password-reset/confirm", status_code=status.HTTP_200_OK)
def confirm_password_reset(req: PasswordResetConfirm, db: Session = Depends(get_mvp_db)):
    MVPAuthService.confirm_password_reset(db, req)
    return {"message": "Password successfully updated. Please log in with your new password."}
