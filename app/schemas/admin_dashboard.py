from pydantic import BaseModel, NonNegativeInt


class AdminDashboardProviders(BaseModel):
    total_profiles: NonNegativeInt
    pending_applications: NonNegativeInt


class AdminDashboardBookings(BaseModel):
    homestay: NonNegativeInt | None = None
    rental_vehicle: NonNegativeInt | None = None
    tour_guide: NonNegativeInt | None = None


class AdminDashboardPendingApprovals(BaseModel):
    homestay_listings: NonNegativeInt
    activity_listings: NonNegativeInt
    eco_experience_listings: NonNegativeInt
    artisan_listings: NonNegativeInt
    rental_vehicles: NonNegativeInt
    tour_guide_profiles: NonNegativeInt


class AdminDashboardTravelerEligibility(BaseModel):
    pending_rental_checks: NonNegativeInt


class AdminDashboardResponse(BaseModel):
    providers: AdminDashboardProviders | None = None
    bookings: AdminDashboardBookings
    pending_approvals: AdminDashboardPendingApprovals | None = None
    traveler_eligibility: AdminDashboardTravelerEligibility | None = None
