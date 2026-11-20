"""FastAPI routers for Travel Community & Social Feed — Phases 1–5 (Posts, Feed, Engagement, Profiles, Provider Promotions, Reporting & Admin Moderation)."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.dependencies import require_admin, require_approved_provider
from app.database.connection import get_db
from app.models.provider_profile import ProviderProfile
from app.models.user import User
from app.routers.auth import get_current_user, get_optional_current_user
from app.schemas.community import (
    AdminCommunityActivityResponse,
    AdminCommunityCommentModerationResponse,
    AdminCommunityHashtagListResponse,
    AdminCommunityHashtagModerationResponse,
    AdminCommunityHashtagUpdate,
    AdminCommunityPostModerationRequest,
    AdminCommunityPostModerationResponse,
    AdminCommunityProfileSuspensionRequest,
    AdminCommunityProfileSuspensionResponse,
    AdminCommunityReportListResponse,
    AdminCommunityReportReviewResponse,
    AdminCommunityReportUpdate,
    CommunityCommentCreate,
    CommunityCommentDeleteResponse,
    CommunityCommentListResponse,
    CommunityCommentResponse,
    CommunityCommentUpdate,
    CommunityFeedResponse,
    CommunityFollowListResponse,
    CommunityFollowResponse,
    CommunityLikeResponse,
    CommunityMediaDeleteResponse,
    CommunityPostCreate,
    CommunityPostDeleteResponse,
    CommunityPostMediaResponse,
    CommunityPostResponse,
    CommunityPostUpdate,
    CommunityProfileResponse,
    CommunityReportCreate,
    CommunityReportResponse,
    CommunitySaveResponse,
    CommunitySavedPostsResponse,
    CommunitySearchResponse,
    CommunityTrendingHashtagsResponse,
    ProviderCommunityEngagementResponse,
    ProviderEligibleListingsResponse,
    ReportEntityType,
    ReportStatusType,
)
from app.services.community_engagement import (
    create_post_comment,
    delete_community_comment,
    like_community_post,
    list_post_comments,
    list_saved_community_posts,
    save_community_post,
    unlike_community_post,
    unsave_community_post,
    update_community_comment,
)
from app.services.community_feed import (
    get_community_feed,
    get_trending_hashtags,
    search_community,
)
from app.services.community_moderation import (
    enforce_community_not_suspended,
    get_admin_community_activity,
    list_admin_community_hashtags,
    list_admin_community_reports,
    moderate_admin_community_hashtag,
    moderate_admin_community_post,
    moderate_admin_community_profile_suspension,
    moderate_admin_delete_comment,
    review_admin_community_report,
    submit_community_report,
)
from app.services.community_posts import (
    create_community_post,
    delete_community_post,
    delete_community_post_media,
    get_authorized_community_media_file,
    get_community_post,
    update_community_post,
    upload_community_post_photo,
)
from app.services.community_profiles import (
    follow_community_user,
    get_community_profile,
    get_provider_community_engagement,
    list_profile_followers,
    list_profile_following,
    list_provider_eligible_listings,
    unfollow_community_user,
)

router = APIRouter(
    prefix="/api/v1/community",
    tags=["Travel Community"],
)

uploads_router = APIRouter(
    tags=["Travel Community Media"],
)


def require_unsuspended_community_user(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    """
    Dependency enforcing community-only user suspension (dbo.community_user_profiles)
    on all community user write endpoints after authentication.
    """
    return enforce_community_not_suspended(db, current_user)


# =============================================================================
# PUBLIC FEED, SEARCH & TRENDING HASHTAGS (PHASE 2)
# =============================================================================

@router.get(
    "/feed",
    response_model=CommunityFeedResponse,
    summary="Browse paginated public Travel Community feed",
)
def get_feed_endpoint(
    db: Annotated[Session, Depends(get_db)],
    viewer: Annotated[User | None, Depends(get_optional_current_user)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=50)] = 20,
    district: Annotated[str | None, Query(max_length=100)] = None,
    author_id: Annotated[int | None, Query(ge=1)] = None,
    hashtag: Annotated[str | None, Query(max_length=101)] = None,
    following_only: bool = False,
    featured_only: bool = False,
    promotional_only: bool = False,
) -> CommunityFeedResponse:
    return get_community_feed(
        db,
        viewer=viewer,
        page=page,
        page_size=page_size,
        district=district,
        author_id=author_id,
        hashtag=hashtag,
        following_only=following_only,
        featured_only=featured_only,
        promotional_only=promotional_only,
    )


@router.get(
    "/search",
    response_model=CommunitySearchResponse,
    summary="Search published community posts, active people, and hashtags",
)
def search_community_endpoint(
    db: Annotated[Session, Depends(get_db)],
    q: Annotated[str, Query(min_length=1, max_length=100)],
    viewer: Annotated[User | None, Depends(get_optional_current_user)] = None,
    type: Annotated[
        Literal["all", "posts", "people", "hashtags"],
        Query(alias="type"),
    ] = "all",
    district: Annotated[str | None, Query(max_length=100)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=50)] = 20,
) -> CommunitySearchResponse:
    return search_community(
        db,
        q=q,
        search_type=type,
        district=district,
        page=page,
        page_size=page_size,
        viewer=viewer,
    )


@router.get(
    "/hashtags/trending",
    response_model=CommunityTrendingHashtagsResponse,
    summary="Get trending Travel Community hashtags ordered by usage count",
)
def trending_hashtags_endpoint(
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> CommunityTrendingHashtagsResponse:
    return get_trending_hashtags(db, limit=limit)


# =============================================================================
# POST CRUD ENDPOINTS
# =============================================================================

@router.post(
    "/posts",
    response_model=CommunityPostResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new Travel Community post",
)
def create_post_endpoint(
    payload: CommunityPostCreate,
    current_user: Annotated[User, Depends(require_unsuspended_community_user)],
    db: Annotated[Session, Depends(get_db)],
) -> CommunityPostResponse:
    return create_community_post(db, current_user=current_user, payload=payload)


@router.get(
    "/posts/{post_id}",
    response_model=CommunityPostResponse,
    summary="Get Travel Community post details",
)
def get_post_endpoint(
    post_id: int,
    db: Annotated[Session, Depends(get_db)],
    viewer: Annotated[User | None, Depends(get_optional_current_user)] = None,
) -> CommunityPostResponse:
    return get_community_post(db, post_id=post_id, viewer=viewer)


@router.patch(
    "/posts/{post_id}",
    response_model=CommunityPostResponse,
    summary="Update an existing Travel Community post",
)
def update_post_endpoint(
    post_id: int,
    payload: CommunityPostUpdate,
    current_user: Annotated[User, Depends(require_unsuspended_community_user)],
    db: Annotated[Session, Depends(get_db)],
) -> CommunityPostResponse:
    return update_community_post(
        db, post_id=post_id, current_user=current_user, payload=payload
    )


@router.delete(
    "/posts/{post_id}",
    response_model=CommunityPostDeleteResponse,
    summary="Delete a Travel Community post safely",
)
def delete_post_endpoint(
    post_id: int,
    current_user: Annotated[User, Depends(require_unsuspended_community_user)],
    db: Annotated[Session, Depends(get_db)],
) -> CommunityPostDeleteResponse:
    return delete_community_post(db, post_id=post_id, current_user=current_user)


# =============================================================================
# SECURE PHOTO UPLOAD & SERVING ENDPOINTS
# =============================================================================

@router.post(
    "/posts/{post_id}/media",
    response_model=CommunityPostMediaResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a validated, EXIF-stripped photo for a Travel Community post",
)
async def upload_post_media_endpoint(
    post_id: int,
    current_user: Annotated[User, Depends(require_unsuspended_community_user)],
    db: Annotated[Session, Depends(get_db)],
    file: Annotated[UploadFile, File(...)],
    display_order: Annotated[int | None, Form()] = None,
) -> CommunityPostMediaResponse:
    return await upload_community_post_photo(
        db,
        post_id=post_id,
        current_user=current_user,
        file=file,
        display_order=display_order,
    )


@router.delete(
    "/posts/{post_id}/media/{media_id}",
    response_model=CommunityMediaDeleteResponse,
    summary="Remove a photo from a Travel Community post",
)
def delete_post_media_endpoint(
    post_id: int,
    media_id: int,
    current_user: Annotated[User, Depends(require_unsuspended_community_user)],
    db: Annotated[Session, Depends(get_db)],
) -> CommunityMediaDeleteResponse:
    return delete_community_post_media(
        db,
        post_id=post_id,
        media_id=media_id,
        current_user=current_user,
    )


# =============================================================================
# SOCIAL ENGAGEMENT: LIKES, COMMENTS & PRIVATE SAVED POSTS (PHASE 3)
# =============================================================================

@router.put(
    "/posts/{post_id}/like",
    response_model=CommunityLikeResponse,
    summary="Like a published Travel Community post (idempotent)",
)
def like_post_endpoint(
    post_id: int,
    current_user: Annotated[User, Depends(require_unsuspended_community_user)],
    db: Annotated[Session, Depends(get_db)],
) -> CommunityLikeResponse:
    return like_community_post(db, post_id=post_id, current_user=current_user)


@router.delete(
    "/posts/{post_id}/like",
    response_model=CommunityLikeResponse,
    summary="Unlike a published Travel Community post (idempotent)",
)
def unlike_post_endpoint(
    post_id: int,
    current_user: Annotated[User, Depends(require_unsuspended_community_user)],
    db: Annotated[Session, Depends(get_db)],
) -> CommunityLikeResponse:
    return unlike_community_post(db, post_id=post_id, current_user=current_user)


@router.get(
    "/posts/{post_id}/comments",
    response_model=CommunityCommentListResponse,
    summary="List paginated threaded comments for a Travel Community post",
)
def list_comments_endpoint(
    post_id: int,
    db: Annotated[Session, Depends(get_db)],
    viewer: Annotated[User | None, Depends(get_optional_current_user)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=50)] = 20,
    parent_comment_id: Annotated[int | None, Query(ge=1)] = None,
) -> CommunityCommentListResponse:
    return list_post_comments(
        db,
        post_id=post_id,
        viewer=viewer,
        page=page,
        page_size=page_size,
        parent_comment_id=parent_comment_id,
    )


@router.post(
    "/posts/{post_id}/comments",
    response_model=CommunityCommentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a comment or threaded reply on a published Travel Community post",
)
def create_comment_endpoint(
    post_id: int,
    payload: CommunityCommentCreate,
    current_user: Annotated[User, Depends(require_unsuspended_community_user)],
    db: Annotated[Session, Depends(get_db)],
) -> CommunityCommentResponse:
    return create_post_comment(
        db, post_id=post_id, current_user=current_user, payload=payload
    )


@router.patch(
    "/comments/{comment_id}",
    response_model=CommunityCommentResponse,
    summary="Edit own Travel Community comment",
)
def update_comment_endpoint(
    comment_id: int,
    payload: CommunityCommentUpdate,
    current_user: Annotated[User, Depends(require_unsuspended_community_user)],
    db: Annotated[Session, Depends(get_db)],
) -> CommunityCommentResponse:
    return update_community_comment(
        db, comment_id=comment_id, current_user=current_user, payload=payload
    )


@router.delete(
    "/comments/{comment_id}",
    response_model=CommunityCommentDeleteResponse,
    summary="Delete own comment (or moderate as admin) safely preserving reply threads",
)
def delete_comment_endpoint(
    comment_id: int,
    current_user: Annotated[User, Depends(require_unsuspended_community_user)],
    db: Annotated[Session, Depends(get_db)],
) -> CommunityCommentDeleteResponse:
    return delete_community_comment(
        db, comment_id=comment_id, current_user=current_user
    )


@router.put(
    "/posts/{post_id}/save",
    response_model=CommunitySaveResponse,
    summary="Bookmark a published Travel Community post privately (idempotent)",
)
def save_post_endpoint(
    post_id: int,
    current_user: Annotated[User, Depends(require_unsuspended_community_user)],
    db: Annotated[Session, Depends(get_db)],
) -> CommunitySaveResponse:
    return save_community_post(db, post_id=post_id, current_user=current_user)


@router.delete(
    "/posts/{post_id}/save",
    response_model=CommunitySaveResponse,
    summary="Remove a post from private bookmarks (idempotent)",
)
def unsave_post_endpoint(
    post_id: int,
    current_user: Annotated[User, Depends(require_unsuspended_community_user)],
    db: Annotated[Session, Depends(get_db)],
) -> CommunitySaveResponse:
    return unsave_community_post(db, post_id=post_id, current_user=current_user)


@router.get(
    "/saved",
    response_model=CommunitySavedPostsResponse,
    summary="List authenticated caller's private saved community posts",
)
def list_saved_posts_endpoint(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=50)] = 20,
) -> CommunitySavedPostsResponse:
    return list_saved_community_posts(
        db, current_user=current_user, page=page, page_size=page_size
    )


# =============================================================================
# PUBLIC PROFILES, FOLLOWS & APPROVED PROVIDER PROMOTIONS (PHASE 4)
# =============================================================================

@router.get(
    "/profiles/{user_id}",
    response_model=CommunityProfileResponse,
    summary="View public Travel Community profile and published posts",
)
def get_profile_endpoint(
    user_id: int,
    db: Annotated[Session, Depends(get_db)],
    viewer: Annotated[User | None, Depends(get_optional_current_user)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=50)] = 20,
) -> CommunityProfileResponse:
    return get_community_profile(
        db, user_id=user_id, viewer=viewer, page=page, page_size=page_size
    )


@router.put(
    "/profiles/{user_id}/follow",
    response_model=CommunityFollowResponse,
    summary="Follow an active Travel Community user (idempotent)",
)
def follow_user_endpoint(
    user_id: int,
    current_user: Annotated[User, Depends(require_unsuspended_community_user)],
    db: Annotated[Session, Depends(get_db)],
) -> CommunityFollowResponse:
    return follow_community_user(
        db, target_user_id=user_id, current_user=current_user
    )


@router.delete(
    "/profiles/{user_id}/follow",
    response_model=CommunityFollowResponse,
    summary="Unfollow an active Travel Community user (idempotent)",
)
def unfollow_user_endpoint(
    user_id: int,
    current_user: Annotated[User, Depends(require_unsuspended_community_user)],
    db: Annotated[Session, Depends(get_db)],
) -> CommunityFollowResponse:
    return unfollow_community_user(
        db, target_user_id=user_id, current_user=current_user
    )


@router.get(
    "/profiles/{user_id}/followers",
    response_model=CommunityFollowListResponse,
    summary="List paginated active followers of a community user",
)
def list_followers_endpoint(
    user_id: int,
    db: Annotated[Session, Depends(get_db)],
    viewer: Annotated[User | None, Depends(get_optional_current_user)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=50)] = 20,
) -> CommunityFollowListResponse:
    return list_profile_followers(
        db, user_id=user_id, viewer=viewer, page=page, page_size=page_size
    )


@router.get(
    "/profiles/{user_id}/following",
    response_model=CommunityFollowListResponse,
    summary="List paginated active accounts followed by a community user",
)
def list_following_endpoint(
    user_id: int,
    db: Annotated[Session, Depends(get_db)],
    viewer: Annotated[User | None, Depends(get_optional_current_user)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=50)] = 20,
) -> CommunityFollowListResponse:
    return list_profile_following(
        db, user_id=user_id, viewer=viewer, page=page, page_size=page_size
    )


@router.get(
    "/provider/eligible-listings",
    response_model=ProviderEligibleListingsResponse,
    summary="List approved, publicly eligible listings owned by the authenticated approved provider",
)
def provider_eligible_listings_endpoint(
    provider_profile: Annotated[ProviderProfile, Depends(require_approved_provider)],
    db: Annotated[Session, Depends(get_db)],
) -> ProviderEligibleListingsResponse:
    return list_provider_eligible_listings(db, provider_profile=provider_profile)


@router.get(
    "/provider/engagement",
    response_model=ProviderCommunityEngagementResponse,
    summary="Get private community engagement summary for the authenticated approved provider",
)
def provider_engagement_endpoint(
    provider_profile: Annotated[ProviderProfile, Depends(require_approved_provider)],
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=50)] = 20,
) -> ProviderCommunityEngagementResponse:
    return get_provider_community_engagement(
        db, provider_profile=provider_profile, page=page, page_size=page_size
    )


# =============================================================================
# REPORTING, ADMIN MODERATION & AUDIT LOGGING (PHASE 5)
# =============================================================================

@router.post(
    "/reports",
    response_model=CommunityReportResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Report a community post, comment, or user for admin moderation",
)
def submit_report_endpoint(
    payload: CommunityReportCreate,
    current_user: Annotated[User, Depends(require_unsuspended_community_user)],
    db: Annotated[Session, Depends(get_db)],
) -> CommunityReportResponse:
    return submit_community_report(db, current_user=current_user, payload=payload)


@router.get(
    "/admin/reports",
    response_model=AdminCommunityReportListResponse,
    summary="List paginated community moderation reports (admin only)",
)
def admin_list_reports_endpoint(
    _admin: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
    report_status: Annotated[ReportStatusType | None, Query()] = None,
    entity_type: Annotated[ReportEntityType | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=50)] = 20,
) -> AdminCommunityReportListResponse:
    return list_admin_community_reports(
        db,
        report_status=report_status,
        entity_type=entity_type,
        page=page,
        page_size=page_size,
    )


@router.patch(
    "/admin/reports/{report_id}",
    response_model=AdminCommunityReportReviewResponse,
    summary="Transition a community report status and record admin audit log (admin only)",
)
def admin_review_report_endpoint(
    report_id: int,
    payload: AdminCommunityReportUpdate,
    request: Request,
    admin_user: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> AdminCommunityReportReviewResponse:
    client_ip = request.client.host if request.client else None
    return review_admin_community_report(
        db,
        report_id=report_id,
        admin_user=admin_user,
        payload=payload,
        ip_address=client_ip,
    )


@router.patch(
    "/admin/posts/{post_id}",
    response_model=AdminCommunityPostModerationResponse,
    summary="Hide, restore, feature, or unfeature a community post with audit logging (admin only)",
)
def admin_moderate_post_endpoint(
    post_id: int,
    payload: AdminCommunityPostModerationRequest,
    request: Request,
    admin_user: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> AdminCommunityPostModerationResponse:
    client_ip = request.client.host if request.client else None
    return moderate_admin_community_post(
        db,
        post_id=post_id,
        admin_user=admin_user,
        payload=payload,
        ip_address=client_ip,
    )


@router.patch(
    "/admin/profiles/{user_id}",
    response_model=AdminCommunityProfileSuspensionResponse,
    summary="Suspend or unsuspend a user's Travel Community write access only (admin only)",
)
def admin_moderate_profile_suspension_endpoint(
    user_id: int,
    payload: AdminCommunityProfileSuspensionRequest,
    request: Request,
    admin_user: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> AdminCommunityProfileSuspensionResponse:
    client_ip = request.client.host if request.client else None
    return moderate_admin_community_profile_suspension(
        db,
        user_id=user_id,
        admin_user=admin_user,
        payload=payload,
        ip_address=client_ip,
    )


@router.get(
    "/admin/hashtags",
    response_model=AdminCommunityHashtagListResponse,
    summary="List and filter community hashtags for admin governance (admin only)",
)
def admin_list_hashtags_endpoint(
    _admin: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
    is_blocked: bool | None = None,
    is_featured: bool | None = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=50)] = 20,
) -> AdminCommunityHashtagListResponse:
    return list_admin_community_hashtags(
        db,
        is_blocked=is_blocked,
        is_featured=is_featured,
        q=q,
        page=page,
        page_size=page_size,
    )


@router.patch(
    "/admin/hashtags/{hashtag_id}",
    response_model=AdminCommunityHashtagModerationResponse,
    summary="Block, unblock, feature, or unfeature a community hashtag with audit logging (admin only)",
)
def admin_moderate_hashtag_endpoint(
    hashtag_id: int,
    payload: AdminCommunityHashtagUpdate,
    request: Request,
    admin_user: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> AdminCommunityHashtagModerationResponse:
    client_ip = request.client.host if request.client else None
    return moderate_admin_community_hashtag(
        db,
        hashtag_id=hashtag_id,
        admin_user=admin_user,
        payload=payload,
        ip_address=client_ip,
    )


@router.delete(
    "/admin/comments/{comment_id}",
    response_model=AdminCommunityCommentModerationResponse,
    summary="Moderate/remove a community comment safely preserving threads & evidence (admin only)",
)
def admin_moderate_delete_comment_endpoint(
    comment_id: int,
    request: Request,
    admin_user: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
    reason: Annotated[str | None, Query(max_length=500)] = None,
) -> AdminCommunityCommentModerationResponse:
    client_ip = request.client.host if request.client else None
    return moderate_admin_delete_comment(
        db,
        comment_id=comment_id,
        admin_user=admin_user,
        reason=reason,
        ip_address=client_ip,
    )


@router.get(
    "/admin/activity",
    response_model=AdminCommunityActivityResponse,
    summary="Get Travel Community moderation counts, open flagged reports & recent audit activity (admin only)",
)
def admin_community_activity_endpoint(
    _admin: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
    recent_limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> AdminCommunityActivityResponse:
    return get_admin_community_activity(db, recent_limit=recent_limit)


@uploads_router.get(
    "/api/v1/uploads/community/{filename}",
    summary="Serve a validated Travel Community post photo",
)
def serve_community_media_endpoint(
    filename: str,
    db: Annotated[Session, Depends(get_db)],
    viewer: Annotated[User | None, Depends(get_optional_current_user)] = None,
) -> FileResponse:
    file_path, media_type = get_authorized_community_media_file(
        db,
        filename=filename,
        viewer=viewer,
    )
    return FileResponse(
        path=str(file_path),
        media_type=media_type,
        headers={
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, max-age=3600",
        },
    )
