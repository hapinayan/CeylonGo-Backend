import asyncio
import json
import logging
from datetime import datetime

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select, update
from sqlalchemy.dialects import mssql
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.location_validation import write_review_snapshot
from app.core.map_config import MapSettings
from app.database.connection import get_db
from app.main import app
from app.models.activity import Activity
from app.models.artisan_listing import ArtisanListing
from app.models.eco_experience import EcoExperience
from app.models.homestay import Homestay
from app.models.provider_profile import ProviderProfile
from app.models.rental import RentalLocation
from app.models.rental_vehicle import RentalVehicle
from app.models.shared import AdminAuditLog
from app.models.tour_guide import TourGuide
from app.models.user import Base, User
from app.routers.auth import get_current_user
from app.routers.interactive_map import NearbyAccessLogFilter, get_routing_service
from app.schemas.interactive_map import RouteRequest
from app.services.map_catalog import GeographyDistance
from app.services.map_routing import MapRoutingService, RouteError

BASE = "/api/v1/map"
ADMIN = "/api/v1/admin/map/locations"
BOUNDS = {"west": 79, "south": 6, "east": 82, "north": 10}
SECRET = "fixture-only-geoapify-key"
REQUEST = {
    "origin": {"latitude": 6.93, "longitude": 79.85},
    "destination": {"latitude": 6.92, "longitude": 79.84},
}


@pytest.fixture
def catalog():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def add_functions(connection, _record):
        connection.create_function(
            "sysutcdatetime", 0, lambda: datetime.now().isoformat()
        )

    Base.metadata.create_all(engine)
    with Session(engine) as db:
        roles = ("provider", "provider", "provider", "admin", "traveler", "provider")
        db.add_all(
            [
                User(
                    id=index,
                    email=f"{index}@example.test",
                    full_name="Private Name",
                    role=role,
                    is_active=index != 6,
                )
                for index, role in enumerate(roles, 1)
            ]
        )
        profiles = (
            (1, "activity", "approved"),
            (2, "activity", "approved"),
            (3, "activity", "pending"),
            (6, "activity", "approved"),
            (7, "artisan", "approved"),
        )
        db.add_all(
            [
                User(
                    id=index,
                    email=f"{index}@example.test",
                    full_name="Private Name",
                    role="provider",
                )
                for index in (7, 8, 9, 10, 11, 12)
            ]
        )
        profiles += (
            (8, "tour_guide", "approved"),
            (9, "rental", "approved"),
            (10, "rental", "approved"),
            (11, "homestay", "approved"),
            (12, "eco_tourism", "approved"),
        )
        db.add_all(
            [
                ProviderProfile(
                    id=index,
                    user_id=index,
                    provider_type=kind,
                    approval_status=status,
                    business_name="Public travel business",
                    phone="PRIVATE-PHONE",
                    district="Colombo",
                )
                for index, kind, status in profiles
            ]
        )
        for index, title, lat, lon, approval, active, provider_id in (
            (1, "Colombo Walk", 6.93, 79.85, "approved", True, 1),
            (2, "Pending Walk", 6.93, 79.85, "pending", True, 1),
            (3, "Inactive Walk", 6.93, 79.85, "approved", False, 1),
            (4, "Missing Walk", 6.93, None, "approved", True, 1),
            (5, "Provider Pending Walk", 6.93, 79.85, "approved", True, 3),
            (6, "Provider Inactive Walk", 6.93, 79.85, "approved", True, 6),
            (7, "Editable draft", None, None, "draft", False, 1),
            (8, "Around four kilometers", 6.93, 79.89, "approved", True, 1),
            (9, "Around eight kilometers", 6.93, 79.92, "approved", True, 1),
        ):
            db.add(
                Activity(
                    id=index,
                    title=title,
                    latitude=lat,
                    longitude=lon,
                    provider_profile_id=provider_id,
                    approval_status=approval,
                    is_active=active,
                    description="A long description",
                    category="cultural",
                    district="Colombo District",
                    city="Fort",
                    meeting_point="Public park",
                    duration_minutes=60,
                    pricing_type="per_person",
                    price=100,
                    currency="LKR",
                )
            )
        db.add(
            Homestay(
                id=1,
                provider_profile_id=11,
                owner_id=11,
                name="Kandy Rooms",
                location="Kandy",
                city="Kandy",
                district="Kandy",
                address="Public road",
                latitude=7.29,
                longitude=80.63,
                approval_status="approved",
                is_active=True,
                price_per_night=5000,
                max_guests=2,
            )
        )
        db.add(
            EcoExperience(
                id=1,
                provider_profile_id=12,
                title="Jaffna Nature",
                eco_category="Wildlife & Birdwatching",
                meeting_point="Visitor entrance",
                duration_hours=2,
                district="Jaffna",
                city="Nallur",
                latitude=9.67,
                longitude=80.03,
                pricing_type="per_person",
                base_price=1800,
                max_group_size=8,
                environmental_guidelines="Example",
                wildlife_safety_summary="Example",
                listing_status="approved",
                is_published=True,
            )
        )
        db.add(
            TourGuide(
                id=1,
                provider_profile_id=8,
                bio="Private bio",
                base_city="Kandy",
                base_district="Kandy",
                base_latitude=7.31,
                base_longitude=80.62,
                approval_status="approved",
                is_active=True,
                daily_rate=5000,
            )
        )
        db.add(
            ArtisanListing(
                id=1,
                provider_profile_id=7,
                title="Artisan Workshop",
                craft_category="wood",
                city="Fort",
                district="Colombo",
                address="Public studio",
                latitude=6.95,
                longitude=79.86,
                phone="PRIVATE-PHONE",
                approval_status="approved",
                is_verified=True,
                is_active=True,
            )
        )
        db.add(
            RentalLocation(
                id=1,
                provider_id=9,
                location_name="Bike depot",
                district="Colombo",
                city="Fort",
                latitude=6.96,
                longitude=79.86,
                is_active=True,
            )
        )
        db.add_all(
            [
                RentalVehicle(
                    id=index,
                    provider_id=provider_id,
                    vehicle_type=kind,
                    vehicle_name=f"Rental {index}",
                    status="available",
                    approval_status="approved",
                    price_per_day=200,
                    location_id=1,
                )
                for index, provider_id, kind in (
                    (1, 9, "bike"),
                    (2, 10, "bicycle"),
                    (3, 9, "tuk_tuk"),
                )
            ]
        )
        db.commit()

    def database():
        with Session(engine) as db:
            yield db

    actor = {"id": 5}

    def authenticated_user():
        with Session(engine) as db:
            return db.get(User, actor["id"])

    app.dependency_overrides.update(
        {get_db: database, get_current_user: authenticated_user}
    )
    client = TestClient(app)
    try:
        yield client, actor, engine
    finally:
        client.close()
        app.dependency_overrides.clear()
        engine.dispose()


def test_aggregation_bounds_categories_and_pagination(catalog):
    client, _, _ = catalog
    response = client.get(f"{BASE}/locations", params=BOUNDS)
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 8
    assert {item["module_type"] for item in items} == {
        "homestay",
        "activity",
        "eco_experience",
        "tour_guide",
        "artisan",
        "rental",
    }
    assert "PRIVATE" not in response.text
    assert {
        item["listing_id"] for item in items if item["module_type"] == "activity"
    } == {
        1,
        8,
        9,
    }
    assert [
        item["listing_id"] for item in items if item["module_type"] == "rental"
    ] == [1]
    subset = client.get(
        f"{BASE}/locations",
        params={
            **BOUNDS,
            "east": 80,
            "north": 7,
            "category": "cultural",
            "page_size": 1,
            "page": 2,
        },
    ).json()
    assert subset["items"][0]["listing_id"] == 8 and subset["has_more"]
    assert (
        client.get(f"{BASE}/locations", params={**BOUNDS, "south": 11}).status_code
        == 422
    )
    assert (
        client.get(f"{BASE}/locations", params={**BOUNDS, "page_size": 101}).status_code
        == 422
    )
    categories = client.get(f"{BASE}/categories").json()
    assert {entry["id"] for entry in categories if not entry["available"]} == {
        "local_business",
        "tourist_attraction",
    }


@pytest.mark.parametrize(
    "query,category,expected",
    [
        ("Kandy Rooms", None, ["Kandy Rooms"]),
        ("Nallur", None, ["Jaffna Nature"]),
        (
            "Colombo District",
            "cultural",
            ["Colombo Walk", "Around four kilometers", "Around eight kilometers"],
        ),
        ("Fort%' OR 1=1 --", None, []),
        ("Pending Walk", None, []),
    ],
)
def test_literal_search(catalog, query, category, expected):
    client, _, _ = catalog
    params = {"q": query}
    if category:
        params["category"] = category
    response = client.get(f"{BASE}/search", params=params)
    assert response.status_code == 200
    assert [item["name"] for item in response.json()["items"]] == expected


def test_nearby_accuracy_orders_before_pagination(catalog):
    client, _, _ = catalog
    params = {"lat": 6.93, "lon": 79.85, "radius_km": 5, "category": "cultural"}
    data = client.get(f"{BASE}/nearby", params=params).json()
    assert data["distance_type"] == "straight_line"
    assert [item["listing_id"] for item in data["items"]] == [1, 8]
    assert data["items"][1]["distance_km"] == pytest.approx(4.415, abs=0.02)
    data = client.get(
        f"{BASE}/nearby",
        params={
            **params,
            "radius_km": 10,
            "page_size": 1,
            "page": 3,
        },
    ).json()
    assert data["items"][0]["listing_id"] == 9 and not data["has_more"]
    for invalid in ({"radius_km": 6}, {"lat": 91}, {"lon": "nan"}):
        assert (
            client.get(f"{BASE}/nearby", params={**params, **invalid}).status_code
            == 422
        )
    stmt = select(GeographyDistance(Activity.latitude, Activity.longitude, 6.93, 79.85))
    assert ".STDistance(geography::Point(" in str(stmt.compile(dialect=mssql.dialect()))


def test_admin_correction_requires_role_and_writes_audit(catalog):
    client, actor, engine = catalog
    path = f"{ADMIN}/activity/2"
    payload = {
        "latitude": 6.94,
        "longitude": 79.86,
        "reason": "Verified meeting entrance",
    }
    assert client.get(ADMIN).status_code == 403
    assert client.patch(path, json=payload).status_code == 403
    actor["id"] = 4
    assert len(client.get(ADMIN, params={"module": "activity"}).json()["items"]) == 9
    assert client.patch(path, json={**payload, "latitude": 100}).status_code == 422
    correction = client.patch(path, json=payload)
    assert correction.status_code == 200
    assert correction.json()["approval_status"] == "pending"
    assert client.patch(path, json=payload).status_code == 200
    with Session(engine) as db:
        audit = db.scalars(select(AdminAuditLog)).one()
        assert (
            audit.admin_user_id,
            audit.action,
            audit.entity_name,
            audit.entity_id,
        ) == (
            4,
            "map_location_corrected",
            "activities",
            2,
        )
        details = json.loads(audit.details)
        assert details["reason"] == payload["reason"]
        assert float(details["before"][0]) == pytest.approx(6.93)
        assert float(details["after"][0]) == pytest.approx(6.94)


def test_provider_permissions_pairing_and_reverification(catalog):
    client, actor, _ = catalog
    path = "/api/v1/provider/activities/7"
    pair = {"latitude": 6.94, "longitude": 79.86}
    actor["id"] = 2
    assert client.patch(path, json=pair).status_code in (403, 404)
    actor["id"] = 3
    assert client.patch(path, json=pair).status_code == 403
    actor["id"] = 1
    assert client.patch(path, json={"latitude": 6.94}).status_code == 422
    assert client.patch(path, json=pair).status_code == 200
    assert client.patch("/api/v1/provider/activities/1", json=pair).status_code == 409
    actor["id"] = 7
    changed = client.put("/api/v1/artisan-listings/1", json=pair)
    assert changed.status_code == 200 and changed.json()["is_verified"] is False
    assert (
        client.get(
            f"{BASE}/locations", params={**BOUNDS, "category": "artisan"}
        ).json()["items"]
        == []
    )


def test_homestay_and_eco_location_changes_require_reapproval(catalog):
    client, actor, _ = catalog
    actor["id"] = 11
    home = client.put(
        "/api/v1/homestays/1",
        json={
            "name": "Kandy Rooms",
            "location": "Kandy",
            "city": "Kandy",
            "district": "Kandy",
            "address": "Public road",
            "latitude": 7.30,
            "longitude": 80.63,
            "price_per_night": 5000,
            "max_guests": 2,
            "is_active": True,
        },
    )
    assert home.status_code == 200 and home.json()["approval_status"] == "draft"
    assert (
        client.get(
            f"{BASE}/locations",
            params={
                **BOUNDS,
                "category": "homestay",
            },
        ).json()["items"]
        == []
    )
    actor["id"] = 12
    eco = client.put("/api/v1/eco-experiences/1", json={"latitude": 9.68})
    assert eco.status_code == 200 and eco.json()["listing_status"] == "draft"
    assert not eco.json()["is_published"]
    assert (
        client.get(
            f"{BASE}/locations",
            params={
                **BOUNDS,
                "category": "eco_tourism",
            },
        ).json()["items"]
        == []
    )


@pytest.mark.parametrize(
    "model,module,path,actor_id,method,payload,state,active,category,old_lat",
    [
        (
            Activity,
            "provider_activities",
            "/api/v1/provider/activities/1",
            1,
            "PATCH",
            {"latitude": 6.94},
            "approval_status",
            "is_active",
            "cultural",
            6.93,
        ),
        (
            Homestay,
            "homestay",
            "/api/v1/homestays/1",
            11,
            "PUT",
            {
                "name": "Kandy Rooms",
                "location": "Kandy",
                "city": "Kandy",
                "district": "Kandy",
                "address": "Public road",
                "latitude": 7.30,
                "longitude": 80.63,
                "price_per_night": 5000,
                "max_guests": 2,
                "is_active": True,
            },
            "approval_status",
            "is_active",
            "homestay",
            7.29,
        ),
        (
            EcoExperience,
            "eco_experiences",
            "/api/v1/eco-experiences/1",
            12,
            "PUT",
            {"latitude": 9.68},
            "listing_status",
            "is_published",
            "eco_tourism",
            9.67,
        ),
    ],
)
def test_provider_location_edit_loses_to_approval(
    catalog,
    monkeypatch,
    model,
    module,
    path,
    actor_id,
    method,
    payload,
    state,
    active,
    category,
    old_lat,
):
    client, actor, engine = catalog
    actor["id"] = actor_id
    with Session(engine) as db:
        db.execute(
            update(model).where(model.id == 1).values({state: "draft", active: False})
        )
        db.commit()

    def approval_then_write(db, listing, fields, changes):
        assert getattr(listing, state) == "draft"
        with Session(engine) as concurrent:
            concurrent.execute(
                update(model)
                .where(model.id == 1)
                .values({state: "approved", active: True})
            )
            concurrent.commit()
        return write_review_snapshot(db, listing, fields, changes)

    monkeypatch.setattr(
        f"app.routers.{module}.write_review_snapshot", approval_then_write
    )
    conflict = client.request(method, path, json=payload)
    assert conflict.status_code == 409
    public = client.get(
        f"{BASE}/locations", params={**BOUNDS, "category": category}
    ).json()
    assert next(item for item in public["items"] if item["listing_id"] == 1)[
        "latitude"
    ] == pytest.approx(old_lat)


@pytest.mark.parametrize(
    "model,module,path,payload,state,pending,active,category,new_lat",
    [
        (
            Activity,
            "admin_activities",
            "/api/v1/admin/activities/1/approve",
            {},
            "approval_status",
            "pending",
            "is_active",
            "cultural",
            6.94,
        ),
        (
            Homestay,
            "admin_homestays",
            "/api/v1/admin/homestays/1/review",
            {"decision": "approved"},
            "approval_status",
            "draft",
            "is_active",
            "homestay",
            7.30,
        ),
        (
            EcoExperience,
            "eco_experiences",
            "/api/v1/admin/eco-experiences/1/approve",
            None,
            "listing_status",
            "pending_approval",
            "is_published",
            "eco_tourism",
            9.68,
        ),
    ],
)
def test_stale_admin_decision_cannot_approve_changed_location(
    catalog,
    monkeypatch,
    model,
    module,
    path,
    payload,
    state,
    pending,
    active,
    category,
    new_lat,
):
    client, actor, engine = catalog
    actor["id"] = 4
    with Session(engine) as db:
        db.execute(
            update(model).where(model.id == 1).values({state: pending, active: False})
        )
        db.commit()

    def new_location_then_decide(db, listing, fields, changes):
        with Session(engine) as concurrent:
            # In pending-only workflows, an edit and resubmission after rejection
            # can leave a different location in the same approval state.
            concurrent.execute(
                update(model).where(model.id == 1).values(latitude=new_lat)
            )
            concurrent.commit()
        return write_review_snapshot(db, listing, fields, changes)

    target = f"app.routers.{module}.write_review_snapshot"
    with monkeypatch.context() as patch:
        patch.setattr(target, new_location_then_decide)
        assert client.post(path, json=payload).status_code == 409

    params = {**BOUNDS, "category": category}
    items = client.get(f"{BASE}/locations", params=params).json()["items"]
    assert all(item["listing_id"] != 1 for item in items)
    approved = client.post(path, json=payload)
    assert approved.status_code == 200
    assert float(approved.json()["latitude"]) == pytest.approx(new_lat)
    if model is EcoExperience:
        actor["id"] = 12
        publish = client.patch(
            "/api/v1/eco-experiences/1/publish", json={"is_published": True}
        )
        assert publish.status_code == 200
    items = client.get(f"{BASE}/locations", params=params).json()["items"]
    assert next(item for item in items if item["listing_id"] == 1)["latitude"] == (
        pytest.approx(new_lat)
    )


def test_stale_eco_publish_cannot_restore_approval_after_location_edit(
    catalog, monkeypatch
):
    client, actor, engine = catalog
    actor["id"] = 12

    def edit_then_publish(db, listing, fields, changes):
        with Session(engine) as concurrent:
            concurrent.execute(
                update(EcoExperience)
                .where(EcoExperience.id == 1)
                .values(
                    latitude=9.68,
                    listing_status="draft",
                    is_published=False,
                )
            )
            concurrent.commit()
        return write_review_snapshot(db, listing, fields, changes)

    monkeypatch.setattr(
        "app.routers.eco_experiences.write_review_snapshot", edit_then_publish
    )
    response = client.patch(
        "/api/v1/eco-experiences/1/publish",
        json={"is_published": True},
    )
    assert response.status_code == 409
    assert (
        client.get(
            f"{BASE}/locations",
            params={
                **BOUNDS,
                "category": "eco_tourism",
            },
        ).json()["items"]
        == []
    )


def test_artisan_review_rejects_location_changed_before_verification(
    catalog, monkeypatch
):
    client, actor, engine = catalog
    actor["id"] = 4

    def change_then_verify(db, listing, fields, changes):
        with Session(engine) as concurrent:
            concurrent.execute(
                update(ArtisanListing)
                .where(ArtisanListing.id == 1)
                .values(
                    latitude=6.96,
                    is_verified=False,
                )
            )
            concurrent.commit()
        return write_review_snapshot(db, listing, fields, changes)

    with monkeypatch.context() as patch:
        patch.setattr(
            "app.routers.artisan_listings.write_review_snapshot", change_then_verify
        )
        review = client.patch(
            "/api/v1/artisan-listings/1/status", json={"is_verified": True}
        )
        assert review.status_code == 409
    params = {**BOUNDS, "category": "artisan"}
    assert client.get(f"{BASE}/locations", params=params).json()["items"] == []
    review = client.patch(
        "/api/v1/artisan-listings/1/status", json={"is_verified": True}
    )
    assert review.status_code == 200
    assert client.get(f"{BASE}/locations", params=params).json()["items"][0][
        "latitude"
    ] == pytest.approx(6.96)


def test_guide_location_requires_resubmission_and_admin_reapproval(catalog):
    client, actor, _ = catalog
    guide = "/api/v1/provider/tour-guides"
    decision = f"{ADMIN}/tour_guide/1/decision"
    actor["id"] = 8
    changed = client.patch(guide, json={"base_latitude": 7.32})
    assert changed.status_code == 200 and changed.json()["approval_status"] == "draft"
    assert client.post(f"{guide}/submit").json()["approval_status"] == "pending"
    assert client.patch(guide, json={"base_latitude": 7.33}).status_code == 409
    approve = {"decision": "approved", "reason": "Verified public guide location"}
    assert client.post(decision, json=approve).status_code == 403
    actor["id"] = 4
    response = client.post(decision, json=approve)
    assert response.status_code == 200 and response.json()["active"] is True
    assert client.post(decision, json=approve).status_code == 409
    markers = client.get(
        f"{BASE}/locations", params={**BOUNDS, "category": "tour_guide"}
    )
    assert markers.json()["items"][0]["latitude"] == pytest.approx(7.32)


def test_rental_pickup_ownership_review_and_republication(catalog):
    client, actor, _ = catalog
    provider_path = "/api/v1/provider/rental-locations"
    payload = {
        "location_name": "Public rental shop",
        "district": "Colombo",
        "city": "Fort",
        "address": "Shop entrance",
        "latitude": 6.92,
        "longitude": 79.84,
    }
    actor["id"] = 9
    invalid = {key: val for key, val in payload.items() if key != "latitude"}
    assert client.post(provider_path, json=invalid).status_code == 422
    created = client.post(provider_path, json=payload)
    assert created.status_code == 201 and not created.json()["is_active"]
    location_id = created.json()["id"]
    vehicle = "/api/v1/rental-vehicles/1"
    assert client.patch(vehicle, json={"location_id": location_id}).status_code == 404
    actor["id"] = 10
    assert client.put(f"{provider_path}/{location_id}", json=payload).status_code == 404
    assert (
        client.patch("/api/v1/rental-vehicles/2", json={"location_id": 1}).status_code
        == 404
    )
    actor["id"] = 4
    decision = f"{ADMIN}/rental/{location_id}/decision"
    approve = {"decision": "approved", "reason": "Verified public rental location"}
    assert client.post(decision, json=approve).json()["active"] is True
    actor["id"] = 9
    assert client.patch(vehicle, json={"location_id": location_id}).status_code == 200
    markers = {**BOUNDS, "category": "bike_rental"}
    assert client.get(f"{BASE}/locations", params=markers).json()["items"][0][
        "latitude"
    ] == pytest.approx(6.92)
    changed = client.put(
        f"{provider_path}/{location_id}", json={**payload, "latitude": 6.94}
    )
    assert changed.status_code == 200 and not changed.json()["is_active"]
    assert client.get(f"{BASE}/locations", params=markers).json()["items"] == []


def test_route_endpoint_validation_and_error_contract(catalog):
    client, _, _ = catalog
    app.dependency_overrides[get_routing_service] = lambda: MapRoutingService(
        MapSettings(SECRET),
        lambda: httpx.MockTransport(
            lambda request: httpx.Response(429, headers={"Retry-After": "7"}),
        ),
    )
    response = client.post(f"{BASE}/route", json=REQUEST)
    assert response.status_code == 503 and response.headers["Retry-After"] == "7"
    assert response.json() == {"detail": {"code": "map_upstream_rate_limited"}}
    assert (
        client.post(
            f"{BASE}/route",
            json={
                **REQUEST,
                "destination": {"latitude": 179, "longitude": 0},
            },
        ).status_code
        == 422
    )


def test_route_local_quota_protects_upstream():
    calls = []

    def mock(request):
        calls.append(request.url.path)
        return httpx.Response(200, json=route_response())

    async def scenario():
        service = MapRoutingService(
            MapSettings(SECRET),
            lambda: httpx.MockTransport(mock),
        )
        service.quotas[1].max_requests = 1
        await service.route(RouteRequest(**REQUEST))
        with pytest.raises(RouteError) as caught:
            await service.route(RouteRequest(**REQUEST, mode="walking"))
        assert caught.value.status == 429 and caught.value.retry_after > 0
        assert len(calls) == 1

    asyncio.run(scenario())


def test_access_logs_omit_traveler_position_only_for_nearby():
    filter_location = NearbyAccessLogFilter()
    for path, expected in (
        ("/api/v1/map/nearby?lat=6.93&lon=79.85", "/api/v1/map/nearby"),
        ("/api/v1/map/search?q=Fort", "/api/v1/map/search?q=Fort"),
    ):
        record = logging.LogRecord(
            "uvicorn.access",
            logging.INFO,
            __file__,
            1,
            '%s - "%s %s HTTP/%s" %d',
            ("client", "GET", path, "1.1", 200),
            None,
        )
        assert filter_location.filter(record)
        assert record.getMessage() == f'client - "GET {expected} HTTP/1.1" 200'


def route_response():
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {
                    "type": "MultiLineString",
                    "coordinates": [
                        [[79.85, 6.93], [79.84, 6.92]],
                    ],
                },
                "properties": {
                    "distance": 1700,
                    "time": 120,
                    "legs": [
                        {
                            "steps": [
                                {
                                    "instruction": {"text": "Turn left"},
                                    "distance": 20,
                                    "time": 10,
                                },
                                {"distance": 6},
                            ]
                        }
                    ],
                },
            }
        ],
    }


@pytest.mark.parametrize(
    "mode,upstream",
    [
        ("driving", "drive"),
        ("cycling", "bicycle"),
        ("walking", "walk"),
    ],
)
def test_route_modes_order_and_coalescing(mode, upstream, caplog):
    calls = []
    now = 0

    async def mock(request):
        calls.append(request.url.path)
        assert request.url.params["mode"] == upstream
        assert request.url.params["apiKey"] == SECRET
        assert (
            request.url.params["waypoints"] == "6.930000,79.850000|6.920000,79.840000"
        )
        await asyncio.sleep(0.01)
        return httpx.Response(200, json=route_response())

    async def scenario():
        nonlocal now
        service = MapRoutingService(
            MapSettings(SECRET, cache_ttl_seconds=10),
            lambda: httpx.MockTransport(mock),
            clock=lambda: now,
        )
        payload = RouteRequest(**REQUEST, mode=mode)
        results = await asyncio.gather(*(service.route(payload) for _ in range(8)))
        result = await service.route(payload)
        assert calls == ["/v1/routing"] and result == results[0]
        assert result.mode == mode and result.geometry.coordinates[0][0] == (
            79.85,
            6.93,
        )
        assert (result.distance_m, result.distance_km) == (1700, 1.7)
        assert (result.duration_seconds, result.duration_minutes) == (120, 2)
        assert [instruction.text for instruction in result.instructions] == [
            "Turn left"
        ]
        now = 10
        await service.route(payload)
        assert len(calls) == 2

    with caplog.at_level(logging.DEBUG):
        asyncio.run(scenario())
    assert SECRET not in caplog.text and "79.850000" not in caplog.text


@pytest.mark.parametrize(
    "kind,status,code,count",
    [
        ("missing", 503, "map_routing_not_configured", 0),
        ("auth", 502, "map_upstream_authentication", 1),
        ("limit", 503, "map_upstream_rate_limited", 1),
        ("timeout", 504, "map_upstream_timeout", 2),
        ("outage", 503, "map_upstream_unavailable", 2),
        ("no_path", 404, "map_no_route", 1),
        ("empty", 404, "map_no_route", 1),
        ("invalid", 502, "map_invalid_route_response", 1),
    ],
)
def test_route_error_handling(kind, status, code, count):
    calls = []

    def mock(request):
        calls.append(request)
        if kind == "timeout":
            raise httpx.ReadTimeout(SECRET)
        if kind == "limit":
            return httpx.Response(429, headers={"Retry-After": "7"})
        if kind == "no_path":
            return httpx.Response(
                400, json={"message": "No path could be found for input"}
            )
        if kind == "empty":
            return httpx.Response(
                200, json={"type": "FeatureCollection", "features": []}
            )
        return httpx.Response(
            {"auth": 401, "outage": 503, "invalid": 200}[kind], text=SECRET
        )

    async def scenario():
        service = MapRoutingService(
            MapSettings("" if kind == "missing" else SECRET),
            lambda: httpx.MockTransport(mock),
        )
        with pytest.raises(RouteError) as caught:
            await service.route(RouteRequest(**REQUEST))
        assert (caught.value.status, caught.value.code) == (status, code)
        if kind == "limit":
            assert caught.value.retry_after == 7
            with pytest.raises(RouteError, match="map_upstream_rate_limited"):
                await service.route(RouteRequest(**REQUEST, mode="cycling"))

    asyncio.run(scenario())
    assert len(calls) == count
