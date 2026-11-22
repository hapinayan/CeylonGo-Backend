
import os
from pathlib import Path

from dotenv import load_dotenv

# ==========================================
# ENVIRONMENT CONFIGURATION
# ==========================================

_env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=_env_path)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# ==========================================
# AUTHENTICATION ROUTER IMPORTS
# ==========================================

from app.routers.auth import router as auth_router

# ==========================================
# ARTISAN MODULE ROUTER IMPORTS
# ==========================================

from app.routers.artisan_listings import (
    router as artisan_listings_router,
)
from app.routers.showcase_items import (
    router as showcase_items_router,
)
from app.routers.artisan_reservations import (
    router as artisan_reservations_router,
)
from app.routers.admin_artisan import (
    router as admin_artisan_router,
)
from app.routers.artisan_images import (
    artisan_uploads_router,
    router as artisan_images_router,
)
from app.routers.artisan_business_hours import (
    router as artisan_business_hours_router,
)
from app.routers.artisan_dashboard import (
    router as artisan_dashboard_router,
)
from app.routers.artisan_reviews import (
    router as artisan_reviews_router,
)
from app.routers.artisan_wishlists import (
    router as artisan_wishlists_router,
)
from app.routers.promotions import (
    router as promotions_router,
)

# ==========================================
# HOMESTAY MODULE ROUTER IMPORTS
# ==========================================

from app.routers.homestay import (
    router as homestay_router,
)
from app.routers.booking import (
    router as booking_router,
)
from app.routers.promotion import (
    router as promotion_router,
)

# ==========================================
# SERVICE PROVIDER MODULE ROUTER IMPORTS
# ==========================================

from app.routers.provider_otp import (
    router as provider_otp_router,
)
from app.routers.provider_registration import (
    router as provider_registration_router,
)
from app.routers.admin_providers import (
    router as admin_providers_router,
)
from app.routers.provider_auth import (
    router as provider_auth_router,
)
from app.routers.provider_dashboard import (
    router as provider_dashboard_router,
)
from app.routers.provider_reviews import (
    router as provider_reviews_router,
)
from app.routers.provider_community import (
    router as provider_community_router,
)

# ==========================================
# ACTIVITIES & EXPERIENCES ROUTER IMPORTS
# ==========================================

from app.routers.provider_activities import (
    router as provider_activities_router,
)
from app.routers.admin_activities import (
    router as admin_activities_router,
)
from app.routers.activity_slots import (
    router as activity_slots_router,
)
from app.routers.activity_bookings import (
    router as activity_bookings_router,
)

# ==========================================
# ECO-TOURISM ROUTER IMPORTS
# ==========================================

from app.routers.eco_experiences import (
    admin_eco_router,
    router as eco_experiences_router,
    uploads_router,
)
from app.routers.eco_promotions import (
    admin_eco_promotions_router,
    eco_promotions_router,
)
from app.routers.wishlists import (
    router as wishlists_router,
)

# ==========================================
# RENTAL MODULE ROUTER IMPORTS
# ==========================================

from app.routers.rental_vehicles import (
    router as rental_vehicles_router,
)
from app.routers.rental_bookings import (
    router as rental_bookings_router,
)

# ==========================================
# TOUR GUIDE MODULE ROUTER IMPORTS
# ==========================================

from app.routers.tour_guide import (
    router as tour_guide_router,
)

# ==========================================
# FASTAPI APPLICATION
# ==========================================

app = FastAPI(
    title="CeylonGo API",
    description="Backend API for the CeylonGo tourism platform",
    version="1.0.0",
)

# ==========================================
# CORS CONFIGURATION
# ==========================================

def get_allowed_origins() -> list[str]:
    # Default Angular development origins
    origins = [
        "http://localhost:4200",
        "http://127.0.0.1:4200",
    ]

    # Read additional comma-separated origins
    # from environment configuration
    custom_origins = os.getenv("CORS_ORIGINS", "")

    if custom_origins:
        for origin in custom_origins.split(","):
            cleaned = origin.strip()

            if cleaned and cleaned not in origins:
                origins.append(cleaned)

    return origins


# ==========================================
# CORS MIDDLEWARE
# ==========================================

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_allowed_origins(),
    allow_credentials=True,
    allow_methods=[
        "GET",
        "POST",
        "PUT",
        "DELETE",
        "PATCH",
        "OPTIONS",
    ],
    allow_headers=[
        "Authorization",
        "Content-Type",
        "Accept",
        "Origin",
        "X-Requested-With",
    ],
)

# ==========================================
# REGISTER ROUTERS
# ==========================================

# Authentication
app.include_router(auth_router)

# ==========================================
# ARTISAN MODULE
# ==========================================

app.include_router(artisan_listings_router)
app.include_router(showcase_items_router)
app.include_router(artisan_images_router)
app.include_router(artisan_uploads_router)
app.include_router(artisan_business_hours_router)
app.include_router(artisan_dashboard_router)
app.include_router(artisan_reviews_router)
app.include_router(artisan_wishlists_router)
app.include_router(artisan_reservations_router)
app.include_router(admin_artisan_router)
app.include_router(promotions_router)

# ==========================================
# HOMESTAY MODULE
# ==========================================

app.include_router(homestay_router)
app.include_router(booking_router)
app.include_router(promotion_router)

# ==========================================
# SERVICE PROVIDER MODULE
# ==========================================

app.include_router(provider_otp_router)
app.include_router(provider_registration_router)
app.include_router(admin_providers_router)
app.include_router(provider_auth_router)
app.include_router(provider_dashboard_router)
app.include_router(provider_reviews_router)
app.include_router(provider_community_router)

# ==========================================
# ACTIVITIES & EXPERIENCES MODULE
# ==========================================

app.include_router(provider_activities_router)
app.include_router(admin_activities_router)
app.include_router(activity_slots_router)
app.include_router(activity_bookings_router)

# ==========================================
# ECO-TOURISM MODULE
# ==========================================

app.include_router(eco_experiences_router)
app.include_router(admin_eco_router)
app.include_router(uploads_router)
app.include_router(wishlists_router)
app.include_router(eco_promotions_router)
app.include_router(admin_eco_promotions_router)

# ==========================================
# RENTAL MODULE
# ==========================================

app.include_router(rental_vehicles_router)
app.include_router(rental_bookings_router)

# ==========================================
# TOUR GUIDE MODULE
# ==========================================

app.include_router(tour_guide_router)

# ==========================================
# HEALTH CHECK API
# ==========================================

@app.get(
    "/api/v1/health",
    tags=["Health"],
)
def health_check():
    return {
        "status": "healthy",
        "service": "CeylonGo Backend",
    }
