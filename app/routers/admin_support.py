import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.dependencies import require_operations_or_support_read
from app.database.connection import get_db
from app.models.rental import RentalIncident
from app.models.rental_booking import RentalBooking
from app.models.rental_vehicle import RentalVehicle

router = APIRouter(prefix="/api/v1/admin/support", tags=["Admin Support"])
logger = logging.getLogger(__name__)

# Native states and types from the RentalIncident model and migration 026.
ResolutionStatus = Literal[
    "reported", "under_investigation", "insurance_claimed", "customer_billed", "resolved", "dismissed"
]
IncidentType = Literal["scratch", "dent", "accident", "mechanical_failure", "theft", "late_return", "traffic_fine"]
DEFAULT_TRIAGE_STATUSES = ("reported", "under_investigation")
MAX_WINDOW = timedelta(days=90)
TIMESTAMP = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?(?:Z|[+-][0-9]{2}:[0-9]{2})")


class IncidentTriageItem(BaseModel):
    id: int
    booking_id: int
    incident_type: str
    resolution_status: str
    created_at: datetime
    updated_at: datetime


class IncidentTriagePage(BaseModel):
    module: Literal["rental_vehicle"]
    items: list[IncidentTriageItem]
    total: int
    page: int
    page_size: int
    total_pages: int


def _utc_timestamp(value: str) -> datetime:
    if not TIMESTAMP.fullmatch(value) or value.endswith("-00:00"):
        raise ValueError("Timestamp requires an explicit, known UTC offset")
    # fromisoformat normalizes some out-of-range offset components. Reject them first.
    if not value.endswith("Z") and (int(value[-5:-3]) > 23 or int(value[-2:]) > 59):
        raise ValueError("Invalid UTC offset")
    return datetime.fromisoformat(value).astimezone(UTC).replace(tzinfo=None)


def _date_window(created_from: str | None, created_before: str | None) -> tuple[datetime | None, datetime | None]:
    try:
        lower = _utc_timestamp(created_from) if created_from is not None else None
        upper = _utc_timestamp(created_before) if created_before is not None else None
        if lower is None and upper is not None:
            lower = upper - MAX_WINDOW
        if upper is None and lower is not None:
            upper = lower + MAX_WINDOW
        if lower is not None and upper is not None and not timedelta(0) < upper - lower <= MAX_WINDOW:
            raise ValueError("Invalid date range")
    except (ValueError, OverflowError):
        raise HTTPException(422, "Provide valid timestamps with explicit UTC offsets and a range of at most 90 days") from None
    return lower, upper


@router.get(
    "/incidents",
    response_model=IncidentTriagePage,
    dependencies=[Depends(require_operations_or_support_read)],
    description=(
        "Reported and under-investigation incidents by default. Date filters use a half-open UTC range. "
        "A single bound creates a 90-day window forward from created_from or backward from created_before. "
        "Without date filters, the selected lane has no age cutoff. Count and page may reflect different "
        "snapshots during concurrent writes."
    ),
)
def list_incident_triage(
    db: Annotated[Session, Depends(get_db)],
    module: Literal["rental_vehicle"],
    resolution_status: ResolutionStatus | None = None,
    incident_type: IncidentType | None = None,
    created_from: Annotated[str | None, Query(max_length=32, description="Inclusive offset-aware timestamp")] = None,
    created_before: Annotated[str | None, Query(max_length=32, description="Exclusive offset-aware timestamp")] = None,
    page: Annotated[int, Query(ge=1, le=1_000_000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> IncidentTriagePage:
    lower, upper = _date_window(created_from, created_before)
    statuses = DEFAULT_TRIAGE_STATUSES if resolution_status is None else (resolution_status,)
    query = (
        db.query(RentalIncident.id)
        .join(RentalBooking, RentalIncident.booking_id == RentalBooking.id)
        .join(RentalVehicle, RentalBooking.vehicle_id == RentalVehicle.id)
        .filter(RentalIncident.resolution_status.in_(statuses))
    )
    if incident_type is not None:
        query = query.filter(RentalIncident.incident_type == incident_type)
    if lower is not None:
        query = query.filter(RentalIncident.created_at >= lower)
    if upper is not None:
        query = query.filter(RentalIncident.created_at < upper)

    try:
        total = query.with_entities(func.count(RentalIncident.id)).scalar()
        rows = (
            query.with_entities(RentalIncident.id, RentalIncident.booking_id, RentalIncident.incident_type,
                                RentalIncident.resolution_status, RentalIncident.created_at, RentalIncident.updated_at)
            .order_by(RentalIncident.created_at.asc(), RentalIncident.id.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
    except SQLAlchemyError:
        logger.error("Rental incident triage query failed")
        raise HTTPException(500, "Unable to load incident triage") from None

    return IncidentTriagePage(
        module=module, items=[IncidentTriageItem(**row._mapping) for row in rows],
        total=total, page=page, page_size=page_size, total_pages=(total + page_size - 1) // page_size,
    )
