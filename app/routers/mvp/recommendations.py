from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response

from app.core.mvp_security import get_optional_mvp_user
from app.models.mvp import MVPUser
from app.routers.mvp.homestays import DB, Traveler
from app.schemas.mvp_recommendations import (
    ActivityInput,
    ActivityResult,
    Category,
    HomeRecommendations,
    PersonalizationSettings,
    RecommendationGroup,
    RecommendationType,
)
from app.services import (
    mvp_recommendation_activity as activity,
    mvp_recommendations as rec,
)

router = APIRouter(prefix="/recommendations", tags=["MVP Recommendations"])
OptionalUser = Annotated[MVPUser | None, Depends(get_optional_mvp_user)]
District = Annotated[str | None, Query(min_length=1, max_length=80)]
Limit = Annotated[int, Query(ge=1, le=30)]
PublicID = Annotated[int, Path(gt=0, le=2**63 - 1)]


def group(engine, kind, limit, source=None):
    ranked = rec.select_diverse(engine.rank(kind, source), limit)
    return RecommendationGroup(
        recommendation_type=kind,
        items=engine.cards([ranked])[0],
    )


@router.get("/popular", response_model=RecommendationGroup)
def popular(
    db: DB,
    category: Category | None = None,
    district: District = None,
    limit: Limit = 12,
):
    engine = rec.RecommendationEngine(db, category=category, district=district)
    return group(engine, "popular_experiences", limit)


@router.get("/me", response_model=RecommendationGroup)
def personalized(
    db: DB,
    user: Traveler,
    response: Response,
    category: Category | None = None,
    district: District = None,
    recommendation_type: RecommendationType = "recommended_for_you",
    limit: Limit = 12,
):
    response.headers["Cache-Control"] = "private, no-store"
    engine = rec.RecommendationEngine(db, user.id, category, district)
    return group(engine, recommendation_type, limit)


@router.get("/home", response_model=HomeRecommendations)
def home(
    db: DB,
    user: OptionalUser,
    response: Response,
    category: Category | None = None,
    district: District = None,
    limit: Limit = 6,
):
    response.headers["Cache-Control"] = "private, no-store"
    traveler_id = user.id if user and user.role == "traveler" else None
    engine = rec.RecommendationEngine(db, traveler_id, category, district)
    kinds = (
        rec.SECTION_TYPES
        if traveler_id is not None
        else (
            "popular_experiences",
            "discover_something_new",
        )
    )
    # Leave the broad sections until last so they cannot consume narrow matches.
    allocation_order = (
        "because_you_viewed",
        "based_on_your_interests",
        "favorite_destinations",
        "recommended_for_you",
        "popular_experiences",
        "discover_something_new",
    )
    sections = {}
    used = set()
    for kind in allocation_order:
        if kind in kinds:
            ranked = [entry for entry in engine.rank(kind) if entry[0].id not in used]
            sections[kind] = rec.select_diverse(ranked, limit)
            used.update(entry[0].id for entry in sections[kind])
    cards = engine.cards([sections[kind] for kind in kinds])
    return HomeRecommendations(
        sections=[
            RecommendationGroup(recommendation_type=kind, items=items)
            for kind, items in zip(kinds, cards)
        ]
    )


@router.get("/similar/{listing_id}", response_model=RecommendationGroup)
def similar(
    listing_id: PublicID,
    db: DB,
    category: Category | None = None,
    district: District = None,
    limit: Limit = 12,
):
    engine = rec.RecommendationEngine(
        db, category=category, district=district, source_id=listing_id
    )
    source = engine.catalog.get(listing_id)
    if source is None:
        raise HTTPException(404, "Published listing not found")
    return group(engine, "similar_services", limit, source)


@router.post("/activity", response_model=ActivityResult)
def record_activity(payload: ActivityInput, db: DB, user: Traveler):
    activity.require_schema(db)
    return activity.record(
        db, user.id, payload, lambda key: rec.eligible_category(db, key)
    )


@router.get("/settings", response_model=PersonalizationSettings)
def get_settings(db: DB, user: Traveler, response: Response):
    response.headers["Cache-Control"] = "private, no-store"
    return PersonalizationSettings(
        behavioral_personalization=activity.installed(db)
        and activity.consent(db, user.id),
    )


@router.patch("/settings", response_model=PersonalizationSettings)
def update_settings(payload: PersonalizationSettings, db: DB, user: Traveler):
    activity.require_schema(db)
    activity.set_consent(db, user.id, payload.behavioral_personalization)
    return payload


@router.delete("/history", status_code=204)
def clear_history(db: DB, user: Traveler):
    activity.require_schema(db)
    activity.clear(db, user.id)
    return Response(status_code=204)
