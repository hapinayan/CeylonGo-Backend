"""
Pydantic v2 schemas for Module 04 — Traveler Eco-Tourism Wishlist / Favorites.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.eco_experience import EcoExperiencePublicSummary


class EcoWishlistItemResponse(BaseModel):
    """
    Detailed response schema for a saved eco experience in the traveler's wishlist.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    eco_experience_id: int
    created_at: datetime
    is_available: bool = Field(
        ...,
        description="True if the eco experience is currently approved and published for booking",
    )
    experience: EcoExperiencePublicSummary | None = Field(
        default=None,
        description="Public summary details of the eco experience (null if deactivated/unpublished)",
    )


class EcoWishlistPagination(BaseModel):
    """
    Paginated list of saved eco experiences for the authenticated traveler.
    """

    items: list[EcoWishlistItemResponse]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total_pages: int = Field(ge=0)
