import hashlib
import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.dialects import mssql
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateTable

from app.core.mvp_security import create_mvp_access_token
from app.database.mvp_session import get_mvp_db
from app.main_mvp import create_mvp_app
from app.models.mvp import (
    MVPAvailabilitySlot,
    MVPBase,
    MVPBooking,
    MVPHomestayWishlist,
    MVPInventoryUnit,
    MVPListing,
    MVPListingMedia,
    MVPListingVerification,
    MVPProviderProfile,
    MVPUser,
)
from app.models.mvp_recommendations import TravelerActivityEvent
from app.services import mvp_recommendation_activity as activity
from app.services.mvp_account import now
from scripts.bootstrap_mvp_schema import UPGRADES


@pytest.fixture
def catalog(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'recommendations.sqlite'}")
    MVPBase.metadata.create_all(engine)
    app = create_mvp_app()

    def database():
        with Session(engine) as session:
            # Compile SQL Server statements as the application runs them in SQLite.
            def compile_for_mssql(state):
                state.statement.compile(dialect=mssql.dialect())

            event.listen(session, "do_orm_execute", compile_for_mssql)
            yield session

    app.dependency_overrides[get_mvp_db] = database
    with Session(engine) as session:
        users = [
            MVPUser(
                email=f"rec{index}@example.com",
                full_name="Recommendation fixture",
                role=role,
                hashed_password="unused",
            )
            for index, role in enumerate(
                ("traveler", "traveler", "admin", "provider", "provider", "provider"),
            )
        ]
        users[-1].is_active = False
        session.add_all(users)
        providers = [
            MVPProviderProfile(
                user=user,
                district="Badulla",
                business_name="Fixture",
                phone="0771111111",
                verification_status=status,
            )
            for user, status in zip(users[3:], ("approved", "pending", "approved"))
        ]
        session.add_all(providers)
        records = [
            (
                "hike",
                "adventure",
                "Sunrise Ridge",
                "Badulla",
                "Ella",
                2600,
                "approved",
                True,
                0,
            ),
            (
                "trail",
                "adventure",
                "Mountain Trek",
                "Nuwara Eliya",
                "Haputale",
                6000,
                "approved",
                True,
                0,
            ),
            (
                "lodging",
                "homestay",
                "Village Homestay",
                "Jaffna",
                "Nallur",
                9100,
                "approved",
                True,
                0,
            ),
            (
                "guide",
                "guide",
                "Heritage Walk",
                "Jaffna",
                "Nallur",
                0,
                "approved",
                True,
                0,
            ),
            (
                "bike",
                "bike",
                "City Scooter",
                "Colombo",
                "Colombo",
                1200,
                "approved",
                True,
                0,
            ),
            (
                "bicycle",
                "bicycle",
                "Coastal Bicycle",
                "Galle",
                "Unawatuna",
                1200,
                "approved",
                True,
                0,
            ),
            (
                "artisan",
                "artisan",
                "Handwoven Batik",
                "Galle",
                "Galle",
                0,
                "approved",
                True,
                0,
            ),
            ("draft", "adventure", "Draft", "Jaffna", "Nallur", 1, "draft", False, 0),
            (
                "suspended",
                "guide",
                "Suspended",
                "Galle",
                "Galle",
                1,
                "suspended",
                False,
                0,
            ),
            (
                "unpublished",
                "bike",
                "Unpublished",
                "Galle",
                "Galle",
                1,
                "approved",
                False,
                0,
            ),
            (
                "unverified",
                "bicycle",
                "Unverified",
                "Galle",
                "Galle",
                1,
                "approved",
                True,
                1,
            ),
            (
                "inactive",
                "bicycle",
                "Inactive provider",
                "Galle",
                "Galle",
                1,
                "approved",
                True,
                2,
            ),
        ]
        listings = {}
        for (
            key,
            category,
            title,
            district,
            locality,
            price,
            status,
            active,
            owner,
        ) in records:
            listings[key] = MVPListing(
                provider=providers[owner],
                category=category,
                title=title,
                description=title,
                district=district,
                locality=locality,
                price=price,
                approval_status=status,
                is_active=active,
            )
        session.add_all(listings.values())
        session.flush()
        keys = {key: value.id for key, value in listings.items()}
        for key in ("hike", "trail"):
            session.add(
                MVPListingVerification(
                    listing_id=keys[key],
                    details=json.dumps(
                        {
                            "activity": {
                                "category": "hiking-trekking",
                                "min_participants": 2,
                                "meeting_point": "Station",
                                "duration_minutes": 120,
                                "max_participants": 6,
                            },
                        }
                    ),
                )
            )
        session.add(MVPListingVerification(listing_id=keys["artisan"], details="[]"))
        session.add_all(
            [
                MVPListingMedia(
                    listing_id=keys["hike"],
                    storage_key="mvp-photo/hidden",
                    sort_order=0,
                    is_visible=False,
                ),
                MVPListingMedia(
                    listing_id=keys["hike"],
                    storage_key="mvp-photo/show",
                    sort_order=1,
                    is_visible=True,
                ),
            ]
        )
        unit = MVPInventoryUnit(
            listing=listings["hike"],
            provider_id=providers[0].id,
            label="Group",
            capacity=6,
        )
        guide_unit = MVPInventoryUnit(
            listing=listings["guide"],
            provider_id=providers[0].id,
            label="Group walk",
            capacity=6,
            room_metadata=json.dumps(
                {
                    "guide_package": {
                        "price": "3400",
                        "pricing_type": "per_group",
                        "description": "A guided heritage walk.",
                        "duration_hours": "2",
                        "meeting_point": "Station",
                    }
                }
            ),
        )
        session.add_all([unit, guide_unit])
        session.flush()
        for target in (unit, guide_unit):
            session.add(
                MVPAvailabilitySlot(
                    unit_id=target.id,
                    listing_id=target.listing_id,
                    capacity=6,
                    starts_at=now() + timedelta(days=1),
                    ends_at=now() + timedelta(days=2),
                )
            )
        session.commit()
        headers = [
            {
                "Authorization": "Bearer "
                + create_mvp_access_token(user.id, user.email, user.role)
            }
            for user in users
        ]
        user_ids = [user.id for user in users]
    with TestClient(app, base_url="http://testserver/api/v1/") as client:
        yield client, engine, keys, headers, user_ids
    engine.dispose()


def get_group(client, route="me", headers=None, **params):
    response = client.get(f"recommendations/{route}", headers=headers, params=params)
    assert response.status_code == 200, response.text
    return response.json()["items"]


def enable(client, headers, value=True):
    response = client.patch(
        "recommendations/settings",
        headers=headers,
        json={
            "behavioral_personalization": value,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def record(client, headers, **data):
    return client.post("recommendations/activity", headers=headers, json=data)


def preferences(client, headers, **payload):
    response = client.put("traveler/preferences", headers=headers, json=payload)
    assert response.status_code == 200, response.text


def test_guest_cold_start_visibility_and_existing_api(catalog):
    client, _, keys, headers, _ = catalog
    cards = get_group(client, "popular")
    assert {card["listing_id"] for card in cards} == {
        keys[key]
        for key in ("hike", "trail", "lodging", "guide", "bike", "bicycle", "artisan")
    }
    assert len(cards) == 7
    assert get_group(client, headers=headers[0]) == cards
    by_category = {card["category"]: card for card in reversed(cards)}
    assert by_category["artisan"]["bookable"] is False
    assert by_category["artisan"]["starting_price"] is None
    assert by_category["bike"]["bookable"] is False
    assert by_category["guide"]["starting_price"] == "3400"
    assert by_category["guide"]["pricing_unit"] == "per_group"
    assert by_category["guide"]["bookable"] is True
    hike = next(card for card in cards if card["listing_id"] == keys["hike"])
    assert hike["rating"] is None
    assert hike["bookable"]
    assert hike["cover_image_url"].startswith(
        f"/api/v1/listings/{keys['hike']}/photos/"
    )
    assert client.get(f"http://testserver{hike['detail_route']}").status_code == 200
    assert (
        client.get("traveler/preferences", headers=headers[0]).json()["interests"] == []
    )
    assert client.get("recommendations/settings", headers=headers[0]).json() == {
        "behavioral_personalization": False,
    }
    sections = client.get("recommendations/home", params={"limit": 2}).json()[
        "sections"
    ]
    all_ids = [card["listing_id"] for section in sections for card in section["items"]]
    assert len(all_ids) == len(set(all_ids)) == 4


def test_interests_destinations_and_budget(catalog):
    client, _, keys, headers, _ = catalog
    traveler = headers[0]
    preferences(
        client,
        traveler,
        interests=["mountains_hiking", "photography"],
        preferred_destinations=["Jaffna"],
        budget_max=5500,
    )
    interests = get_group(
        client, headers=traveler, recommendation_type="based_on_your_interests"
    )
    assert {card["listing_id"] for card in interests} == {keys["hike"], keys["trail"]}
    assert all(
        "mountains hiking" in card["recommendation_reason"] for card in interests
    )
    places = get_group(
        client, headers=traveler, recommendation_type="favorite_destinations"
    )
    assert {card["district"] for card in places} == {"Jaffna"}
    ranked = get_group(client, headers=traveler, category="adventure")
    assert ranked[0]["listing_id"] == keys["hike"]
    assert ranked[0]["recommendation_score"] > ranked[1]["recommendation_score"]
    assert (
        get_group(client, "popular", district="Nallur", category="guide")[0][
            "listing_id"
        ]
        == keys["guide"]
    )
    assert get_group(client, "popular", district="nonexistent") == []


def test_activity_tracking_recency_and_similar(catalog):
    client, engine, keys, headers, _ = catalog
    traveler = headers[0]
    enable(client, traveler)
    assert record(
        client,
        traveler,
        event_type="search",
        search_query="  BatiK ",
        category="artisan",
        district="Galle",
    ).json()["recorded"]
    assert get_group(client, headers=traveler)[0]["listing_id"] == keys["artisan"]
    assert (
        record(
            client,
            traveler,
            event_type="search",
            search_query="batik",
            category="artisan",
            district="galle",
        ).json()["reason"]
        == "duplicate"
    )
    assert record(
        client,
        traveler,
        event_type="search",
        search_query="village",
        district="Jaffna",
        category="homestay",
    ).json()["recorded"]
    with Session(engine) as db:
        batik = db.scalar(
            select(TravelerActivityEvent).where(
                TravelerActivityEvent.search_query == "batik",
            )
        )
        batik.created_at = now() - timedelta(days=45)
        db.commit()
    assert get_group(client, headers=traveler)[0]["listing_id"] == keys["lodging"]
    assert record(client, traveler, event_type="view", listing_id=keys["hike"]).json()[
        "recorded"
    ]
    assert (
        record(
            client,
            traveler,
            event_type="view",
            listing_id=keys["hike"],
            category="adventure",
        ).json()["reason"]
        == "duplicate"
    )
    viewed = get_group(
        client, headers=traveler, recommendation_type="because_you_viewed"
    )
    assert viewed[0]["listing_id"] == keys["trail"]
    similar = get_group(client, f"similar/{keys['hike']}")
    assert similar[0]["listing_id"] == keys["trail"]
    assert keys["hike"] not in {card["listing_id"] for card in similar}
    assert client.get(f"recommendations/similar/{keys['draft']}").status_code == 404
    discovered = get_group(
        client, headers=traveler, recommendation_type="discover_something_new"
    )
    assert keys["hike"] not in {card["listing_id"] for card in discovered}


def test_wishlist_booking_and_popularity(catalog):
    client, engine, keys, headers, user_ids = catalog
    traveler, other = headers[:2]
    enable(client, traveler)
    enable(client, other)
    with Session(engine) as db:
        db.add(MVPHomestayWishlist(traveler_id=user_ids[0], listing_id=keys["bicycle"]))
        timestamp = now()
        for index, (key, status) in enumerate(
            (("guide", "confirmed"), ("hike", "cancelled"))
        ):
            db.add(
                MVPBooking(
                    traveler_id=user_ids[1],
                    listing_id=keys[key],
                    request_key=str(index),
                    request_hash=b"x" * 32,
                    starts_at=timestamp,
                    ends_at=timestamp + timedelta(days=1),
                    quantity=1,
                    total_amount=3400,
                    status=status,
                )
            )
        db.commit()
    ranked = get_group(client, headers=traveler)
    assert ranked[0]["listing_id"] == keys["bicycle"]
    assert "saved" in ranked[0]["recommendation_reason"]
    ranked = get_group(client, headers=other)
    assert ranked[0]["listing_id"] == keys["guide"]
    assert "booked" in ranked[0]["recommendation_reason"]
    assert keys["hike"] not in {card["listing_id"] for card in ranked}
    assert get_group(client, "popular")[0]["listing_id"] == keys["guide"]
    enable(client, traveler, False)
    assert get_group(client, headers=traveler) == get_group(client, "popular")


def test_activity_validation_rate_consent_isolation_and_retention(catalog):
    client, engine, keys, headers, user_ids = catalog
    traveler, other, admin, provider = headers[:4]
    payload = {"event_type": "view", "listing_id": keys["hike"]}
    assert record(client, None, **payload).status_code == 401
    assert record(client, admin, **payload).status_code == 403
    assert record(client, provider, **payload).status_code == 403
    assert client.get("recommendations/me").status_code == 401
    assert client.delete("recommendations/history").status_code == 401
    assert (
        record(client, traveler, **payload).json()["reason"]
        == "personalization_disabled"
    )
    enable(client, traveler)
    enable(client, other)
    for invalid in (
        dict(payload, traveler_id=user_ids[1]),
        dict(payload, listing_id=0),
        dict(payload, category="bike"),
        {"event_type": "search"},
        {"event_type": "search", "search_query": "example@example.org"},
        {"event_type": "search", "search_query": "192.168.0.1"},
        {"event_type": "search", "filters": {"max_price": 2000}},
        {"event_type": "search", "filters": {"token": "invalid"}},
    ):
        assert record(client, traveler, **invalid).status_code == 422
    assert (
        record(client, traveler, **dict(payload, listing_id=keys["draft"])).status_code
        == 404
    )
    assert record(client, other, **payload).json()["recorded"]
    for index in range(activity.RATE_LIMIT):
        assert record(
            client, traveler, event_type="search", search_query=f"tour {index}"
        ).json()["recorded"]
    limited = record(client, traveler, **payload)
    assert limited.status_code == 429
    assert limited.headers["Retry-After"] == "60"
    assert (
        get_group(client, headers=traveler, recommendation_type="because_you_viewed")
        == []
    )
    assert get_group(client, headers=other, recommendation_type="because_you_viewed")
    with Session(engine) as db:
        db.add(MVPHomestayWishlist(traveler_id=user_ids[0], listing_id=keys["bicycle"]))
        db.add(
            TravelerActivityEvent(
                traveler_id=user_ids[1],
                event_type="search",
                district="Jaffna",
                fingerprint=hashlib.sha256(b"old").digest(),
                created_at=now() - timedelta(days=91),
            )
        )
        db.commit()
        assert len(activity.history(db, user_ids[1], now())) == 1
        assert activity.purge_expired(db, now()) == 1
        db.commit()
    assert client.delete("recommendations/history", headers=traveler).status_code == 204
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(TravelerActivityEvent)) == 1
        assert db.get(MVPHomestayWishlist, (user_ids[0], keys["bicycle"])) is not None
    enable(client, other, False)
    assert (
        record(client, other, **payload).json()["reason"] == "personalization_disabled"
    )
    assert (
        get_group(client, headers=other, recommendation_type="because_you_viewed") == []
    )


def test_mssql_activity_schema_and_migration():
    ddl = str(
        CreateTable(TravelerActivityEvent.__table__).compile(dialect=mssql.dialect())
    )
    assert "BIGINT NOT NULL IDENTITY" in ddl
    assert "FOREIGN KEY(traveler_id) REFERENCES users" in ddl
    migration = next(item for item in UPGRADES if item.version == "1.0.9")
    assert migration.version == "1.0.9"
    assert "CREATE TABLE dbo.traveler_activity_events" in migration.batches()[0]
    assert "DEFAULT (0)" in migration.batches()[0]


def test_retention_only_deletes_events_strictly_older_than_90_days(catalog):
    _, engine, _, _, user_ids = catalog
    reference = now()
    boundary = reference - timedelta(days=90)
    with Session(engine) as db:
        for seconds in (-1, 0, 1):
            db.add(
                TravelerActivityEvent(
                    traveler_id=user_ids[0],
                    event_type="search",
                    district="Galle",
                    fingerprint=hashlib.sha256(str(seconds).encode()).digest(),
                    created_at=boundary + timedelta(seconds=seconds),
                )
            )
        db.commit()
        assert activity.purge_expired(db, reference) == 1
        db.commit()
        assert db.scalars(
            select(TravelerActivityEvent.created_at).order_by(
                TravelerActivityEvent.created_at,
            )
        ).all() == [boundary, boundary + timedelta(seconds=1)]
