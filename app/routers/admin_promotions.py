from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.dependencies import require_operations_or_finance_admin
from app.database.connection import get_db
from app.models.eco_promotion import EcoSponsoredPromotion
from app.models.shared import PromotionCampaign

router = APIRouter(prefix="/api/v1/admin/promotions", tags=["Admin Promotions"])
CampaignModule = Literal["homestay", "eco_experience"]

# Homestay uses the shared campaign constraint in migration 031. Eco has its own
# campaign constraint in migration 020 and does not share the payment lifecycle.
CAMPAIGN_STATUSES = {
    "homestay": frozenset({"draft", "pending_approval", "approved", "active", "paused",
                            "completed", "cancelled", "rejected"}),
    "eco_experience": frozenset({"pending_approval", "approved", "active", "rejected", "expired", "cancelled"}),
}


class CampaignQueueItem(BaseModel):
    id: int
    status: str
    created_at: datetime
    starts_at: datetime | None
    ends_at: datetime | None


class CampaignQueuePage(BaseModel):
    module: CampaignModule
    items: list[CampaignQueueItem]
    total: int
    page: int
    page_size: int
    total_pages: int


@router.get(
    "/campaigns",
    response_model=CampaignQueuePage,
    dependencies=[Depends(require_operations_or_finance_admin)],
    description="Read campaigns from one module. Count and page may reflect different snapshots during concurrent writes.",
)
def list_campaigns(
    db: Annotated[Session, Depends(get_db)],
    module: CampaignModule,
    status: Annotated[str | None, Query(max_length=30)] = None,
    page: Annotated[int, Query(ge=1, le=1_000_000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> CampaignQueuePage:
    if status is not None and status not in CAMPAIGN_STATUSES[module]:
        raise HTTPException(422, "Invalid campaign status for the selected module")

    if module == "homestay":
        campaign = PromotionCampaign
        native_status = campaign.status
        query = db.query(campaign.id).filter(campaign.target_module == "homestay")
    else:
        campaign = EcoSponsoredPromotion
        native_status = campaign.campaign_status
        query = db.query(campaign.id)

    if status is not None:
        query = query.filter(native_status == status)

    total = query.with_entities(func.count(campaign.id)).scalar()
    rows = (
        query.with_entities(campaign.id, native_status.label("status"), campaign.created_at,
                            campaign.starts_at, campaign.ends_at)
        .order_by(campaign.created_at.desc(), campaign.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return CampaignQueuePage(
        module=module, items=[CampaignQueueItem(**row._mapping) for row in rows],
        total=total, page=page, page_size=page_size, total_pages=(total + page_size - 1) // page_size,
    )
