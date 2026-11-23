"""
CeylonGo Academic MVP — AI Itinerary Router
Endpoints for Bounded Multi-day Smart Itinerary Planning across Sri Lanka.
"""

from fastapi import APIRouter, status

from app.schemas.mvp import ItineraryRequest, ItineraryResponse
from app.services.mvp_services import MVPAIItineraryService

router = APIRouter(prefix="/itinerary", tags=["MVP AI Itinerary"])


@router.post("/generate", response_model=ItineraryResponse, status_code=status.HTTP_200_OK)
def generate_itinerary(req: ItineraryRequest):
    return MVPAIItineraryService.generate_itinerary(req)
