from datetime import datetime
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class CommunityPage(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int
    total_pages: int


class CommunityAuthorProfile(BaseModel):
    id: int
    full_name: str
    author_type: Literal["traveler", "provider", "admin"]
    provider_profile_id: int | None = None
    provider_business_name: str | None = None
    provider_district: str | None = None
    is_verified_provider: bool = False
    member_since: datetime
    avatar_url: str | None = None


class CommunityPostMediaItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    post_id: int
    media_type: str = "image"
    image_url: str
    alt_text: str | None = None
    display_order: int = 0
    created_at: datetime


class CommunityPostCreateRequest(BaseModel):
    body: str = Field(..., min_length=1, max_length=2000)


class CommunityPostUpdateRequest(BaseModel):
    body: str = Field(..., min_length=1, max_length=2000)


class CommunityPostDetails(BaseModel):
    id: int
    author_id: int
    author_name: str
    author_type: Literal["traveler", "provider", "admin"]
    author: CommunityAuthorProfile
    body: str
    visibility: str
    created_at: datetime
    updated_at: datetime
    likes_count: int = 0
    comments_count: int = 0
    viewer_has_liked: bool = False
    viewer_has_saved: bool = False
    media: list[CommunityPostMediaItem] = []


class CommunityCommentCreateRequest(BaseModel):
    body: str = Field(..., min_length=1, max_length=1000)


class CommunityCommentUpdateRequest(BaseModel):
    body: str = Field(..., min_length=1, max_length=1000)


class CommunityCommentDetails(BaseModel):
    id: int
    post_id: int
    author_id: int
    author_name: str
    author_type: Literal["traveler", "provider", "admin"]
    author: CommunityAuthorProfile
    body: str
    visibility: str
    created_at: datetime
    updated_at: datetime


class CommunityLikeStatus(BaseModel):
    post_id: int
    liked: bool
    likes_count: int


class CommunitySaveStatus(BaseModel):
    post_id: int
    saved: bool


class CommunityReportCreateRequest(BaseModel):
    entity_type: Literal["post", "comment"]
    entity_id: int = Field(..., ge=1)
    reason: str = Field(..., min_length=3, max_length=100)
    description: str | None = Field(None, max_length=1000)


class CommunityReportDetails(BaseModel):
    id: int
    reporter_id: int
    reporter_name: str | None = None
    entity_type: str
    entity_id: int
    reason: str
    description: str | None = None
    status: str
    reviewed_by: int | None = None
    review_notes: str | None = None
    reviewed_at: datetime | None = None
    created_at: datetime


class AdminCommunityPostModerationPatch(BaseModel):
    visibility: Literal["visible", "hidden", "deleted"]
    reason: str = Field(..., min_length=3, max_length=500)


class AdminCommunityCommentModerationPatch(BaseModel):
    visibility: Literal["visible", "hidden", "deleted"]
    reason: str = Field(..., min_length=3, max_length=500)


class AdminCommunityReportDecision(BaseModel):
    status: Literal["reviewed", "resolved", "dismissed"]
    review_notes: str | None = Field(None, max_length=500)
    action_visibility: Literal["visible", "hidden", "deleted"] | None = None
