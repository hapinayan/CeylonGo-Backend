
from app.models.activity import Activity
from app.models.activity_booking import ActivityBooking
from app.models.activity_image import ActivityImage
from app.models.activity_slot import ActivitySlot
from app.models.artisan_listing import ArtisanListing
from app.models.eco_experience import (
    EcoEvidence,
    EcoExperience,
    EcoExperienceImage,
    EcoPractice,
    EcoSafetyRule,
    EcoSeasonRestriction,
    EcoVerification,
)
from app.models.eco_promotion import (
    EcoPromotionAnalytics,
    EcoPromotionPackage,
    EcoSponsoredPromotion,
)
from app.models.eco_wishlist import EcoWishlist
from app.models.password_reset import PasswordReset
from app.models.promotion import HomestayPromotion, Promotion
from app.models.provider_email_otp import ProviderEmailOtp
from app.models.provider_profile import ProviderProfile
from app.models.provider_registration_authorization import (
    ProviderRegistrationAuthorization,
)
from app.models.provider_registration_draft import ProviderRegistrationDraft
from app.models.showcase_item import ShowcaseItem
from app.models.user import Base, User

# Homestay Models
from app.models.homestay import (
    Homestay,
    HomestayImage,
    HomestayAmenity,
)

# Rental Module 09 Models
from app.models.rental_vehicle import RentalVehicle
from app.models.rental_booking import RentalBooking

# Tour Guide Module Models
from app.models.tour_guide import (
    TourGuide,
    TourGuideLanguage,
    TourGuideServiceArea,
    TourGuideCredential,
    TourGuideImage,
    TourGuidePackage,
    TourGuideAvailability,
    TourGuideBooking,
    TourGuideVerification,
)


__all__ = [
    "Activity",
    "ActivityBooking",
    "ActivityImage",
    "ActivitySlot",
    "ArtisanListing",
    "Base",
    "EcoEvidence",
    "EcoExperience",
    "EcoExperienceImage",
    "EcoPractice",
    "EcoPromotionAnalytics",
    "EcoPromotionPackage",
    "EcoSafetyRule",
    "EcoSeasonRestriction",
    "EcoSponsoredPromotion",
    "EcoVerification",
    "EcoWishlist",
    "HomestayPromotion",
    "PasswordReset",
    "Promotion",
    "ProviderEmailOtp",
    "ProviderProfile",
    "ProviderRegistrationAuthorization",
    "ProviderRegistrationDraft",
    "ShowcaseItem",
    "User",

    # Homestay Models
    "Homestay",
    "HomestayImage",
    "HomestayAmenity",

    # Rental Module 09
    "RentalVehicle",
    "RentalBooking",

    # Tour Guide Module
    "TourGuide",
    "TourGuideLanguage",
    "TourGuideServiceArea",
    "TourGuideCredential",
    "TourGuideImage",
    "TourGuidePackage",
    "TourGuideAvailability",
    "TourGuideBooking",
    "TourGuideVerification",
]
