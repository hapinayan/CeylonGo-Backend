from datetime import datetime

from pydantic import BaseModel

from app.schemas.showcase_item import ShowcaseItemSummary


class ArtisanFavoriteResponse(BaseModel):
    product_id: int
    created_at: datetime
    product: ShowcaseItemSummary


class ArtisanFavoritePage(BaseModel):
    items: list[ArtisanFavoriteResponse]
    total: int
    page: int
    page_size: int
