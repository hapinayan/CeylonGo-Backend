import logging
import re
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, NonNegativeInt
from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.dependencies import require_admin, require_operations_or_finance_admin, require_operations_or_support_read
from app.database.connection import get_db
from app.models.booking import Booking
from app.models.provider_profile import ProviderProfile
from app.models.rental_booking import RentalBooking
from app.models.rental_vehicle import RentalVehicle
from app.models.user import User
from app.routers.admin_support import _date_window

router = APIRouter(prefix="/api/v1/admin/reports", tags=["Admin Reports"])
logger = logging.getLogger(__name__)
BookingModule = Literal["homestay", "rental_vehicle"]
STATUS_CODE = re.compile(r"(?!.*[0-9]{7})[A-Za-z][A-Za-z0-9_-]{0,29}")


class BookingStatusCount(BaseModel):
    status: str
    count: NonNegativeInt


class BookingVolumeReport(BaseModel):
    module: BookingModule
    created_from: datetime
    created_before: datetime
    total: NonNegativeInt
    by_status: list[BookingStatusCount]


@router.get(
    "/booking-volume",
    response_model=BookingVolumeReport,
    description=("Counts bookings created within the half-open UTC interval, grouped by their current native status. "
                 "Statuses can change after the report is generated."),
)
def get_booking_volume(
    db: Annotated[Session, Depends(get_db)],
    admin: Annotated[User, Depends(require_admin)],
    module: BookingModule,
    created_from: Annotated[str, Query(max_length=32)],
    created_before: Annotated[str, Query(max_length=32)],
) -> BookingVolumeReport:
    if module == "homestay":
        require_operations_or_finance_admin(admin)
        booking = Booking
        query = db.query(Booking.id)
    else:
        require_operations_or_support_read(admin)
        booking = RentalBooking
        query = (
            db.query(RentalBooking.id)
            .join(RentalVehicle, RentalBooking.vehicle_id == RentalVehicle.id)
            .join(ProviderProfile, RentalVehicle.provider_id == ProviderProfile.id)
            .join(User, RentalBooking.user_id == User.id)
        )

    lower, upper = _date_window(created_from, created_before)
    assert lower is not None and upper is not None
    native_status = booking.status
    grouping = (native_status,)
    if db.get_bind().dialect.name == "mssql":
        # SQL Server pads strings for comparison even under a binary collation.
        # Keep distinct spellings and lengths so malformed values fail validation.
        native_status = native_status.collate("Latin1_General_100_BIN2")
        grouping = (native_status, func.datalength(booking.status))
    try:
        rows = (query.with_entities(native_status.label("status"), func.count(booking.id).label("count"))
                .filter(booking.created_at >= lower, booking.created_at < upper)
                .group_by(*grouping).all())
    except SQLAlchemyError:
        logger.error("Booking volume query failed")
        raise HTTPException(500, "Unable to load booking volume") from None

    if any(not isinstance(row.status, str) or not STATUS_CODE.fullmatch(row.status) for row in rows):
        logger.error("Booking volume found a stored status outside the status-code format")
        raise HTTPException(500, "Unable to load booking volume")

    groups = [BookingStatusCount(status=row.status, count=row.count) for row in sorted(rows, key=lambda row: row.status)]
    return BookingVolumeReport(
        module=module, created_from=lower.replace(tzinfo=UTC), created_before=upper.replace(tzinfo=UTC),
        total=sum(group.count for group in groups), by_status=groups,
    )
