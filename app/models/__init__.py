# CeylonGo Centralized Master Database ORM Models
# =================================================

# User & Auth
from app.models.user import Base, User
from app.models.password_reset import PasswordReset
from app.models.provider_profile import ProviderProfile
from app.models.provider_email_otp import ProviderEmailOtp
from app.models.provider_registration_authorization import (
    ProviderRegistrationAuthorization,
)
from app.models.provider_registration_draft import ProviderRegistrationDraft

# Homestay Module
from app.models.homestay import (
    Homestay,
    HomestayImage,
    HomestayAmenity,
    HomestayAvailabilityBlock,
)
from app.models.booking import Booking, Booking as HomestayBooking
from app.models.promotion import HomestayPromotion, Promotion

# Tour Guide Module
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

# Artisan Module
from app.models.artisan_listing import ArtisanListing
from app.models.showcase_item import ShowcaseItem
from app.models.artisan import (
    ArtisanBusinessImage,
    ArtisanProductImage,
    ArtisanReservation,
    ArtisanBusinessHours,
)

# Activities & Eco-Tourism Module
from app.models.activity import Activity
from app.models.activity_image import ActivityImage
from app.models.activity_slot import ActivitySlot
from app.models.activity_booking import (
    ActivityBooking,
    ActivityBookingParticipant,
)
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

# Vehicle Rental Module
from app.models.rental_vehicle import RentalVehicle
from app.models.rental_booking import RentalBooking
from app.models.rental import (
    RentalLocation,
    RentalRatePlan,
    RentalVehicleImage,
    RentalVehicleDocument,
    RentalAvailabilityBlock,
    RentalEligibilityCheck,
    RentalInspection,
    RentalConditionEvidence,
    RentalIncident,
    RentalDeposit,
)

# Shared Infrastructure Module
from app.models.shared import (
    ListingReference,
    BookingReference,
    PaymentTransaction,
    AdvertisingPayment,
    PaymentRefund,
    ProviderSettlement,
    Notification,
    Review,
    Wishlist,
    PromotionPackage,
    PromotionCampaign,
    PromotionEvent,
    BookingStatusHistory,
    AdminAuditLog,
)

# Travel Community Module
from app.models.community import (
    CommunityPost,
    CommunityPostMedia,
    CommunityPostLike,
    CommunityComment,
    CommunitySavedPost,
    CommunityFollow,
    CommunityReport,
    CommunityHashtag,
    CommunityPostHashtag,
)

# AI Smart Travel Planner Module
from app.models.ai_planner import (
    AIChatSession,
    AIChatMessage,
    TravelPreference,
    TravelPlan,
    TravelPlanDay,
    TravelPlanItem,
    AIPendingAction,
    AIActionAudit,
)

__all__ = [
    # Auth & Users
    "Base",
    "User",
    "PasswordReset",
    "ProviderProfile",
    "ProviderEmailOtp",
    "ProviderRegistrationAuthorization",
    "ProviderRegistrationDraft",

    # Homestay
    "Homestay",
    "HomestayImage",
    "HomestayAmenity",
    "HomestayAvailabilityBlock",
    "Booking",
    "HomestayBooking",
    "HomestayPromotion",
    "Promotion",

    # Tour Guide
    "TourGuide",
    "TourGuideLanguage",
    "TourGuideServiceArea",
    "TourGuideCredential",
    "TourGuideImage",
    "TourGuidePackage",
    "TourGuideAvailability",
    "TourGuideBooking",
    "TourGuideVerification",

    # Artisan
    "ArtisanListing",
    "ShowcaseItem",
    "ArtisanBusinessImage",
    "ArtisanProductImage",
    "ArtisanReservation",
    "ArtisanBusinessHours",

    # Activities & Eco-Tourism
    "Activity",
    "ActivityImage",
    "ActivitySlot",
    "ActivityBooking",
    "ActivityBookingParticipant",
    "EcoEvidence",
    "EcoExperience",
    "EcoExperienceImage",
    "EcoPractice",
    "EcoSafetyRule",
    "EcoSeasonRestriction",
    "EcoVerification",
    "EcoWishlist",
    "EcoPromotionAnalytics",
    "EcoPromotionPackage",
    "EcoSponsoredPromotion",

    # Rentals
    "RentalVehicle",
    "RentalBooking",
    "RentalLocation",
    "RentalRatePlan",
    "RentalVehicleImage",
    "RentalVehicleDocument",
    "RentalAvailabilityBlock",
    "RentalEligibilityCheck",
    "RentalInspection",
    "RentalConditionEvidence",
    "RentalIncident",
    "RentalDeposit",

    # Shared Infrastructure
    "ListingReference",
    "BookingReference",
    "PaymentTransaction",
    "AdvertisingPayment",
    "PaymentRefund",
    "ProviderSettlement",
    "Notification",
    "Review",
    "Wishlist",
    "PromotionPackage",
    "PromotionCampaign",
    "PromotionEvent",
    "BookingStatusHistory",
    "AdminAuditLog",

    # Travel Community
    "CommunityPost",
    "CommunityPostMedia",
    "CommunityPostLike",
    "CommunityComment",
    "CommunitySavedPost",
    "CommunityFollow",
    "CommunityReport",
    "CommunityHashtag",
    "CommunityPostHashtag",

    # AI Travel Planner
    "AIChatSession",
    "AIChatMessage",
    "TravelPreference",
    "TravelPlan",
    "TravelPlanDay",
    "TravelPlanItem",
    "AIPendingAction",
    "AIActionAudit",
]
