"""
CeylonGo Academic MVP — Community Feed Router
Endpoints for Community Posts, Comments, and Likes.
"""

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.mvp_security import get_current_mvp_user
from app.database.mvp_session import get_mvp_db
from app.models.mvp import MVPUser
from app.schemas.mvp import (
    CommentCreateRequest,
    CommentResponse,
    PostCreateRequest,
    PostResponse,
)
from app.services.mvp_services import MVPCommunityService

router = APIRouter(prefix="/community", tags=["MVP Community"])


@router.post("/posts", response_model=PostResponse, status_code=status.HTTP_201_CREATED)
def create_community_post(
    req: PostCreateRequest,
    current_user: MVPUser = Depends(get_current_mvp_user),
    db: Session = Depends(get_mvp_db),
):
    post = MVPCommunityService.create_post(db, current_user, req)
    return {
        "id": post.id,
        "author_id": post.author_id,
        "author_name": current_user.full_name,
        "body": post.body,
        "visibility": post.visibility,
        "created_at": post.created_at,
        "likes_count": 0,
        "comments_count": 0,
    }


@router.get("/posts", response_model=list[PostResponse])
def get_community_feed(
    limit: int = Query(30, le=50),
    db: Session = Depends(get_mvp_db),
):
    return MVPCommunityService.list_posts(db, limit)


@router.post("/posts/{post_id}/comments", response_model=CommentResponse, status_code=status.HTTP_201_CREATED)
def create_comment(
    post_id: int,
    req: CommentCreateRequest,
    current_user: MVPUser = Depends(get_current_mvp_user),
    db: Session = Depends(get_mvp_db),
):
    comment = MVPCommunityService.create_comment(db, current_user, post_id, req)
    return {
        "id": comment.id,
        "post_id": comment.post_id,
        "author_id": comment.author_id,
        "author_name": current_user.full_name,
        "body": comment.body,
        "visibility": comment.visibility,
        "created_at": comment.created_at,
    }


@router.post("/posts/{post_id}/like")
def toggle_post_like(
    post_id: int,
    current_user: MVPUser = Depends(get_current_mvp_user),
    db: Session = Depends(get_mvp_db),
):
    res = MVPCommunityService.toggle_like(db, current_user, post_id)
    return res
