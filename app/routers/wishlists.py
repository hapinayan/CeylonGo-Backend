"""
FastAPI Router for Module 04 — Traveler Eco-Tourism Wishlist / Favorites.

Endpoints:
- POST /api/v1/wishlists/eco/{id}   (Add approved & published eco experience to traveler's wishlist)
- DELETE /api/v1/wishlists/eco/{id} (Remove eco experience from traveler's wishlist)
- GET /api/v1/wishlists/me          (List authenticated traveler's saved eco experiences with pagination)
"""

import math
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.dependencies import require_traveler
from app.core.listing_sync import sync_homestay_listing_reference
from app.database.connection import get_db
from app.models.eco_experience import EcoExperience
from app.models.eco_wishlist import EcoWishlist
from app.models.homestay import Homestay
from app.models.shared import ListingReference, Wishlist
from app.models.user import User
from app.schemas.eco_experience import EcoExperiencePublicSummary
from app.schemas.eco_wishlist import (
    EcoWishlistItemResponse,
    EcoWishlistPagination,
)
from app.schemas.homestay import HomestayResponse

router = APIRouter(
    prefix="/api/v1/wishlists",
    tags=["Wishlists"],
)


@router.post(
    "/eco/{experience_id}",
    response_model=EcoWishlistItemResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add an approved eco experience to authenticated traveler's wishlist",
)
def add_to_eco_wishlist(
    experience_id: int,
    current_user: Annotated[User, Depends(require_traveler)],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Protected endpoint: Allows an authenticated traveler to save an approved, published
    eco experience to their personal wishlist.

    SECURITY & INTEGRITY RULES:
    1. Authenticated user must have 'traveler' role.
    2. Experience must exist and be in 'approved' status (draft/pending/rejected return 404
       to avoid leaking unapproved listings).
    3. Experience must be published (deactivated listings return 400).
    4. Duplicates return 409 Conflict.
    5. Database unique constraint prevents race conditions under concurrent calls.
    """
    experience = (
        db.query(EcoExperience)
        .options(
            selectinload(EcoExperience.provider_profile),
            selectinload(EcoExperience.verifications),
        )
        .filter(EcoExperience.id == experience_id)
        .first()
    )
    if not experience:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Eco experience not found",
        )

    # Never leak unapproved listings to travelers
    if experience.listing_status != "approved":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Eco experience not found",
        )

    # Deactivated listings cannot be newly added
    if not experience.is_published:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This eco experience is currently not published",
        )

    # Check for existing duplicate before insert
    existing = (
        db.query(EcoWishlist)
        .filter(
            EcoWishlist.user_id == current_user.id,
            EcoWishlist.eco_experience_id == experience.id,
        )
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Eco experience is already in your wishlist",
        )

    wishlist_item = EcoWishlist(
        user_id=current_user.id,
        eco_experience_id=experience.id,
    )
    db.add(wishlist_item)
    try:
        db.commit()
        db.refresh(wishlist_item)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Eco experience is already in your wishlist",
        ) from exc

    return EcoWishlistItemResponse(
        id=wishlist_item.id,
        user_id=wishlist_item.user_id,
        eco_experience_id=wishlist_item.eco_experience_id,
        created_at=wishlist_item.created_at,
        is_available=True,
        experience=EcoExperiencePublicSummary.model_validate(experience),
    )


@router.delete(
    "/eco/{experience_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove an eco experience from authenticated traveler's wishlist",
)
def remove_from_eco_wishlist(
    experience_id: int,
    current_user: Annotated[User, Depends(require_traveler)],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Protected endpoint: Removes an eco experience from the authenticated traveler's wishlist.
    Rejects requests if the item is not currently saved in the caller's wishlist.
    """
    item = (
        db.query(EcoWishlist)
        .filter(
            EcoWishlist.user_id == current_user.id,
            EcoWishlist.eco_experience_id == experience_id,
        )
        .first()
    )
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Eco experience not found in your wishlist",
        )

    db.delete(item)
    db.commit()
    return None


@router.get(
    "/me",
    response_model=EcoWishlistPagination,
    summary="List authenticated traveler's saved eco experiences",
)
def list_my_wishlist(
    current_user: Annotated[User, Depends(require_traveler)],
    db: Annotated[Session, Depends(get_db)],
    page: int = Query(default=1, ge=1, description="Page number"),
    page_size: int = Query(default=20, ge=1, le=100, description="Items per page"),
    available_only: bool = Query(
        default=False,
        description="Filter only currently approved and published experiences",
    ),
):
    """
    Protected endpoint: Returns a paginated list of the authenticated traveler's saved
    eco experiences ordered by most recently added.

    SAFETY & PRIVACY RULES:
    1. Only the authenticated traveler's own items are returned.
    2. If a saved experience was subsequently deactivated or unpublished by the provider:
       - If available_only=True: Excluded from results and total count.
       - If available_only=False: Included with is_available=False and experience=None,
         allowing the client UI to handle unavailable items gracefully without 500 crashes.
    3. Exposes only public summary data; never reveals private sustainability evidence or
       provider verification documents.
    """
    query = (
        db.query(EcoWishlist)
        .options(
            selectinload(EcoWishlist.eco_experience).selectinload(
                EcoExperience.provider_profile
            ),
            selectinload(EcoWishlist.eco_experience).selectinload(
                EcoExperience.verifications
            ),
        )
        .filter(EcoWishlist.user_id == current_user.id)
    )

    if available_only:
        query = query.join(
            EcoExperience, EcoWishlist.eco_experience_id == EcoExperience.id
        ).filter(
            EcoExperience.listing_status == "approved",
            EcoExperience.is_published == True,
        )

    total = query.count()
    total_pages = math.ceil(total / page_size) if total > 0 else 0

    entries = (
        query.order_by(EcoWishlist.created_at.desc(), EcoWishlist.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    items = []
    for entry in entries:
        exp = entry.eco_experience
        is_available = bool(exp and exp.is_publicly_visible)
        exp_summary = None
        if exp and is_available:
            exp_summary = EcoExperiencePublicSummary.model_validate(exp)

        items.append(
            EcoWishlistItemResponse(
                id=entry.id,
                user_id=entry.user_id,
                eco_experience_id=entry.eco_experience_id,
                created_at=entry.created_at,
                is_available=is_available,
                experience=exp_summary,
            )
        )

    return EcoWishlistPagination(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


# ==========================================
# HOMESTAY WISHLIST ENDPOINTS (MODULE 02 / PHASE 03)
# ==========================================

@router.post(
    "/homestays/{homestay_id}",
    status_code=status.HTTP_201_CREATED,
    summary="Add an approved homestay to authenticated traveler's wishlist",
)
def add_homestay_to_wishlist(
    homestay_id: int,
    current_user: Annotated[User, Depends(require_traveler)],
    db: Annotated[Session, Depends(get_db)],
):
    homestay = db.query(Homestay).filter(Homestay.id == homestay_id).first()
    if not homestay or not homestay.is_active or homestay.approval_status != "approved":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Homestay not found",
        )

    listing_ref = (
        db.query(ListingReference)
        .filter(
            ListingReference.module_type == "homestay",
            ListingReference.target_id == homestay.id,
        )
        .first()
    )
    if not listing_ref:
        listing_ref = sync_homestay_listing_reference(db, homestay)
        if not listing_ref:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Homestay cannot be saved at this time",
            )
        db.flush()

    existing = (
        db.query(Wishlist)
        .filter(
            Wishlist.user_id == current_user.id,
            Wishlist.listing_reference_id == listing_ref.id,
        )
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Homestay is already in your wishlist",
        )

    entry = Wishlist(
        user_id=current_user.id,
        listing_reference_id=listing_ref.id,
    )
    db.add(entry)
    db.commit()

    return {
        "message": "Homestay added to wishlist",
        "homestay_id": homestay_id,
        "saved": True,
    }


@router.delete(
    "/homestays/{homestay_id}",
    summary="Remove a homestay from authenticated traveler's wishlist",
)
def remove_homestay_from_wishlist(
    homestay_id: int,
    current_user: Annotated[User, Depends(require_traveler)],
    db: Annotated[Session, Depends(get_db)],
):
    listing_ref = (
        db.query(ListingReference)
        .filter(
            ListingReference.module_type == "homestay",
            ListingReference.target_id == homestay_id,
        )
        .first()
    )
    if not listing_ref:
        return {
            "message": "Homestay removed from wishlist",
            "homestay_id": homestay_id,
            "saved": False,
        }

    entry = (
        db.query(Wishlist)
        .filter(
            Wishlist.user_id == current_user.id,
            Wishlist.listing_reference_id == listing_ref.id,
        )
        .first()
    )
    if entry:
        db.delete(entry)
        db.commit()

    return {
        "message": "Homestay removed from wishlist",
        "homestay_id": homestay_id,
        "saved": False,
    }


@router.get(
    "/homestays",
    summary="List authenticated traveler's saved homestays with pagination",
)
def get_my_homestay_wishlist(
    current_user: Annotated[User, Depends(require_traveler)],
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=50)] = 20,
):
    query = (
        db.query(Wishlist, Homestay)
        .join(ListingReference, Wishlist.listing_reference_id == ListingReference.id)
        .join(Homestay, ListingReference.target_id == Homestay.id)
        .options(
            selectinload(Homestay.images),
            selectinload(Homestay.amenities),
        )
        .filter(
            Wishlist.user_id == current_user.id,
            ListingReference.module_type == "homestay",
        )
    )

    total = query.count()
    results = (
        query.order_by(Wishlist.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    items = []
    for w, h in results:
        items.append(
            {
                "wishlist_id": w.id,
                "saved_at": w.created_at,
                "homestay": HomestayResponse.model_validate(h),
            }
        )

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get(
    "/homestays/{homestay_id}/status",
    summary="Check whether a homestay is saved in traveler's wishlist",
)
def check_homestay_wishlist_status(
    homestay_id: int,
    current_user: Annotated[User, Depends(require_traveler)],
    db: Annotated[Session, Depends(get_db)],
):
    listing_ref = (
        db.query(ListingReference)
        .filter(
            ListingReference.module_type == "homestay",
            ListingReference.target_id == homestay_id,
        )
        .first()
    )
    if not listing_ref:
        return {"homestay_id": homestay_id, "saved": False}

    saved = (
        db.query(Wishlist)
        .filter(
            Wishlist.user_id == current_user.id,
            Wishlist.listing_reference_id == listing_ref.id,
        )
        .first()
        is not None
    )
    return {"homestay_id": homestay_id, "saved": saved}
