from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.dependencies import require_approved_provider
from app.database.connection import get_db
from app.models.provider_profile import ProviderProfile
from app.models.tour_guide import TourGuide
from app.schemas.tour_guide import (
    TourGuideCreate,
    TourGuideResponse,
    TourGuideUpdate,
)


router = APIRouter(
    prefix="/api/v1/provider/tour-guides",
    tags=["Tour Guide Provider"],
)


def require_tour_guide_provider(
    provider_profile: Annotated[
        ProviderProfile,
        Depends(require_approved_provider),
    ],
) -> ProviderProfile:
    """
    Allow only approved Tour Guide providers.
    """

    if provider_profile.provider_type != "tour_guide":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only approved Tour Guide providers can access this endpoint",
        )

    return provider_profile


GuideProvider = Annotated[ProviderProfile, Depends(require_tour_guide_provider)]
DatabaseSession = Annotated[Session, Depends(get_db)]


def find_owned_guide(db: Session, provider_id: int) -> TourGuide | None:
    return db.query(TourGuide).filter(TourGuide.provider_profile_id == provider_id).first()


def require_owned_guide(provider: GuideProvider, db: DatabaseSession) -> TourGuide:
    guide = find_owned_guide(db, provider.id)
    if guide is None:
        raise HTTPException(status_code=404, detail="Tour guide profile not found")
    return guide


OwnedGuide = Annotated[TourGuide, Depends(require_owned_guide)]


def commit_changes(db: Session) -> None:
    try:
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise


@router.post(
    "",
    response_model=TourGuideResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a draft tour guide profile",
)
def create_tour_guide(payload: TourGuideCreate, provider: GuideProvider, db: DatabaseSession):
    if find_owned_guide(db, provider.id) is not None:
        raise HTTPException(status_code=409, detail="Tour guide profile already exists")

    guide = TourGuide(
        **payload.model_dump(),
        provider_profile_id=provider.id,
        approval_status="draft",
        is_active=False,
    )
    db.add(guide)
    try:
        commit_changes(db)
    except IntegrityError as exc:
        if find_owned_guide(db, provider.id) is not None:
            raise HTTPException(status_code=409, detail="Tour guide profile already exists") from exc
        raise
    return guide


@router.get("", response_model=TourGuideResponse, summary="Get your tour guide profile")
def get_tour_guide(guide: OwnedGuide):
    return guide


@router.patch("", response_model=TourGuideResponse, summary="Update your tour guide profile")
def update_tour_guide(payload: TourGuideUpdate, guide: OwnedGuide, db: DatabaseSession):
    changes = payload.model_dump(exclude_unset=True)
    latitude = changes.get("base_latitude", guide.base_latitude)
    longitude = changes.get("base_longitude", guide.base_longitude)
    if (latitude is None) != (longitude is None):
        raise HTTPException(status_code=422, detail="Latitude and longitude must be provided together")

    if "base_latitude" in changes or "base_longitude" in changes:
        # A partial PATCH depends on the coordinate pair read by this request.
        try:
            updated = (
                db.query(TourGuide)
                .filter(
                    TourGuide.id == guide.id,
                    TourGuide.base_latitude == guide.base_latitude,
                    TourGuide.base_longitude == guide.base_longitude,
                )
                .update(changes, synchronize_session=False)
            )
        except SQLAlchemyError:
            db.rollback()
            raise
        if updated == 0:
            db.rollback()
            raise HTTPException(
                status_code=409, detail="Tour guide coordinates changed. Retry the update.",
            )
        commit_changes(db)
        db.expire(guide)
        return guide

    for field, value in changes.items():
        setattr(guide, field, value)
    commit_changes(db)
    return guide


@router.delete("", status_code=status.HTTP_204_NO_CONTENT, summary="Deactivate your tour guide profile")
def deactivate_tour_guide(guide: OwnedGuide, db: DatabaseSession):
    guide.is_active = False
    commit_changes(db)
