from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.models.rental import RentalLocation
from app.schemas.rental_vehicle import RentalLocationResponse


router = APIRouter(
    prefix="/api/v1/rental-locations",
    tags=["Rental Locations"],
)


# ==============================================================================
# GET ALL ACTIVE RENTAL LOCATIONS (PUBLIC)
# ==============================================================================

@router.get(
    "",
    response_model=list[RentalLocationResponse],
    summary="Get all public rental pickup/return locations",
)
def get_rental_locations(
    db: Annotated[Session, Depends(get_db)],
    district: Annotated[
        str | None,
        Query(description="Filter locations by district name (case-insensitive substring)"),
    ] = None,
):
    """
    Returns active rental hubs across Sri Lanka with GPS coordinates (latitude, longitude)
    for map rendering. Optionally filterable by district.
    """
    query = db.query(RentalLocation)

    if district and district.strip():
        query = query.filter(
            RentalLocation.district.ilike(f"%{district.strip()}%")
        )

    locations = (
        query.order_by(
            RentalLocation.district.asc(),
            RentalLocation.city.asc(),
            RentalLocation.location_name.asc(),
        )
        .all()
    )

    return locations
