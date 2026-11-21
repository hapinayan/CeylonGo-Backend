"""Pydantic schemas for Travel Community & Social Feed — Phase 1 (Migration 028 compatible)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ListingModuleType = Literal[
    "homestay",
    "tour_guide",
    "activity",
    "eco_experience",
    "artisan",
    "rental_vehicle",
]


class CommunityLinkedListingSummary(BaseModel):
    """Public-safe summary of an approved, live-eligible linked provider listing."""

    listing_reference_id: int
    module_type: ListingModuleType
    target_id: int
    title: str
    district: str | None = None
    city: str | None = None
    public_url: str


class CommunityAuthorSummary(BaseModel):
    """Public-safe author identity summary (never exposes private email or phone)."""

    id: int
    full_name: str
    role: str
    is_verified_provider: bool = False
    business_name: str | None = None
    provider_type: str | None = None


class CommunityPostLocation(BaseModel):
    """Structured location metadata for a community post."""

    location_tag: str | None = None
    district: str | None = None
    latitude: Decimal | None = None
    longitude: Decimal | None = None


class CommunityHashtagResponse(BaseModel):
    """Normalized hashtag record associated with a post."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    tag: str
    usage_count: int
    is_featured: bool = False


class CommunityPostMediaResponse(BaseModel):
    """Photo media item attached to a community post."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    post_id: int
    media_type: str
    media_url: str
    display_order: int
    created_at: datetime


class CommunityPostCreate(BaseModel):
    """Payload to create a new Travel Community post."""

    content: str = Field(..., min_length=1, max_length=5000)
    location_tag: str | None = Field(default=None, max_length=150)
    district: str | None = Field(default=None, max_length=100)
    latitude: Decimal | None = Field(default=None, ge=Decimal("-90"), le=Decimal("90"))
    longitude: Decimal | None = Field(default=None, ge=Decimal("-180"), le=Decimal("180"))
    hashtags: list[str] | None = Field(default=None, max_length=20)
    is_published: bool = True
    is_promotional: bool = False
    listing_reference_id: int | None = Field(default=None, gt=0)
    listing_module_type: ListingModuleType | None = None
    listing_target_id: int | None = Field(default=None, gt=0)

    @field_validator("content")
    @classmethod
    def _validate_content_not_blank(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("Post caption cannot be empty.")
        return value

    @model_validator(mode="after")
    def _validate_listing_link_inputs(self) -> "CommunityPostCreate":
        has_pair_any = (
            self.listing_module_type is not None or self.listing_target_id is not None
        )
        has_pair_both = (
            self.listing_module_type is not None and self.listing_target_id is not None
        )
        if has_pair_any and not has_pair_both:
            raise ValueError(
                "Both listing_module_type and listing_target_id must be provided together."
            )
        if self.listing_reference_id is not None and has_pair_any:
            raise ValueError(
                "Provide either listing_reference_id or (listing_module_type, listing_target_id), not both."
            )
        return self


class CommunityPostUpdate(BaseModel):
    """Partial update payload for an existing Travel Community post."""

    content: str | None = Field(default=None, min_length=1, max_length=5000)
    location_tag: str | None = Field(default=None, max_length=150)
    district: str | None = Field(default=None, max_length=100)
    latitude: Decimal | None = Field(default=None, ge=Decimal("-90"), le=Decimal("90"))
    longitude: Decimal | None = Field(default=None, ge=Decimal("-180"), le=Decimal("180"))
    hashtags: list[str] | None = Field(default=None, max_length=20)
    is_published: bool | None = None
    is_promotional: bool | None = None
    listing_reference_id: int | None = Field(default=None, gt=0)
    listing_module_type: ListingModuleType | None = None
    listing_target_id: int | None = Field(default=None, gt=0)

    @field_validator("content")
    @classmethod
    def _validate_content_not_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("Post caption cannot be empty.")
        return value

    @model_validator(mode="after")
    def _validate_listing_link_inputs(self) -> "CommunityPostUpdate":
        has_pair_any = (
            self.listing_module_type is not None or self.listing_target_id is not None
        )
        has_pair_both = (
            self.listing_module_type is not None and self.listing_target_id is not None
        )
        if has_pair_any and not has_pair_both:
            raise ValueError(
                "Both listing_module_type and listing_target_id must be provided together."
            )
        if self.listing_reference_id is not None and has_pair_any:
            raise ValueError(
                "Provide either listing_reference_id or (listing_module_type, listing_target_id), not both."
            )
        return self


class CommunityPostShareMetadata(BaseModel):
    """Canonical public sharing & preview metadata for a community post."""

    is_shareable: bool
    share_url: str | None = None
    preview_title: str
    preview_text: str
    preview_image_url: str | None = None


class CommunityPostResponse(BaseModel):
    """Complete community post response (Phase 1 + Phase 2 enrichment)."""

    id: int
    author_id: int
    author: CommunityAuthorSummary
    content: str
    location_tag: str | None = None
    district: str | None = None
    latitude: Decimal | None = None
    longitude: Decimal | None = None
    location: CommunityPostLocation
    likes_count: int
    comments_count: int
    is_published: bool
    is_promotional: bool = False
    is_featured: bool = False
    listing_reference_id: int | None = None
    linked_listing: CommunityLinkedListingSummary | None = None
    is_liked: bool = False
    is_saved: bool = False
    media: list[CommunityPostMediaResponse] = Field(default_factory=list)
    hashtags: list[CommunityHashtagResponse] = Field(default_factory=list)
    share_url: str
    share_metadata: CommunityPostShareMetadata
    created_at: datetime
    updated_at: datetime | None = None


class CommunityFeedResponse(BaseModel):
    """Paginated public Travel Community feed response."""

    items: list[CommunityPostResponse] = Field(default_factory=list)
    total: int
    page: int
    page_size: int
    total_pages: int
    has_next: bool
    has_previous: bool


class CommunityPersonSearchResult(BaseModel):
    """Public-safe community user/provider search result (never leaks email/phone/NIC)."""

    id: int
    full_name: str
    role: str
    is_verified_provider: bool = False
    business_name: str | None = None
    provider_type: str | None = None
    district: str | None = None
    posts_count: int = 0
    followers_count: int = 0
    is_following: bool = False


class CommunitySearchResponse(BaseModel):
    """Unified paginated search response across published posts, people, and hashtags."""

    query: str
    search_type: Literal["all", "posts", "people", "hashtags"]
    page: int
    page_size: int
    posts: list[CommunityPostResponse] = Field(default_factory=list)
    posts_total: int = 0
    people: list[CommunityPersonSearchResult] = Field(default_factory=list)
    people_total: int = 0
    hashtags: list[CommunityHashtagResponse] = Field(default_factory=list)
    hashtags_total: int = 0


class CommunityTrendingHashtagsResponse(BaseModel):
    """Trending hashtags ordered by usage_count DESC with deterministic tie-breaking."""

    items: list[CommunityHashtagResponse] = Field(default_factory=list)
    limit: int
    total: int


# =============================================================================
# PHASE 3: SOCIAL ENGAGEMENT SCHEMAS (LIKES, COMMENTS, SAVED POSTS)
# =============================================================================

class CommunityLikeResponse(BaseModel):
    """Response for idempotent like / unlike operations."""

    post_id: int
    liked: bool
    likes_count: int


class CommunitySaveResponse(BaseModel):
    """Response for idempotent save / unsave bookmark operations."""

    post_id: int
    saved: bool


class CommunityCommentCreate(BaseModel):
    """Payload to create a top-level comment or threaded reply on a community post."""

    content: str = Field(..., min_length=1, max_length=1000)
    parent_comment_id: int | None = Field(default=None, ge=1)

    @field_validator("content")
    @classmethod
    def _validate_comment_not_blank(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("Comment content cannot be empty.")
        return value


class CommunityCommentUpdate(BaseModel):
    """Payload to update an existing comment owned by the caller."""

    content: str = Field(..., min_length=1, max_length=1000)

    @field_validator("content")
    @classmethod
    def _validate_comment_update_not_blank(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("Comment content cannot be empty.")
        return value


class CommunityCommentResponse(BaseModel):
    """Serialized community comment with public-safe author summary and threaded replies."""

    id: int
    post_id: int
    author_id: int
    author: CommunityAuthorSummary
    parent_comment_id: int | None = None
    content: str
    is_redacted: bool = False
    reply_count: int = 0
    replies: list["CommunityCommentResponse"] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime | None = None


class CommunityCommentListResponse(BaseModel):
    """Paginated comments list for a community post."""

    post_id: int
    items: list[CommunityCommentResponse] = Field(default_factory=list)
    total: int
    total_comments_on_post: int
    page: int
    page_size: int
    total_pages: int
    has_next: bool
    has_previous: bool


class CommunityCommentDeleteResponse(BaseModel):
    """Response describing safe comment deletion under Migration 028 FK constraints."""

    deleted: bool = True
    comment_id: int
    post_id: int
    comments_count: int
    deletion_mode: Literal["hard_deleted", "redacted_thread_preserved"]
    detail: str


class CommunitySavedPostsResponse(BaseModel):
    """Strictly private paginated list of the authenticated caller's bookmarked posts."""

    items: list[CommunityPostResponse] = Field(default_factory=list)
    total: int
    page: int
    page_size: int
    total_pages: int
    has_next: bool
    has_previous: bool


CommunityCommentResponse.model_rebuild()


# =============================================================================
# PHASE 4: PROFILES, FOLLOWS & APPROVED PROVIDER PROMOTIONS SCHEMAS
# =============================================================================

class CommunityProfileResponse(BaseModel):
    """Public-safe community profile for an active user (never exposes email, phone, NIC, or private fields)."""

    id: int
    full_name: str
    role: str
    is_verified_provider: bool = False
    business_name: str | None = None
    provider_type: str | None = None
    district: str | None = None
    posts_count: int = 0
    followers_count: int = 0
    following_count: int = 0
    is_following: bool = False
    created_at: datetime
    posts: CommunityFeedResponse


class CommunityFollowResponse(BaseModel):
    """Response for idempotent follow / unfollow actions."""

    target_user_id: int
    following: bool
    followers_count: int
    following_count: int


class CommunityFollowListResponse(BaseModel):
    """Paginated list of active followers or followed users."""

    user_id: int
    list_type: Literal["followers", "following"]
    items: list[CommunityPersonSearchResult] = Field(default_factory=list)
    total: int
    page: int
    page_size: int
    total_pages: int
    has_next: bool
    has_previous: bool


class ProviderEligibleListingItem(BaseModel):
    """Approved, provider-owned, publicly eligible listing across supported CeylonGo modules."""

    module_type: Literal[
        "homestay",
        "tour_guide",
        "activity",
        "eco_experience",
        "artisan",
        "rental_vehicle",
    ]
    target_id: int
    listing_reference_id: int | None = None
    title: str
    district: str | None = None
    city: str | None = None
    public_url: str


class ProviderEligibleListingsResponse(BaseModel):
    """Approved listings owned by the authenticated provider that are eligible for community promotion."""

    provider_profile_id: int
    business_name: str
    provider_type: str
    items: list[ProviderEligibleListingItem] = Field(default_factory=list)
    total: int
    promotional_linking_note: str


class ProviderPostEngagementItem(BaseModel):
    """Per-post engagement summary for an approved provider's own community post."""

    post_id: int
    content_preview: str
    is_published: bool
    likes_count: int
    comments_count: int
    saves_count: int
    total_engagements: int
    created_at: datetime


class ProviderCommunityEngagementResponse(BaseModel):
    """Private community engagement metrics strictly scoped to the authenticated approved provider."""

    provider_profile_id: int
    business_name: str
    provider_type: str
    total_posts: int
    published_posts: int
    unpublished_posts: int
    total_likes: int
    total_comments: int
    total_saves: int
    followers_count: int
    posts: list[ProviderPostEngagementItem] = Field(default_factory=list)
    page: int
    page_size: int
    total_pages: int
    has_next: bool
    has_previous: bool


class CommunityPostDeleteResponse(BaseModel):
    """Response describing how a post deletion was completed."""

    deleted: bool = True
    post_id: int
    deletion_mode: Literal["hard_deleted", "soft_unpublished_moderation_preserved"]
    detail: str


class CommunityMediaDeleteResponse(BaseModel):
    """Response returned after deleting a single post media item."""

    deleted: bool = True
    post_id: int
    media_id: int
    detail: str


# =============================================================================
# PHASE 5: REPORTING, ADMIN MODERATION & AUDIT LOG SCHEMAS
# =============================================================================

ReportEntityType = Literal["post", "comment", "user"]
ReportStatusType = Literal["pending", "investigating", "resolved", "dismissed"]


class CommunityReportCreate(BaseModel):
    """Payload to submit a moderation report against a post, comment, or user."""

    entity_type: ReportEntityType
    entity_id: int = Field(..., gt=0)
    report_reason: str = Field(..., min_length=3, max_length=100)
    description: str | None = Field(default=None, max_length=1000)


class CommunityReportResponse(BaseModel):
    """Response returned to the authenticated reporter after filing a report."""

    id: int
    reporter_id: int
    entity_type: ReportEntityType
    entity_id: int
    report_reason: str
    description: str | None = None
    report_status: ReportStatusType
    created_at: datetime


class AdminSafeUserRef(BaseModel):
    """Minimal non-PII user reference for moderation views."""

    id: int
    full_name: str
    role: str


class CommunityReportTargetPreview(BaseModel):
    """Safe, non-PII preview of the reported post, comment, or user."""

    entity_type: ReportEntityType
    entity_id: int
    exists: bool
    summary: str | None = None
    author_id: int | None = None
    author_name: str | None = None
    is_published: bool | None = None
    post_id: int | None = None
    is_active: bool | None = None


class AdminCommunityReportItem(BaseModel):
    """Enriched report item in the admin moderation queue."""

    id: int
    reporter_id: int
    reporter: AdminSafeUserRef | None = None
    entity_type: ReportEntityType
    entity_id: int
    report_reason: str
    description: str | None = None
    report_status: ReportStatusType
    reviewed_by: int | None = None
    reviewer: AdminSafeUserRef | None = None
    reviewed_at: datetime | None = None
    review_notes: str | None = None
    created_at: datetime
    target_preview: CommunityReportTargetPreview


class AdminCommunityReportListResponse(BaseModel):
    """Paginated admin report moderation queue."""

    items: list[AdminCommunityReportItem] = Field(default_factory=list)
    total: int
    page: int
    page_size: int
    total_pages: int
    has_next: bool
    has_previous: bool


class AdminCommunityReportUpdate(BaseModel):
    """Admin review transition payload for a community report."""

    report_status: ReportStatusType
    review_notes: str | None = Field(default=None, max_length=500)


class AdminCommunityReportReviewResponse(BaseModel):
    """Response after an admin transitions a community report."""

    report: AdminCommunityReportItem
    previous_status: ReportStatusType
    audit_log_id: int


class AdminCommunityPostModerationRequest(BaseModel):
    """Admin hide/restore (is_published) and/or feature/unfeature (is_featured) payload."""

    is_published: bool | None = None
    is_featured: bool | None = None
    reason: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _require_at_least_one_field(self) -> "AdminCommunityPostModerationRequest":
        if self.is_published is None and self.is_featured is None:
            raise ValueError("Provide at least one of 'is_published' or 'is_featured'.")
        return self


class AdminCommunityPostModerationResponse(BaseModel):
    """Response after an admin moderates visibility or featured status of a community post."""

    post_id: int
    author_id: int
    is_published: bool
    previous_is_published: bool
    is_featured: bool = False
    previous_is_featured: bool = False
    moderation_status: str = "active"
    action: Literal[
        "community_post_hide",
        "community_post_restore",
        "community_post_feature",
        "community_post_unfeature",
    ]
    reason: str | None = None
    updated_at: datetime | None = None
    audit_log_id: int


class AdminCommunityProfileSuspensionRequest(BaseModel):
    """Admin payload to suspend or unsuspend a user's Travel Community write access only."""

    is_community_suspended: bool
    suspension_reason: str | None = Field(default=None, max_length=500)
    suspended_until: datetime | None = None


class AdminCommunityProfileSuspensionResponse(BaseModel):
    """Response after an admin updates a user's community-only suspension state."""

    user_id: int
    is_community_suspended: bool
    is_currently_suspended: bool
    suspended_until: datetime | None = None
    suspension_reason: str | None = None
    suspended_by: int | None = None
    suspended_at: datetime | None = None
    updated_at: datetime | None = None
    action: Literal["community_user_suspend", "community_user_unsuspend"]
    audit_log_id: int


class AdminCommunityHashtagItem(BaseModel):
    """Admin hashtag governance view."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    tag: str
    usage_count: int
    is_blocked: bool
    is_featured: bool
    created_at: datetime


class AdminCommunityHashtagListResponse(BaseModel):
    """Paginated admin hashtag governance listing."""

    items: list[AdminCommunityHashtagItem] = Field(default_factory=list)
    total: int
    page: int
    page_size: int
    total_pages: int
    has_next: bool
    has_previous: bool


class AdminCommunityHashtagUpdate(BaseModel):
    """Admin payload to block/unblock or feature/unfeature a community hashtag."""

    is_blocked: bool | None = None
    is_featured: bool | None = None
    reason: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _require_at_least_one_flag(self) -> "AdminCommunityHashtagUpdate":
        if self.is_blocked is None and self.is_featured is None:
            raise ValueError("Provide at least one of 'is_blocked' or 'is_featured'.")
        return self


class AdminCommunityHashtagModerationResponse(BaseModel):
    """Response after an admin updates hashtag governance flags."""

    hashtag: AdminCommunityHashtagItem
    previous_is_blocked: bool
    previous_is_featured: bool
    action: Literal[
        "community_hashtag_block",
        "community_hashtag_unblock",
        "community_hashtag_feature",
        "community_hashtag_unfeature",
        "community_hashtag_moderate",
    ]
    reason: str | None = None
    audit_log_id: int


class AdminCommunityCommentModerationResponse(BaseModel):
    """Response after an admin moderates/removes a community comment."""

    deleted: bool = True
    comment_id: int
    post_id: int
    comments_count: int
    deletion_mode: Literal["hard_deleted", "redacted_thread_preserved"]
    detail: str
    audit_log_id: int


class AdminCommunityAuditLogItem(BaseModel):
    """Structured community moderation audit entry from dbo.admin_audit_logs."""

    id: int
    admin_user_id: int
    admin_name: str | None = None
    action: str
    entity_name: str
    entity_id: int
    details: dict[str, Any] | str | None = None
    created_at: datetime


class AdminCommunityActivityResponse(BaseModel):
    """Admin overview of Travel Community counts, open flagged reports, and recent moderation activity."""

    total_posts: int
    published_posts: int
    hidden_posts: int
    total_comments: int
    total_reports: int
    pending_reports: int
    investigating_reports: int
    resolved_reports: int
    dismissed_reports: int
    recent_flagged_reports: list[AdminCommunityReportItem] = Field(default_factory=list)
    recent_moderation_actions: list[AdminCommunityAuditLogItem] = Field(default_factory=list)
    pending_migration_037_features: list[str] = Field(default_factory=list)
