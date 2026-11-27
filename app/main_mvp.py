"""
CeylonGo Academic MVP — Dedicated Lean FastAPI Runtime
Mounts ONLY the 17+1 Academic MVP routers.
Excludes all legacy vertical silos (artisan, showcase_items, eco_*, homestay_campaigns, promotions, wishlists, reviews, etc.).
"""

from contextlib import asynccontextmanager
import os
from pathlib import Path
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Load .env reliably
_env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=_env_path)

from app.routers.mvp import (
    admin_router,
    auth_router,
    bookings_router,
    community_router,
    itinerary_router,
    listings_router,
    payments_router,
    providers_router,
)
from app.routers.mvp.homestays import router as homestays_router
from app.routers.mvp.activities import router as activities_router
from app.routers.mvp.planner import router as planner_router
from app.routers.mvp.artisans import router as artisans_router
from app.routers.mvp.tour_guides import router as tour_guides_router
from app.routers.mvp.rentals import router as rentals_router
from app.routers.mvp.listing_management import router as listing_management_router
from app.routers.mvp.account import router as account_router
from app.routers.mvp.provider_account import router as provider_account_router
from app.routers.mvp.wallet import router as wallet_router
from app.routers.mvp.map import router as map_router
from app.routers.weather import router as weather_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup validation
    print("=== CeylonGo Academic MVP FastAPI Runtime Starting ===")
    print("Database Schema: Unified 17 Application Tables + 1 Ledger")
    print("Legacy Vertical Silos: Excluded")
    yield
    print("=== CeylonGo Academic MVP FastAPI Runtime Shutting Down ===")


def create_mvp_app() -> FastAPI:
    app = FastAPI(
        title="CeylonGo Academic MVP API",
        version="1.1.0",
        description="Unified, simplified backend API for CeylonGo Academic MVP. Supports Traveler, Provider, Admin, Homestay, Rental, Adventure, Guide, Simulated Payments, and AI Itinerary.",
        lifespan=lifespan,
    )

    # Configure CORS for Angular and Flutter clients (no wildcard origins)
    default_origins = (
        "http://localhost:4200,"
        "http://127.0.0.1:4200,"
        "http://localhost:4201,"
        "http://127.0.0.1:4201,"
        "http://localhost:8420,"
        "http://127.0.0.1:8420"
    )
    allowed_origins = [
        origin.strip()
        for origin in os.getenv("CORS_ORIGINS", default_origins).split(",")
        if origin.strip() and origin.strip() != "*"
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Wallet-Event", "Retry-After"],
    )

    # Health & Meta endpoints
    @app.get("/", tags=["System"])
    def root():
        return {
            "name": "CeylonGo Academic MVP API",
            "version": "1.1.0",
            "schema_profile": "17+1 Unified MVP",
            "status": "online",
        }

    @app.get("/health", tags=["System"])
    def health_check():
        return {
            "status": "healthy",
            "runtime": "academic_mvp",
            "database_profile": "CeylonGoDB",
        }

    # Mount unified MVP routers under both /api and /api/v1 for 100% client compatibility
    for prefix_base in ["/api", "/api/v1"]:
        app.include_router(auth_router, prefix=prefix_base)
        app.include_router(account_router, prefix=prefix_base)
        app.include_router(providers_router, prefix=prefix_base)
        app.include_router(provider_account_router, prefix=prefix_base)
        app.include_router(listings_router, prefix=prefix_base)
        app.include_router(bookings_router, prefix=prefix_base)
        app.include_router(homestays_router, prefix=prefix_base)
        app.include_router(activities_router, prefix=prefix_base)
        app.include_router(planner_router, prefix=prefix_base)
        app.include_router(artisans_router, prefix=prefix_base)
        app.include_router(tour_guides_router, prefix=prefix_base)
        app.include_router(rentals_router, prefix=prefix_base)
        app.include_router(listing_management_router, prefix=prefix_base)
        app.include_router(payments_router, prefix=prefix_base)
        app.include_router(community_router, prefix=prefix_base)
        app.include_router(admin_router, prefix=prefix_base)
        app.include_router(itinerary_router, prefix=prefix_base)

    app.include_router(wallet_router, prefix="/api/v1")
    app.include_router(map_router, prefix="/api/v1")
    app.include_router(weather_router)

    return app


app = create_mvp_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main_mvp:app", host="0.0.0.0", port=8000, reload=True)
