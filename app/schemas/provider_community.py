from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ProviderCommunityPostCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(..., min_length=5, max_length=4000, description="Post text or cultural/tourism update")
    location_tag: str | None = Field(default=None, max_length=150)
    district: str | None = Field(default=None, max_length=100)


class ProviderCommunityPostResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    author_id: int
    author_name: str
    business_name: str
    content: str
    location_tag: str | None = None
    district: str | None = None
    likes_count: int = 0
    comments_count: int = 0
    is_published: bool = True
    created_at: datetime


class ProviderCommunityPostListResponse(BaseModel):
    items: list[ProviderCommunityPostResponse]
    total: int
    page: int
    page_size: int
    total_pages: int

