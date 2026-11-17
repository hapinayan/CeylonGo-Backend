
from datetime import date
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    status,
)
from sqlalchemy.orm import Session

from app.core.homestay_permissions import (
    check_homestay_ownership,
    require_homestay_manager,
)
from app.database.connection import get_db
from app.models.homestay import Homestay
from app.models.promotion import HomestayPromotion
from app.models.provider_profile import ProviderProfile
from app.models.user import User
from app.schemas.promotion import (
    PromotionCreateRequest,
    PromotionListResponse,
    HomestayPromotionResponse,
    PromotionUpdateRequest,
)


router = APIRouter(
    prefix="/api/v1/homestay-promotions",
    tags=["Homestay Promotions"],
)


@router.post(
    "",
    response_model=HomestayPromotionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_promotion(
    request: PromotionCreateRequest,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[
        User,
        Depends(require_homestay_manager),
    ],
):
    homestay = (
        db.query(Homestay)
        .filter(Homestay.id == request.homestay_id)
        .first()
    )

    if homestay is None:
        raise HTTPException(
            status_code=404,
            detail="Homestay not found",
        )

    profile = None
    if current_user.role != "admin" and (
        homestay.provider_profile_id is not None
        or current_user.role == "provider"
    ):
        try:
            profile = (
                db.query(ProviderProfile)
                .filter(ProviderProfile.user_id == current_user.id)
                .first()
            )
        except Exception:
            profile = None

    if not check_homestay_ownership(homestay, current_user, profile):
        raise HTTPException(
            status_code=403,
            detail="You cannot manage this homestay",
        )

    promotion = HomestayPromotion(
        homestay_id=homestay.id,
        title=request.title,
        description=request.description,
        discount_percent=request.discount_percent,
        start_date=request.start_date,
        end_date=request.end_date,
        is_active=True,
    )

    try:
        db.add(promotion)
        db.commit()
    except Exception:
        db.rollback()
        raise

    db.refresh(promotion)

    return promotion


@router.put(
    "/{promotion_id}",
    response_model=HomestayPromotionResponse,
)
def update_promotion(
    promotion_id: int,
    request: PromotionUpdateRequest,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[
        User,
        Depends(require_homestay_manager),
    ],
):
    promotion = (
        db.query(HomestayPromotion)
        .filter(HomestayPromotion.id == promotion_id)
        .first()
    )

    if promotion is None:
        raise HTTPException(
            status_code=404,
            detail="Promotion not found",
        )

    homestay = (
        db.query(Homestay)
        .filter(Homestay.id == promotion.homestay_id)
        .first()
    )

    if homestay is None:
        raise HTTPException(
            status_code=404,
            detail="Homestay not found",
        )

    profile = None
    if current_user.role != "admin" and (
        homestay.provider_profile_id is not None
        or current_user.role == "provider"
    ):
        try:
            profile = (
                db.query(ProviderProfile)
                .filter(ProviderProfile.user_id == current_user.id)
                .first()
            )
        except Exception:
            profile = None

    if not check_homestay_ownership(homestay, current_user, profile):
        raise HTTPException(
            status_code=403,
            detail="You cannot manage this promotion",
        )

    promotion.title = request.title
    promotion.description = request.description
    promotion.discount_percent = request.discount_percent
    promotion.start_date = request.start_date
    promotion.end_date = request.end_date
    promotion.is_active = request.is_active

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise

    db.refresh(promotion)

    return promotion


@router.get(
    "",
    response_model=PromotionListResponse,
)
def list_active_promotions(
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[
        int,
        Query(ge=1, le=50),
    ] = 10,
    homestay_id: Annotated[
        int | None,
        Query(gt=0),
    ] = None,
):
    today = date.today()

    query = (
        db.query(HomestayPromotion)
        .join(
            Homestay,
            Homestay.id == HomestayPromotion.homestay_id,
        )
        .filter(
            HomestayPromotion.is_active == True,
            HomestayPromotion.start_date <= today,
            HomestayPromotion.end_date >= today,
            Homestay.is_active == True,
        )
    )

    # Optional filter for a specific homestay.
    if homestay_id is not None:
        query = query.filter(
            HomestayPromotion.homestay_id == homestay_id
        )

    total = query.count()

    promotions = (
        query.order_by(HomestayPromotion.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    return PromotionListResponse(
        items=promotions,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/mine",
    response_model=PromotionListResponse,
)
def list_my_promotions(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[
        User,
        Depends(require_homestay_manager),
    ],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[
        int,
        Query(ge=1, le=50),
    ] = 10,
):
    profile = (
        db.query(ProviderProfile)
        .filter(ProviderProfile.user_id == current_user.id)
        .first()
    )

    if profile is not None:
        query = (
            db.query(HomestayPromotion)
            .join(
                Homestay,
                Homestay.id == HomestayPromotion.homestay_id,
            )
            .filter(
                (Homestay.provider_profile_id == profile.id)
                | (
                    (Homestay.provider_profile_id == None)
                    & (Homestay.owner_id == current_user.id)
                )
            )
        )
    else:
        query = (
            db.query(HomestayPromotion)
            .join(
                Homestay,
                Homestay.id == HomestayPromotion.homestay_id,
            )
            .filter(
                Homestay.owner_id == current_user.id
            )
        )

    total = query.count()

    promotions = (
        query.order_by(HomestayPromotion.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    return PromotionListResponse(
        items=promotions,
        total=total,
        page=page,
        page_size=page_size,
    )


# OWNER / ADMIN: Soft delete (deactivate) homestay promotion
@router.delete(
    "/{promotion_id}",
    summary="Soft delete (deactivate) a homestay promotion",
)
def delete_promotion(
    promotion_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[
        User,
        Depends(require_homestay_manager),
    ],
):
    promotion = (
        db.query(HomestayPromotion)
        .filter(HomestayPromotion.id == promotion_id)
        .first()
    )
    if not promotion:
        raise HTTPException(
            status_code=404,
            detail="Promotion not found",
        )

    homestay = (
        db.query(Homestay)
        .filter(Homestay.id == promotion.homestay_id)
        .first()
    )
    if not homestay:
        raise HTTPException(
            status_code=404,
            detail="Homestay not found",
        )

    profile = None
    if current_user.role != "admin" and (
        homestay.provider_profile_id is not None
        or current_user.role == "provider"
    ):
        try:
            profile = (
                db.query(ProviderProfile)
                .filter(ProviderProfile.user_id == current_user.id)
                .first()
            )
        except Exception:
            profile = None

    if not check_homestay_ownership(homestay, current_user, profile):
        raise HTTPException(
            status_code=403,
            detail="You cannot manage this promotion",
        )

    promotion.is_active = False
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise

    return {
        "message": "Homestay promotion deactivated successfully",
        "id": promotion_id,
        "is_active": False,
    }
