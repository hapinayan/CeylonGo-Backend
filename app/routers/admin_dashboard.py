from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.dependencies import require_admin_roles, resolve_canonical_admin_role
from app.database.connection import get_db
from app.models.activity import Activity
from app.models.artisan_listing import ArtisanListing
from app.models.booking import Booking
from app.models.eco_experience import EcoExperience
from app.models.homestay import Homestay
from app.models.provider_profile import ProviderProfile
from app.models.rental import RentalEligibilityCheck
from app.models.rental_booking import RentalBooking
from app.models.rental_vehicle import RentalVehicle
from app.models.tour_guide import TourGuide, TourGuideBooking
from app.models.user import User
from app.schemas.admin_dashboard import (
    AdminDashboardBookings,
    AdminDashboardPendingApprovals,
    AdminDashboardProviders,
    AdminDashboardResponse,
    AdminDashboardTravelerEligibility,
)

router = APIRouter(prefix="/api/v1/admin/dashboard", tags=["Admin Dashboard"])
require_dashboard_admin = require_admin_roles("operations_admin", "support_admin", "finance_admin")


def _provider_counts(db: Session) -> AdminDashboardProviders:
    # Match the provider applications list, including its actor join.
    profiles = db.query(func.count(ProviderProfile.id)).join(User, ProviderProfile.user_id == User.id)
    return AdminDashboardProviders(
        total_profiles=profiles.scalar(),
        pending_applications=profiles.filter(ProviderProfile.approval_status == "pending").scalar(),
    )


def _pending_approval_counts(db: Session) -> AdminDashboardPendingApprovals:
    return AdminDashboardPendingApprovals(
        homestay_listings=db.query(func.count(Homestay.id)).filter(Homestay.approval_status == "pending").scalar(),
        activity_listings=db.query(func.count(Activity.id)).filter(Activity.approval_status == "pending").scalar(),
        eco_experience_listings=(
            db.query(func.count(EcoExperience.id)).filter(EcoExperience.listing_status == "pending_approval").scalar()
        ),
        artisan_listings=(
            db.query(func.count(ArtisanListing.id))
            .join(ProviderProfile, ArtisanListing.provider_profile_id == ProviderProfile.id)
            .filter(ArtisanListing.approval_status == "pending")
            .scalar()
        ),
        rental_vehicles=(
            db.query(func.count(RentalVehicle.id))
            .join(ProviderProfile, RentalVehicle.provider_id == ProviderProfile.id)
            .filter(RentalVehicle.approval_status == "pending")
            .scalar()
        ),
        tour_guide_profiles=(
            db.query(func.count(TourGuide.id))
            .join(ProviderProfile, TourGuide.provider_profile_id == ProviderProfile.id)
            .join(User, ProviderProfile.user_id == User.id)
            .filter(TourGuide.approval_status == "pending")
            .scalar()
        ),
    )


@router.get("", response_model=AdminDashboardResponse, response_model_exclude_none=True)
def get_admin_dashboard(
    admin: Annotated[User, Depends(require_dashboard_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> AdminDashboardResponse:
    role = resolve_canonical_admin_role(admin.role)
    can_read_operations = role in ("operations_admin", "super_admin")
    bookings = AdminDashboardBookings()
    dashboard = AdminDashboardResponse(bookings=bookings)

    if can_read_operations or role == "finance_admin":
        bookings.homestay = db.query(func.count(Booking.id)).scalar()

    if can_read_operations or role == "support_admin":
        bookings.rental_vehicle = (
            db.query(func.count(RentalBooking.id))
            .join(RentalVehicle, RentalBooking.vehicle_id == RentalVehicle.id)
            .join(ProviderProfile, RentalVehicle.provider_id == ProviderProfile.id)
            .join(User, RentalBooking.user_id == User.id)
            .scalar()
        )
        # The booking list's expiry step changes statuses. A total needs only the native rows.
        bookings.tour_guide = db.query(func.count(TourGuideBooking.id)).scalar()
        dashboard.traveler_eligibility = AdminDashboardTravelerEligibility(
            pending_rental_checks=(
                db.query(func.count(RentalEligibilityCheck.id))
                .join(User, RentalEligibilityCheck.user_id == User.id)
                .filter(func.lower(RentalEligibilityCheck.verification_status) == "pending")
                .scalar()
            ),
        )

    if can_read_operations:
        dashboard.providers = _provider_counts(db)
        dashboard.pending_approvals = _pending_approval_counts(db)

    return dashboard
