from datetime import UTC, date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.dependencies import require_approved_provider
from app.database.connection import get_db
from app.models.provider_profile import ProviderProfile
from app.schemas.provider_workspace import (
    ActivityPage,
    DashboardAnalytics,
    DashboardPeriod,
    ProviderDashboard,
    ProviderWorkspace,
)
from app.services.provider_workspace import WorkspaceReader

router = APIRouter(prefix="/api/v1/provider", tags=["Provider Workspace"])


def date_period(start_date: date | None = None, end_date: date | None = None):
    if start_date is None and end_date is None:
        return DashboardPeriod()
    if (
        start_date is None
        or end_date is None
        or start_date > end_date
        or end_date.year >= 9999
        or (end_date - start_date).days > 365
    ):
        raise HTTPException(
            422,
            detail="Supply both dates in order, covering at most 366 calendar days.",
        )
    return DashboardPeriod(start_date=start_date, end_date=end_date)


def workspace_reader(
    db: Annotated[Session, Depends(get_db)],
    provider: Annotated[ProviderProfile, Depends(require_approved_provider)],
) -> WorkspaceReader:
    return WorkspaceReader(db, provider)


Reader = Annotated[WorkspaceReader, Depends(workspace_reader)]
Period = Annotated[DashboardPeriod, Depends(date_period)]
PreviewLimit = Annotated[int, Query(ge=1, le=20)]


@router.get("/workspace", response_model=ProviderWorkspace)
def provider_workspace(reader: Reader):
    return reader.workspace()


@router.get("/dashboard", response_model=ProviderDashboard)
def provider_dashboard(
    reader: Reader,
    period: Period,
    activity_limit: PreviewLimit = 5,
    reservation_limit: PreviewLimit = 5,
):
    return reader.dashboard(period, activity_limit, reservation_limit)


@router.get("/dashboard/analytics", response_model=DashboardAnalytics)
def provider_dashboard_analytics(reader: Reader, period: Period):
    if period.start_date is None:
        today = datetime.now(UTC).date()
        year, month = divmod(today.year * 12 + today.month - 1 - 5, 12)
        period = DashboardPeriod(start_date=date(year, month + 1, 1), end_date=today)
    return reader.analytics(period)


@router.get("/dashboard/recent-activity", response_model=ActivityPage)
def provider_dashboard_recent_activity(
    reader: Reader,
    period: Period,
    page: Annotated[int, Query(ge=1, le=1000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=50)] = 20,
):
    return reader.recent_activity(period, page, page_size)
