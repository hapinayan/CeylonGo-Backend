import math
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.dependencies import require_approved_provider
from app.database.connection import get_db
from app.models.community import CommunityPost
from app.models.provider_profile import ProviderProfile
from app.schemas.community import CommunityPostCreate
from app.schemas.provider_community import (
    ProviderCommunityPostCreate,
    ProviderCommunityPostListResponse,
    ProviderCommunityPostResponse,
)
from app.services.community_moderation import enforce_community_not_suspended
from app.services.community_posts import create_community_post, delete_community_post


router = APIRouter(
    prefix="/api/v1/providers/me/community",
    tags=["Provider Community Integration"],
)


@router.post(
    "/posts",
    response_model=ProviderCommunityPostResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create provider community update or story",
)
def create_provider_community_post(
    payload: ProviderCommunityPostCreate,
    provider_profile: Annotated[ProviderProfile, Depends(require_approved_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Publishes a cultural, activity, homestay, or tourism update to the community feed.
    Enforces approved provider status and attributes the post to the provider user.
    """
    user = enforce_community_not_suspended(db, provider_profile.user)
    if not payload.content.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Post caption cannot be empty.",
        )
    post = create_community_post(
        db,
        current_user=user,
        payload=CommunityPostCreate(
            content=payload.content,
            location_tag=payload.location_tag,
            district=payload.district or provider_profile.district,
        ),
    )

    return ProviderCommunityPostResponse(
        id=post.id,
        author_id=user.id,
        author_name=user.full_name,
        business_name=provider_profile.business_name,
        content=post.content,
        location_tag=post.location_tag,
        district=post.district,
        likes_count=post.likes_count,
        comments_count=post.comments_count,
        is_published=post.is_published,
        created_at=post.created_at,
    )


@router.get(
    "/posts",
    response_model=ProviderCommunityPostListResponse,
    summary="List provider's community posts",
)
def list_provider_community_posts(
    provider_profile: Annotated[ProviderProfile, Depends(require_approved_provider)],
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
):
    """
    Lists community posts authored by the authenticated provider.
    """
    user = provider_profile.user
    query = db.query(CommunityPost).filter(CommunityPost.author_id == user.id)

    total = query.count()
    total_pages = max(1, math.ceil(total / page_size))

    posts = (
        query.order_by(CommunityPost.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    items = [
        ProviderCommunityPostResponse(
            id=p.id,
            author_id=user.id,
            author_name=user.full_name,
            business_name=provider_profile.business_name,
            content=p.content,
            location_tag=p.location_tag,
            district=p.district,
            likes_count=p.likes_count,
            comments_count=p.comments_count,
            is_published=p.is_published,
            created_at=p.created_at,
        )
        for p in posts
    ]

    return ProviderCommunityPostListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.delete(
    "/posts/{post_id}",
    summary="Delete provider's community post",
)
def delete_provider_community_post(
    post_id: int,
    provider_profile: Annotated[ProviderProfile, Depends(require_approved_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Deletes a community post owned by the authenticated provider.
    """
    user = enforce_community_not_suspended(db, provider_profile.user)
    post = (
        db.query(CommunityPost)
        .filter(CommunityPost.id == post_id, CommunityPost.author_id == user.id)
        .first()
    )
    if not post:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Community post not found or not authored by you",
        )

    delete_community_post(db, post_id=post.id, current_user=user)
    return {"message": "Community post deleted successfully", "id": post_id}

