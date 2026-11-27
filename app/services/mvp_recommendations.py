"""Deterministic content-based ranking for the shared MVP catalog."""

import json
import os
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import get_args

from sqlalchemy import and_, case, false, func, or_, select, true
from sqlalchemy.orm import aliased, selectinload, undefer

from app.models.mvp import (
    MVPAvailabilitySlot as Slot,
    MVPBooking as Booking,
    MVPHomestayWishlist as Wishlist,
    MVPInventoryUnit as Unit,
    MVPListing as Listing,
    MVPListingVerification as Verification,
)
from app.routers.mvp.activities import published_listings
from app.schemas.mvp_activity import ActivityMetadata
from app.schemas.mvp_recommendations import Category, RecommendationCard, normalize
from app.services import (
    mvp_activity,
    mvp_homestay,
    mvp_listing_verification as verification,
)
from app.services import mvp_recommendation_activity as activity, mvp_rental
from app.services.mvp_account import now as utc_now

WEIGHTS = {
    "preferences": 30,
    "search": 25,
    "view": 15,
    "wishlist": 15,
    "booking": 10,
    "budget": 5,
}
CANDIDATES_PER_CATEGORY = 100
BOOKING_STATUSES = ("confirmed", "completed")
HALF_LIFE = timedelta(days=30)
DETAIL_ROUTES = {
    "homestay": "homestays",
    "adventure": "activities",
    "guide": "tour-guides",
    "bike": "rental-vehicles",
    "bicycle": "rental-vehicles",
    "artisan": "artisan-listings",
}
REASONS = {
    "search": "Matches your recent searches",
    "view": "Similar to services you recently viewed",
    "wishlist": "Based on your saved services",
    "booking": "Matches services you previously booked",
    "budget": "Matches your preferred budget",
}
INTEREST_TERMS = {
    "nature": ("nature", "forest", "rainforest", "botanical", "nature eco"),
    "wildlife": ("wildlife", "safari", "birdwatching"),
    "adventure": ("adventure", "hiking", "rafting", "trekking", "surfing", "zipline"),
    "beaches": ("beach", "beaches", "coastal", "surfing"),
    "mountains hiking": ("mountain", "mountains", "hike", "hiking", "trekking"),
    "culture": ("culture", "cultural", "heritage", "temple", "craft"),
    "cultural": ("culture", "cultural", "heritage", "temple"),
    "heritage": ("heritage", "historic", "temple", "archaeology"),
    "local food": ("cooking", "food", "cuisine", "culinary"),
    "artisan handicrafts": (
        "artisan",
        "handicrafts",
        "craft",
        "pottery",
        "weaving",
        "batik",
    ),
    "village experiences": ("village", "rural", "village experiences"),
    "local experiences": ("village", "artisan", "cultural", "local food"),
    "eco tourism": ("eco", "eco tourism", "conservation", "sustainable"),
    "cycling": ("cycling", "bicycle", "bike trail"),
    "photography": ("photography", "photo tour", "birdwatching"),
    "relaxation": ("relaxation", "relaxing", "wellness", "spa", "retreat"),
    "walking": ("walking", "walking tour", "hiking"),
    "motorbike": ("bike", "motorbike", "motorcycle"),
    "family": ("family", "family friendly"),
}
SECTION_TYPES = (
    "recommended_for_you",
    "because_you_viewed",
    "based_on_your_interests",
    "favorite_destinations",
    "discover_something_new",
    "popular_experiences",
)


def object_data(raw):
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def mapping(value):
    return value if isinstance(value, dict) else {}


def strings(value):
    return (
        [item for item in value if isinstance(item, str) and normalize(item)]
        if (isinstance(value, list))
        else []
    )


def labels(value):
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return strings(value)
    return []


def money(value):
    try:
        result = Decimal(str(value))
        if result.is_finite() and 0 < result <= Decimal("9999999999.99"):
            return result
    except InvalidOperation:
        pass
    return None


def recency(timestamp, now):
    return 2 ** (-max(now - timestamp, timedelta(0)) / HALF_LIFE)


def location_matches(location, term):
    term = normalize(term)
    return bool(term) and any(f" {term} " in f" {value} " for value in location)


@dataclass(frozen=True)
class ListingFacts:
    listing: Listing
    content: str
    topics: frozenset[str]
    location: tuple[str, str]
    activity_category: str | None
    rental_subtype: str | None
    price: Decimal | None
    pricing_unit: str | None
    minimum_order: int | None
    cover: str | None
    guide_units: frozenset[int]

    @property
    def id(self):
        return self.listing.id

    @property
    def category(self):
        return self.listing.category


def facts(listing, details):
    category = listing.category
    homestay = object_data(listing.homestay_metadata) if category == "homestay" else {}
    adventure = mapping(details.get("activity"))
    guide = mapping(details.get("guide"))
    rental = mapping(details.get("rental"))
    artisan = mapping(details.get("artisan"))
    fields = [
        category,
        listing.title,
        listing.description,
        *labels(homestay.get("property_type")),
        *strings(homestay.get("amenities")),
        *labels(adventure.get("category")),
        *strings(adventure.get("highlights")),
        *strings(guide.get("categories")),
        *labels(rental.get("subtype")),
        *labels(artisan.get("craft_category")),
    ]
    content = normalize(" ".join(fields))
    topics = frozenset(
        topic
        for topic, terms in INTEREST_TERMS.items()
        if any(f" {term} " in f" {content} " for term in terms)
    )
    price = money(listing.price)
    pricing_unit = {
        "homestay": "per_night",
        "adventure": "per_participant",
        "bike": "per_day",
        "bicycle": "per_day",
    }.get(category)
    minimum_order = 1
    if category == "adventure":
        try:
            minimum_order = ActivityMetadata.model_validate(adventure).min_participants
        except ValueError:
            minimum_order = None
    guide_units = set()
    if category == "homestay":
        rates = [
            money(object_data(room.room_metadata).get("nightly_price")) or price
            for room in listing.units
            if room.is_active
        ]
        price = min((rate for rate in rates if rate is not None), default=price)
    elif category == "guide":
        packages = []
        for unit in listing.units:
            if not unit.is_active:
                continue
            try:
                package = verification.guide_package(unit)
            except (AttributeError, TypeError, ValueError):
                continue
            if package is not None:
                packages.append((package.price, unit.id, package.pricing_type))
                guide_units.add(unit.id)
        price, _, pricing_unit = min(packages, default=(None, None, None))
    elif category == "artisan":
        price = None

    cover_id = homestay.get("cover_photo_id") or details.get("cover_photo_id")
    photos = sorted(
        verification.visible_media(listing),
        key=lambda photo: (photo.id != cover_id, photo.sort_order, photo.id),
    )
    cover = None
    for photo in photos:
        if photo.storage_key.startswith(verification.PHOTO_PREFIX):
            cover = f"/api/v1/listings/{listing.id}/photos/{photo.id}"
        elif category in {"homestay", "guide"}:
            cover = mvp_homestay.listing_image_url(
                photo.storage_key,
                os.getenv("MVP_LISTING_MEDIA_BASE_URL", "").rstrip("/"),
            )
        if cover:
            break
    return ListingFacts(
        listing,
        content,
        topics,
        (normalize(listing.district), normalize(listing.locality)),
        adventure.get("category"),
        rental.get("subtype"),
        price,
        pricing_unit if price is not None else None,
        minimum_order,
        cover,
        frozenset(guide_units),
    )


def eligible_category(db, listing_id):
    return db.scalar(
        published_listings(*get_args(Category))
        .with_only_columns(
            Listing.category,
        )
        .where(Listing.id == listing_id)
    )


def load_catalog(db, category=None, district=None, sources=()):
    base = published_listings(*get_args(Category)).options(
        undefer(Listing.homestay_metadata),
        selectinload(Listing.units).undefer(Unit.room_metadata),
    )
    target = base
    if district:
        term = " ".join(district.lower().split())
        target = target.where(
            or_(
                func.lower(Listing.district).contains(term, autoescape=True),
                func.lower(Listing.locality).contains(term, autoescape=True),
            )
        )
    # Partition candidates so a high-volume category cannot crowd out all others.
    partition = target.with_only_columns(
        Listing.id,
        func.row_number()
        .over(partition_by=Listing.category, order_by=Listing.id.desc())
        .label("position"),
    )
    if category:
        partition = partition.where(Listing.category == category)
    subset = partition.subquery()
    eligible_ids = select(subset.c.id).where(
        subset.c.position <= CANDIDATES_PER_CATEGORY
    )
    candidate_query = Listing.id.in_(eligible_ids)
    if sources:
        candidate_query = or_(candidate_query, Listing.id.in_(sources))
    rows = db.scalars(base.where(candidate_query).order_by(Listing.id)).all()
    ids = [row.id for row in rows]
    extra = (
        {
            row.listing_id: object_data(row.details)
            for row in db.scalars(
                select(Verification).where(Verification.listing_id.in_(ids))
            )
        }
        if ids
        else {}
    )
    # A source may appear outside the request filters and still inform similarity.
    selected_ids = set(db.scalars(eligible_ids)) if sources else set(ids)
    catalog = {row.id: facts(row, extra.get(row.id, {})) for row in rows}
    return catalog, [item for item in catalog.values() if item.id in selected_ids]


def popularity(db, ids, now):
    if not ids:
        return {}
    booked = dict(
        db.execute(
            select(Booking.listing_id, func.count())
            .where(
                Booking.listing_id.in_(ids),
                Booking.status.in_(BOOKING_STATUSES),
                Booking.created_at > now - activity.RETENTION,
            )
            .group_by(Booking.listing_id)
        ).all()
    )
    saved = dict(
        db.execute(
            select(Wishlist.listing_id, func.count())
            .where(
                Wishlist.listing_id.in_(ids),
                Wishlist.is_saved == true(),
            )
            .group_by(Wishlist.listing_id)
        ).all()
    )
    return {
        listing_id: 2 * booked.get(listing_id, 0) + saved.get(listing_id, 0)
        for listing_id in ids
    }


def saved_and_booked(db, user_id):
    saved = list(
        db.scalars(
            select(Wishlist.listing_id)
            .where(
                Wishlist.traveler_id == user_id,
                Wishlist.is_saved == true(),
            )
            .order_by(Wishlist.listing_id.desc())
            .limit(activity.HISTORY_LIMIT)
        )
    )
    latest = func.max(Booking.created_at)
    booked = dict(
        db.execute(
            select(Booking.listing_id, latest)
            .where(
                Booking.traveler_id == user_id,
                Booking.status.in_(BOOKING_STATUSES),
            )
            .group_by(Booking.listing_id)
            .order_by(
                latest.desc(),
                Booking.listing_id.desc(),
            )
            .limit(activity.HISTORY_LIMIT)
        ).all()
    )
    return saved, booked


def budget_score(item, preferences):
    if (
        item.price is None
        or item.minimum_order is None
        or item.listing.currency != "LKR"
    ):
        return None
    ceiling = preferences.get("budget_max")
    if type(ceiling) is not int:
        return None
    # Existing settings describe a trip budget. One minimum order gives a lower
    # bound on the trip cost. The trip's budget floor says nothing about one card.
    return float(item.price * item.minimum_order <= ceiling)


def explicit_score(item, preferences):
    destinations = strings(preferences.get("preferred_destinations"))
    interests = strings(preferences.get("interests"))
    categories = strings(preferences.get("accommodation_preferences"))
    categories += strings(preferences.get("transport_preferences"))
    categories += labels(preferences.get("travel_style"))
    inputs = [(value, "interest") for value in interests]
    inputs += [
        (value, "preference")
        for value in categories
        if normalize(value) not in {"other", "budget"}
    ]
    inputs += [(value, "destination") for value in destinations]
    matches = []
    for value, kind in inputs:
        term = normalize(value)
        if kind == "destination":
            if location_matches(item.location, value):
                matches.append("Matches a preferred destination")
        elif term in item.topics or f" {term} " in f" {item.content} ":
            matches.append(
                f"Matches your {kind} in {term}"
                if kind == "interest"
                else f"Matches your {term} preference"
            )
    if not inputs:
        return None, None
    # Keep a match meaningful even when the traveler selected many interests.
    return min(1.0, len(matches) / min(2, len(inputs))), next(iter(matches), None)


def search_score(item, event):
    terms = set(normalize(event.search_query or "").split())
    content = set(item.content.split()) | set(" ".join(item.location).split())
    filters = object_data(event.filters)
    checks = []
    if terms:
        checks.append(len(terms & content) / len(terms))
    if event.category:
        checks.append(float(item.category == event.category))
    for location in (event.district, event.destination):
        if location:
            checks.append(float(location_matches(item.location, location)))
    for key in ("activity_category", "rental_subtype"):
        if filters.get(key):
            checks.append(float(getattr(item, key) == filters[key]))
    if filters.get("min_price") is not None or filters.get("max_price") is not None:
        if item.price is not None and item.pricing_unit == filters.get("pricing_unit"):
            checks.append(
                float(
                    filters.get("min_price", 0)
                    <= item.price
                    <= filters.get("max_price", 10_000_000_000)
                )
            )
    return sum(checks) / len(checks) if checks else None


def similarity(item, source):
    if item.id == source.id:
        return 1.0
    shared = item.topics & source.topics
    topic_score = len(shared) / max(1, len(item.topics | source.topics))
    same_place = bool((set(item.location) & set(source.location)) - {""})
    return (
        0.45 * (item.category == source.category)
        + 0.4 * topic_score
        + 0.15 * same_place
    )


def strongest(item, evidence):
    return max(
        (similarity(item, source) * weight for source, weight in evidence), default=None
    )


def search_signal(item, events, now):
    return max(
        (
            score * recency(event.created_at, now)
            for event in events
            if (score := search_score(item, event)) is not None
        ),
        default=None,
    )


def select_diverse(ranked, limit):
    remaining = list(ranked)
    categories = Counter()
    districts = Counter()
    result = []
    while remaining and len(result) < limit:
        index = max(
            range(len(remaining)),
            key=lambda i: (
                remaining[i][1]
                - min(6, 2 * categories[remaining[i][0].category])
                - min(4, 2 * districts[remaining[i][0].location[0]]),
                remaining[i][1],
                -remaining[i][0].id,
            ),
        )
        item, score, reason = remaining.pop(index)
        result.append((item, score, reason))
        categories[item.category] += 1
        districts[item.location[0]] += 1
    return result


def bookable_ids(db, items, now):
    """Check an indicative window for stays/rentals and future scheduled slots."""
    if not items:
        return set()
    result = set()
    next_day = mvp_homestay.local_date(now.replace(tzinfo=UTC)) + timedelta(days=1)
    start = mvp_homestay.utc_naive(
        datetime.combine(
            next_day,
            time.min,
            mvp_homestay.SRI_LANKA,
        )
    )
    end = start + timedelta(days=1)
    rental_ids = [item.id for item in items if item.category in {"bike", "bicycle"}]
    if rental_ids:
        units = mvp_rental.available_units(Listing.id, start, end, Listing)
        result.update(
            db.scalars(
                select(Listing.id).where(
                    Listing.id.in_(rental_ids),
                    units.exists(),
                )
            )
        )
    stays = [item.id for item in items if item.category == "homestay"]
    if stays:
        closed = aliased(Slot)
        blocked = (
            select(closed.id)
            .where(
                closed.unit_id == Unit.id,
                closed.is_open == false(),
                closed.starts_at < end,
                closed.ends_at > start,
            )
            .correlate(Unit)
            .exists()
        )
        reservations = mvp_rental.unit_reservations(Unit.id, start, end).correlate(Unit)
        result.update(
            db.scalars(
                select(Unit.listing_id)
                .join(
                    Slot,
                    and_(Slot.unit_id == Unit.id, Slot.listing_id == Unit.listing_id),
                )
                .where(
                    Unit.listing_id.in_(stays),
                    Unit.is_active == true(),
                    Slot.is_open == true(),
                    Slot.starts_at <= start,
                    Slot.ends_at >= end,
                    ~blocked,
                    ~reservations.exists(),
                )
                .distinct()
            )
        )
    groups = [
        item
        for item in items
        if item.category == "adventure" and item.minimum_order is not None
    ]
    if groups:
        required = {item.id: item.minimum_order for item in groups}
        capacity = mvp_activity.remaining_capacity()
        result.update(
            db.scalars(
                select(Slot.listing_id)
                .join(
                    Unit,
                    and_(Unit.id == Slot.unit_id, Unit.listing_id == Slot.listing_id),
                )
                .where(
                    Slot.listing_id.in_(required),
                    Slot.starts_at > now,
                    Slot.is_open == true(),
                    Unit.is_active == true(),
                    capacity >= case(required, value=Slot.listing_id, else_=1),
                )
                .distinct()
            )
        )
    guides = [item for item in items if item.category == "guide" and item.guide_units]
    if guides:
        other = aliased(Listing)
        occupied = (
            select(Booking.id)
            .join(other, other.id == Booking.listing_id)
            .where(
                other.category == "guide",
                other.provider_id == Listing.provider_id,
                Booking.status.in_(("pending", *BOOKING_STATUSES)),
                Booking.starts_at < Slot.ends_at,
                Booking.ends_at > Slot.starts_at,
            )
            .correlate(Listing, Slot)
        )
        query = (
            select(Slot.listing_id)
            .join(Listing, Listing.id == Slot.listing_id)
            .join(
                Unit, and_(Unit.id == Slot.unit_id, Unit.listing_id == Slot.listing_id)
            )
            .where(
                Slot.listing_id.in_([item.id for item in guides]),
                Unit.is_active == true(),
                Slot.is_open == true(),
                Slot.starts_at > now,
                ~occupied.exists(),
            )
            .distinct()
        )
        unit_ids = sorted(set().union(*(item.guide_units for item in guides)))
        # Leave room for the other bound values under SQL Server's parameter limit.
        for offset in range(0, len(unit_ids), 900):
            result.update(
                db.scalars(
                    query.where(
                        Slot.unit_id.in_(unit_ids[offset : offset + 900]),
                    )
                )
            )
    return result


def as_card(item, score, reason, bookable):
    listing = item.listing
    return RecommendationCard(
        listing_id=item.id,
        category=item.category,
        title=listing.title,
        short_description=listing.description[:240],
        district=listing.district,
        location=listing.locality,
        cover_image_url=item.cover,
        starting_price=item.price,
        currency=listing.currency if item.price is not None else None,
        pricing_unit=item.pricing_unit,
        recommendation_score=round(score, 2),
        recommendation_reason=reason,
        detail_route=f"/api/v1/{DETAIL_ROUTES[item.category]}/{item.id}",
        bookable=item.price is not None and item.id in bookable,
    )


class RecommendationEngine:
    def __init__(
        self, db, traveler_id=None, category=None, district=None, source_id=None
    ):
        verification.require_schema(db)
        self.db = db
        self.now = utc_now()
        self.preferences = {}
        events, saved, booked = [], [], {}
        if traveler_id is not None:
            raw, enabled = activity.preferences_and_consent(db, traveler_id)
            self.preferences = object_data(raw)
            if enabled:
                events = activity.history(db, traveler_id, self.now)
                saved, booked = saved_and_booked(db, traveler_id)
        self.searches = [event for event in events if event.event_type == "search"]
        views = [event for event in events if event.event_type == "view"]
        sources = set(saved) | set(booked) | {event.listing_id for event in views}
        if source_id is not None:
            sources.add(source_id)
        self.catalog, self.candidates = load_catalog(db, category, district, sources)
        viewed = {}
        for event in views:
            viewed.setdefault(event.listing_id, event.created_at)
        self.views = self.evidence(viewed)
        self.saved = [(self.catalog[key], 1.0) for key in saved if key in self.catalog]
        self.booked = self.evidence(booked)
        self.seen = set(viewed) | set(saved) | set(booked)
        self.popularity = popularity(
            db, [item.id for item in self.candidates], self.now
        )

    def evidence(self, dates):
        return [
            (self.catalog[key], recency(date, self.now))
            for key, date in dates.items()
            if key in self.catalog
        ]

    def popular(self, item):
        count = self.popularity[item.id]
        peak = max(self.popularity.values(), default=0)
        return (
            (100 * count / peak, "Popular among travelers")
            if count
            else (
                0.0,
                "Explore this published service",
            )
        )

    def destination(self, item):
        if any(
            location_matches(item.location, destination)
            for destination in strings(self.preferences.get("preferred_destinations"))
        ):
            return 1.0, "Matches a preferred destination"
        score = max(
            (
                recency(event.created_at, self.now)
                for event in self.searches
                if any(
                    value and location_matches(item.location, value)
                    for value in (event.district, event.destination)
                )
            ),
            default=0.0,
        )
        return score, "Matches a recently searched destination"

    def personalized(self, item, explicit=None):
        explicit, label = (
            explicit
            if explicit is not None
            else explicit_score(
                item,
                self.preferences,
            )
        )
        signals = {
            "preferences": explicit,
            "search": search_signal(item, self.searches, self.now),
            "view": strongest(item, self.views),
            "wishlist": strongest(item, self.saved),
            "booking": strongest(item, self.booked),
            "budget": budget_score(item, self.preferences),
        }
        weighted = {
            key: WEIGHTS[key] * value
            for key, value in signals.items()
            if value is not None and WEIGHTS[key] > 0
        }
        total = sum(weighted.values())
        if total <= 0:
            return 0.0, None
        strongest_key = max(weighted, key=weighted.get)
        return 100 * total / sum(WEIGHTS[key] for key in weighted), (
            label if strongest_key == "preferences" else REASONS[strongest_key]
        )

    def rank(self, kind, source=None):
        ranked = []
        for item in self.candidates:
            if kind == "similar_services":
                if item.id == source.id:
                    continue
                score, reason = (
                    100 * similarity(item, source),
                    "Similar to this service",
                )
            elif kind == "popular_experiences":
                score, reason = self.popular(item)
            elif kind == "because_you_viewed":
                if any(view.id == item.id for view, _ in self.views):
                    continue
                score = 100 * (strongest(item, self.views) or 0)
                reason = REASONS["view"]
            elif kind == "based_on_your_interests":
                interests = {"interests": self.preferences.get("interests")}
                value, reason = explicit_score(item, interests)
                score = 100 * (value or 0)
            elif kind == "favorite_destinations":
                value, reason = self.destination(item)
                score = 100 * value
            else:
                if kind == "discover_something_new" and item.id in self.seen:
                    continue
                score, reason = self.personalized(item)
                if kind == "discover_something_new" and not score:
                    score, reason = 0.0, "Discover a different published service"
            if score > 0 or kind in {"popular_experiences", "discover_something_new"}:
                ranked.append((item, score, reason))
        if kind == "recommended_for_you" and not ranked:
            return self.rank("popular_experiences")
        return ranked

    def cards(self, ranked_batches):
        bookable = bookable_ids(
            self.db,
            [item for ranked in ranked_batches for item, _, _ in ranked],
            self.now,
        )
        return [
            [as_card(item, score, reason, bookable) for item, score, reason in ranked]
            for ranked in ranked_batches
        ]
