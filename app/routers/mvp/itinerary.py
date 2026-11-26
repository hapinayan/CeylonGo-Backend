"""
CeylonGo Academic MVP — AI Itinerary Router
Endpoints for Bounded Multi-day Smart Itinerary Planning across Sri Lanka.
"""

from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, HTTPException, status

from app.routers.mvp.homestays import DB, Traveler
from app.routers.mvp.planner import limiter
from app.schemas.mvp import ItineraryDayPlan, ItineraryRequest, ItineraryResponse
from app.services.mvp_planner import run_turn

router = APIRouter(prefix="/itinerary", tags=["MVP AI Itinerary"])


@router.post("/generate", response_model=ItineraryResponse, status_code=status.HTTP_200_OK)
def generate_itinerary(req: ItineraryRequest, db: DB, traveler: Traveler):
    """Keep the existing response shape for the one-shot itinerary client."""
    user_id = traveler.id
    limiter.check_identifier(f"planner-user:{user_id}")
    destination = req.destination.strip()
    budget = {"budget": 3500, "moderate": 8500, "luxury": 22000}[req.budget_level]
    interests = ", ".join(item[:80] for item in req.interests[:10])
    prompt = (
        f"Generate a preliminary {req.duration_days}-day itinerary for one traveler in "
        f"{destination}, with an assumed LKR {budget * req.duration_days} budget and "
        f"interests {interests}. Dates are unknown; leave service costs unquoted."
    )
    _, itinerary, listings, quotes, _ = run_turn(db, user_id, [], prompt)
    if itinerary is None:
        raise HTTPException(422, "Use a planner conversation to supply missing trip details")
    if len(itinerary.plan.days) != req.duration_days:
        raise HTTPException(502, "Gemini returned the wrong itinerary duration")
    days = []
    used_quotes = set()
    for day in itinerary.plan.days:
        refs = set(day.service_refs)
        amount = sum((quotes[ref].quote.total_lkr for ref in refs & quotes.keys() - used_quotes),
                     Decimal(0))
        used_quotes.update(refs & quotes.keys())
        categories = {item.category for ref, item in listings.items() if ref in refs}
        categories.update(item.quote.category for ref, item in quotes.items() if ref in refs)
        days.append(ItineraryDayPlan(
            day=day.day, title=day.destination, morning=day.morning, afternoon=day.afternoon,
            evening=day.evening, recommended_categories=sorted(categories),
            estimated_day_cost_lkr=amount + day.estimated_food_lkr
            + day.estimated_transport_lkr + day.estimated_other_lkr,
        ))
    return ItineraryResponse(
        destination=destination, duration_days=req.duration_days, budget_level=req.budget_level,
        total_estimated_cost_lkr=itinerary.estimated_total_lkr, days=days,
        tips=["Preliminary plan for one traveler. Check dated service quotes before booking."],
        generated_at=datetime.now(UTC),
    )
