"""
CeylonGo Academic MVP — Unified Routers Export
"""

from app.routers.mvp.auth import router as auth_router
from app.routers.mvp.providers import router as providers_router
from app.routers.mvp.listings import router as listings_router
from app.routers.mvp.bookings import router as bookings_router
from app.routers.mvp.payments import router as payments_router
from app.routers.mvp.community import router as community_router
from app.routers.mvp.admin import router as admin_router
from app.routers.mvp.itinerary import router as itinerary_router

__all__ = [
    "auth_router",
    "providers_router",
    "listings_router",
    "bookings_router",
    "payments_router",
    "community_router",
    "admin_router",
    "itinerary_router",
]
