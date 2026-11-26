"""
CeylonGo Academic MVP — Community Feed & Social Interactions Router
Supports public feed discovery, post/photo management, likes, comments, saved posts, and reports.
"""

from typing import Literal

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.mvp_security import get_current_mvp_user, get_optional_mvp_user
from app.database.mvp_session import get_mvp_db
from app.models.mvp import MVPUser
from app.schemas.mvp_community import (
    CommunityCommentCreateRequest,
    CommunityCommentDetails,
    CommunityCommentUpdateRequest,
    CommunityLikeStatus,
    CommunityPage,
    CommunityPostCreateRequest,
    CommunityPostDetails,
    CommunityPostMediaItem,
    CommunityPostUpdateRequest,
    CommunityReportCreateRequest,
    CommunityReportDetails,
    CommunitySaveStatus,
)
from app.services import mvp_community as service

router = APIRouter(prefix="/community", tags=["MVP Community"])


@router.get("/posts", response_model=CommunityPage[CommunityPostDetails])
@router.get("/feed", response_model=CommunityPage[CommunityPostDetails])
def get_community_feed(
    search: str | None = Query(None, max_length=150),
    author_id: int | None = Query(None, ge=1),
    author_type: Literal["traveler", "provider"] | None = None,
    page: int = Query(1, ge=1, le=100000),
    page_size: int = Query(20, ge=1, le=50),
    viewer: MVPUser | None = Depends(get_optional_mvp_user),
    db: Session = Depends(get_mvp_db),
):
    return service.list_public_posts(
        db,
        viewer=viewer,
        search=search,
        author_id=author_id,
        author_type=author_type,
        page=page,
        page_size=page_size,
    )


@router.get("/posts/me", response_model=CommunityPage[CommunityPostDetails])
def list_my_community_posts(
    page: int = Query(1, ge=1, le=100000),
    page_size: int = Query(20, ge=1, le=50),
    current_user: MVPUser = Depends(get_current_mvp_user),
    db: Session = Depends(get_mvp_db),
):
    return service.list_own_posts(db, user=current_user, page=page, page_size=page_size)


@router.post("/posts", response_model=CommunityPostDetails, status_code=status.HTTP_201_CREATED)
def create_community_post(
    req: CommunityPostCreateRequest,
    current_user: MVPUser = Depends(get_current_mvp_user),
    db: Session = Depends(get_mvp_db),
):
    return service.create_post(db, author=current_user, req=req)


@router.get("/posts/{post_id}", response_model=CommunityPostDetails)
def get_community_post(
    post_id: int,
    viewer: MVPUser | None = Depends(get_optional_mvp_user),
    db: Session = Depends(get_mvp_db),
):
    return service.get_post_details(db, post_id=post_id, viewer=viewer)


@router.patch("/posts/{post_id}", response_model=CommunityPostDetails)
@router.put("/posts/{post_id}", response_model=CommunityPostDetails)
def update_community_post(
    post_id: int,
    req: CommunityPostUpdateRequest,
    current_user: MVPUser = Depends(get_current_mvp_user),
    db: Session = Depends(get_mvp_db),
):
    return service.update_own_post(db, post_id=post_id, user=current_user, req=req)


@router.delete("/posts/{post_id}")
def delete_community_post(
    post_id: int,
    current_user: MVPUser = Depends(get_current_mvp_user),
    db: Session = Depends(get_mvp_db),
):
    return service.delete_post(db, post_id=post_id, user=current_user)


@router.post("/posts/{post_id}/media", response_model=CommunityPostMediaItem, status_code=status.HTTP_201_CREATED)
async def upload_post_photo(
    post_id: int,
    file: UploadFile = File(...),
    alt_text: str | None = Form(None),
    current_user: MVPUser = Depends(get_current_mvp_user),
    db: Session = Depends(get_mvp_db),
):
    return await service.upload_post_photo(db, post_id=post_id, user=current_user, file=file, alt_text=alt_text)


@router.get("/posts/{post_id}/media/{media_id}")
def serve_post_photo(
    post_id: int,
    media_id: int,
    viewer: MVPUser | None = Depends(get_optional_mvp_user),
    db: Session = Depends(get_mvp_db),
):
    file_path, content_type = service.get_post_photo_file(db, post_id=post_id, media_id=media_id, viewer=viewer)
    return FileResponse(
        path=str(file_path),
        media_type=content_type,
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=3600"},
    )


@router.delete("/posts/{post_id}/media/{media_id}")
def delete_post_photo(
    post_id: int,
    media_id: int,
    current_user: MVPUser = Depends(get_current_mvp_user),
    db: Session = Depends(get_mvp_db),
):
    return service.delete_post_photo(db, post_id=post_id, media_id=media_id, user=current_user)


@router.post("/posts/{post_id}/like", response_model=CommunityLikeStatus)
def toggle_post_like(
    post_id: int,
    current_user: MVPUser = Depends(get_current_mvp_user),
    db: Session = Depends(get_mvp_db),
):
    return service.toggle_like(db, post_id=post_id, user=current_user)


@router.put("/posts/{post_id}/like", response_model=CommunityLikeStatus)
def like_post(
    post_id: int,
    current_user: MVPUser = Depends(get_current_mvp_user),
    db: Session = Depends(get_mvp_db),
):
    return service.like_post(db, post_id=post_id, user=current_user)


@router.delete("/posts/{post_id}/like", response_model=CommunityLikeStatus)
def unlike_post(
    post_id: int,
    current_user: MVPUser = Depends(get_current_mvp_user),
    db: Session = Depends(get_mvp_db),
):
    return service.unlike_post(db, post_id=post_id, user=current_user)


@router.get("/posts/{post_id}/comments", response_model=CommunityPage[CommunityCommentDetails])
def list_post_comments(
    post_id: int,
    page: int = Query(1, ge=1, le=100000),
    page_size: int = Query(20, ge=1, le=50),
    viewer: MVPUser | None = Depends(get_optional_mvp_user),
    db: Session = Depends(get_mvp_db),
):
    return service.list_comments(db, post_id=post_id, viewer=viewer, page=page, page_size=page_size)


@router.post("/posts/{post_id}/comments", response_model=CommunityCommentDetails, status_code=status.HTTP_201_CREATED)
def create_comment(
    post_id: int,
    req: CommunityCommentCreateRequest,
    current_user: MVPUser = Depends(get_current_mvp_user),
    db: Session = Depends(get_mvp_db),
):
    return service.create_comment(db, post_id=post_id, author=current_user, req=req)


@router.patch("/comments/{comment_id}", response_model=CommunityCommentDetails)
def update_comment(
    comment_id: int,
    req: CommunityCommentUpdateRequest,
    current_user: MVPUser = Depends(get_current_mvp_user),
    db: Session = Depends(get_mvp_db),
):
    return service.update_comment(db, comment_id=comment_id, user=current_user, req=req)


@router.delete("/comments/{comment_id}")
def delete_comment(
    comment_id: int,
    current_user: MVPUser = Depends(get_current_mvp_user),
    db: Session = Depends(get_mvp_db),
):
    return service.delete_comment(db, comment_id=comment_id, user=current_user)


@router.put("/posts/{post_id}/save", response_model=CommunitySaveStatus)
def save_post(
    post_id: int,
    current_user: MVPUser = Depends(get_current_mvp_user),
    db: Session = Depends(get_mvp_db),
):
    return service.save_post(db, post_id=post_id, user=current_user)


@router.delete("/posts/{post_id}/save", response_model=CommunitySaveStatus)
def unsave_post(
    post_id: int,
    current_user: MVPUser = Depends(get_current_mvp_user),
    db: Session = Depends(get_mvp_db),
):
    return service.unsave_post(db, post_id=post_id, user=current_user)


@router.get("/saved", response_model=CommunityPage[CommunityPostDetails])
def list_saved_posts(
    page: int = Query(1, ge=1, le=100000),
    page_size: int = Query(20, ge=1, le=50),
    current_user: MVPUser = Depends(get_current_mvp_user),
    db: Session = Depends(get_mvp_db),
):
    return service.list_saved_posts(db, user=current_user, page=page, page_size=page_size)


@router.post("/reports", response_model=CommunityReportDetails, status_code=status.HTTP_201_CREATED)
def submit_community_report(
    req: CommunityReportCreateRequest,
    current_user: MVPUser = Depends(get_current_mvp_user),
    db: Session = Depends(get_mvp_db),
):
    return service.submit_report(db, reporter=current_user, req=req)
