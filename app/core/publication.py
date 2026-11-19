from sqlalchemy import and_, or_, select, true

from app.models.activity import Activity
from app.models.artisan_listing import ArtisanListing
from app.models.eco_experience import EcoExperience
from app.models.homestay import Homestay
from app.models.provider_profile import ProviderProfile
from app.models.rental import RentalLocation
from app.models.rental_vehicle import RentalVehicle
from app.models.user import User


def approved_provider(profile_id, *, hold=False, roles=("provider",)):
    query = (
        select(1)
        .select_from(ProviderProfile)
        .join(User, User.id == ProviderProfile.user_id)
        .where(
            ProviderProfile.id == profile_id,
            ProviderProfile.approval_status == "approved",
            User.is_active == true(),
            User.role.in_(roles),
        )
        .correlate_except(ProviderProfile, User)
    )
    if hold:
        query = query.with_hint(ProviderProfile, "WITH (HOLDLOCK)", "mssql")
        query = query.with_hint(User, "WITH (HOLDLOCK)", "mssql")
    return query.exists()


def public_homestay(*, hold=False):
    legacy_profile = (
        select(1)
        .where(
            ProviderProfile.user_id == User.id,
            ProviderProfile.approval_status == "approved",
        )
        .correlate(User)
    )
    if hold:
        legacy_profile = legacy_profile.with_hint(
            ProviderProfile,
            "WITH (HOLDLOCK)",
            "mssql",
        )
    legacy_owner = (
        select(1)
        .select_from(User)
        .where(
            User.id == Homestay.owner_id,
            User.is_active == true(),
            or_(
                User.role == "admin",
                and_(
                    User.role.in_(("owner", "provider")),
                    legacy_profile.exists(),
                ),
            ),
        )
        .correlate_except(User)
    )
    if hold:
        legacy_owner = legacy_owner.with_hint(User, "WITH (HOLDLOCK)", "mssql")
    return and_(
        Homestay.is_active == true(),
        Homestay.approval_status == "approved",
        or_(
            approved_provider(Homestay.provider_profile_id, hold=hold, roles=("provider", "owner")),
            and_(
                Homestay.provider_profile_id.is_(None),
                or_(Homestay.owner_id.is_(None), legacy_owner.exists()),
            ),
        ),
    )


def public_activity(*, hold=False):
    return and_(
        Activity.is_active == true(),
        Activity.approval_status == "approved",
        approved_provider(Activity.provider_profile_id, hold=hold),
    )


def public_eco(*, hold=False):
    return and_(
        EcoExperience.is_published == true(),
        EcoExperience.listing_status == "approved",
        approved_provider(EcoExperience.provider_profile_id, hold=hold),
    )


def public_artisan():
    return and_(
        ArtisanListing.is_active == true(),
        ArtisanListing.approval_status == "approved",
        ArtisanListing.is_verified == true(),
        approved_provider(ArtisanListing.provider_profile_id),
    )


def public_rental(*, hold=False):
    active_pickup = (
        select(1)
        .where(
            RentalLocation.id == RentalVehicle.location_id,
            RentalLocation.provider_id == RentalVehicle.provider_id,
            RentalLocation.is_active == true(),
        )
        .correlate_except(RentalLocation)
    )
    if hold:
        active_pickup = active_pickup.with_hint(
            RentalLocation, "WITH (HOLDLOCK)", "mssql"
        )
    return and_(
        RentalVehicle.status == "available",
        RentalVehicle.approval_status == "approved",
        approved_provider(RentalVehicle.provider_id, hold=hold),
        or_(RentalVehicle.location_id.is_(None), active_pickup.exists()),
    )
