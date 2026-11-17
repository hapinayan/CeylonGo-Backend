import pytest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.database.connection import get_db
from app.models.user import Base, User
from app.models.provider_profile import ProviderProfile
from app.models.homestay import Homestay, HomestayImage, HomestayAmenity
from app.models.booking import Booking
from app.core.security import create_access_token
from app.core.database_guard import verify_safe_integration_database, DatabaseSafetyError
from app.schemas.homestay import HomestayUpdateRequest


@pytest.fixture
def regression_db_client():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def configure_sqlite_connection(dbapi_connection, connection_record):
        dbapi_connection.create_function(
            "sysutcdatetime",
            0,
            lambda: datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        )
    Base.metadata.create_all(
        bind=engine,
        tables=[
            User.__table__,
            ProviderProfile.__table__,
            Homestay.__table__,
            HomestayImage.__table__,
            HomestayAmenity.__table__,
            Booking.__table__,
        ],
    )
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    # Seed approved homestay host 1 (User 10, Profile 1)
    with Session() as db:
        host1 = User(
            id=10,
            full_name="Host One",
            email="host1@example.com",
            role="provider",
            is_active=True,
        )
        profile1 = ProviderProfile(
            id=1,
            user_id=10,
            business_name="Host One Stays",
            provider_type="homestay",
            phone="0771234561",
            district="Kandy",
            approval_status="approved",
            identity_document_type="nic",
        )
        # Seed host 2 (User 20, Profile 2)
        host2 = User(
            id=20,
            full_name="Host Two",
            email="host2@example.com",
            role="provider",
            is_active=True,
        )
        profile2 = ProviderProfile(
            id=2,
            user_id=20,
            business_name="Host Two Stays",
            provider_type="homestay",
            phone="0771234562",
            district="Galle",
            approval_status="approved",
            identity_document_type="nic",
        )
        # Seed Homestay 1 owned by Host 1 with initial amenities & images
        hs1 = Homestay(
            id=1,
            owner_id=10,
            provider_profile_id=1,
            name="Hillside Haven",
            description="Scenic mountain views",
            location="Kandy",
            price_per_night=Decimal("6000.00"),
            max_guests=4,
            is_active=True,
            created_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
        hs1.amenities = [
            HomestayAmenity(name="WiFi"),
            HomestayAmenity(name="Hot Water"),
            HomestayAmenity(name="Mountain View"),
        ]
        hs1.images = [
            HomestayImage(image_url="https://example.com/img1.jpg", is_primary=True),
            HomestayImage(image_url="https://example.com/img2.jpg", is_primary=False),
        ]
        db.add_all([host1, profile1, host2, profile2, hs1])
        db.commit()

    session = Session()

    def override_db():
        try:
            yield session
        finally:
            pass

    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    
    token1 = create_access_token({"sub": "10", "role": "provider"})
    token2 = create_access_token({"sub": "20", "role": "provider"})
    
    yield {
        "client": client,
        "token1": token1,
        "token2": token2,
        "session": session,
        "Session": Session,
    }

    session.close()
    app.dependency_overrides.pop(get_db, None)


# 1. Updating a homestay while retaining existing amenities
def test_update_homestay_retaining_amenities(regression_db_client):
    c = regression_db_client["client"]
    t1 = regression_db_client["token1"]
    
    resp = c.put(
        "/api/v1/homestays/1",
        headers={"Authorization": f"Bearer {t1}"},
        json={
            "name": "Hillside Haven Updated",
            "location": "Kandy",
            "price_per_night": 6500.00,
            "max_guests": 4,
            "amenities": ["WiFi", "Hot Water", "Air Conditioning"],
            "images": [{"image_url": "https://example.com/img1.jpg", "is_primary": True}],
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    amenity_names = {a["name"] for a in data["amenities"]}
    assert amenity_names == {"WiFi", "Hot Water", "Air Conditioning"}


# 2. Updating details without supplying amenities (preserves existing)
def test_update_homestay_omitting_amenities(regression_db_client):
    c = regression_db_client["client"]
    t1 = regression_db_client["token1"]
    
    resp = c.put(
        "/api/v1/homestays/1",
        headers={"Authorization": f"Bearer {t1}"},
        json={
            "name": "Hillside Haven Renamed",
            "location": "Kandy",
            "price_per_night": 7000.00,
            "max_guests": 5,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Hillside Haven Renamed"
    amenity_names = {a["name"] for a in data["amenities"]}
    assert amenity_names == {"WiFi", "Hot Water", "Mountain View"}


# 3. Adding new amenities
def test_update_homestay_adding_new_amenities(regression_db_client):
    c = regression_db_client["client"]
    t1 = regression_db_client["token1"]
    
    resp = c.put(
        "/api/v1/homestays/1",
        headers={"Authorization": f"Bearer {t1}"},
        json={
            "name": "Hillside Haven",
            "location": "Kandy",
            "price_per_night": 6000.00,
            "max_guests": 4,
            "amenities": ["WiFi", "Hot Water", "Mountain View", "Breakfast Included"],
        },
    )
    assert resp.status_code == 200
    amenity_names = {a["name"] for a in resp.json()["amenities"]}
    assert "Breakfast Included" in amenity_names
    assert len(amenity_names) == 4


# 4. Removing existing amenities
def test_update_homestay_removing_amenities(regression_db_client):
    c = regression_db_client["client"]
    t1 = regression_db_client["token1"]
    
    resp = c.put(
        "/api/v1/homestays/1",
        headers={"Authorization": f"Bearer {t1}"},
        json={
            "name": "Hillside Haven",
            "location": "Kandy",
            "price_per_night": 6000.00,
            "max_guests": 4,
            "amenities": ["WiFi"],
        },
    )
    assert resp.status_code == 200
    amenity_names = [a["name"] for a in resp.json()["amenities"]]
    assert amenity_names == ["WiFi"]


# 5. Replacing amenities completely
def test_update_homestay_replacing_amenities(regression_db_client):
    c = regression_db_client["client"]
    t1 = regression_db_client["token1"]
    
    resp = c.put(
        "/api/v1/homestays/1",
        headers={"Authorization": f"Bearer {t1}"},
        json={
            "name": "Hillside Haven",
            "location": "Kandy",
            "price_per_night": 6000.00,
            "max_guests": 4,
            "amenities": ["Swimming Pool", "Gym"],
        },
    )
    assert resp.status_code == 200
    amenity_names = {a["name"] for a in resp.json()["amenities"]}
    assert amenity_names == {"Swimming Pool", "Gym"}


# 6. Explicitly clearing amenities
def test_update_homestay_clearing_amenities(regression_db_client):
    c = regression_db_client["client"]
    t1 = regression_db_client["token1"]
    
    resp = c.put(
        "/api/v1/homestays/1",
        headers={"Authorization": f"Bearer {t1}"},
        json={
            "name": "Hillside Haven",
            "location": "Kandy",
            "price_per_night": 6000.00,
            "max_guests": 4,
            "amenities": [],
        },
    )
    assert resp.status_code == 200
    assert resp.json()["amenities"] == []


# 7. Duplicate amenity input rejection (case-insensitive)
def test_duplicate_amenity_rejection():
    with pytest.raises(ValidationError) as exc:
        HomestayUpdateRequest(
            name="Test",
            location="Kandy",
            price_per_night=5000,
            max_guests=2,
            amenities=["WiFi", "wifi"],
        )
    assert "Duplicate amenities are not allowed" in str(exc.value)


# 8. Repeated identical updates (idempotent, no unique key collision)
def test_repeated_identical_updates(regression_db_client):
    c = regression_db_client["client"]
    t1 = regression_db_client["token1"]
    payload = {
        "name": "Hillside Haven",
        "location": "Kandy",
        "price_per_night": 6000.00,
        "max_guests": 4,
        "amenities": ["WiFi", "Hot Water", "Mountain View"],
    }
    for _ in range(3):
        resp = c.put("/api/v1/homestays/1", headers={"Authorization": f"Bearer {t1}"}, json=payload)
        assert resp.status_code == 200


# 9. Preserving existing images when omitted and updating when provided
def test_preserve_and_update_images(regression_db_client):
    c = regression_db_client["client"]
    t1 = regression_db_client["token1"]
    
    # Omit images -> images preserved
    resp = c.put(
        "/api/v1/homestays/1",
        headers={"Authorization": f"Bearer {t1}"},
        json={
            "name": "Hillside Haven",
            "location": "Kandy",
            "price_per_night": 6000.00,
            "max_guests": 4,
        },
    )
    assert resp.status_code == 200
    assert len(resp.json()["images"]) == 2
    
    # Provide new images list -> images updated
    resp2 = c.put(
        "/api/v1/homestays/1",
        headers={"Authorization": f"Bearer {t1}"},
        json={
            "name": "Hillside Haven",
            "location": "Kandy",
            "price_per_night": 6000.00,
            "max_guests": 4,
            "images": [{"image_url": "https://example.com/brand_new.jpg", "is_primary": True}],
        },
    )
    assert resp2.status_code == 200
    assert len(resp2.json()["images"]) == 1
    assert resp2.json()["images"][0]["image_url"] == "https://example.com/brand_new.jpg"


# 10. Transaction rollback on update failure
def test_transaction_rollback_on_failure(regression_db_client):
    c = regression_db_client["client"]
    t1 = regression_db_client["token1"]
    Session = regression_db_client["Session"]
    
    # Active booking prevents deactivation
    with Session() as db:
        booking = Booking(
            homestay_id=1,
            user_id=10,
            check_in=date.today() + timedelta(days=5),
            check_out=date.today() + timedelta(days=10),
            status="confirmed",
            guests=2,
            total_price=Decimal("12000.00"),
        )
        db.add(booking)
        db.commit()

    resp = c.put(
        "/api/v1/homestays/1",
        headers={"Authorization": f"Bearer {t1}"},
        json={
            "name": "Should Not Persist",
            "location": "Kandy",
            "price_per_night": 9999.00,
            "max_guests": 4,
            "is_active": False,
        },
    )
    assert resp.status_code == 400
    
    # Verify name was rolled back
    with Session() as db:
        hs = db.query(Homestay).filter(Homestay.id == 1).first()
        assert hs.name != "Should Not Persist"


# 11. Cross-provider update rejection (403 Forbidden)
def test_cross_provider_update_rejection(regression_db_client):
    c = regression_db_client["client"]
    t2 = regression_db_client["token2"]  # Host 2 trying to edit Homestay 1
    
    resp = c.put(
        "/api/v1/homestays/1",
        headers={"Authorization": f"Bearer {t2}"},
        json={
            "name": "Hacked Title",
            "location": "Kandy",
            "price_per_night": 1000.00,
            "max_guests": 2,
        },
    )
    assert resp.status_code == 403


# 12. Unique OpenAPI operation IDs
def test_unique_openapi_operation_ids():
    schema = app.openapi()
    operation_ids = []
    for path, methods in schema["paths"].items():
        for method, operation in methods.items():
            if isinstance(operation, dict) and "operationId" in operation:
                operation_ids.append(operation["operationId"])
    
    duplicates = [op for op in operation_ids if operation_ids.count(op) > 1]
    assert len(duplicates) == 0, f"Duplicate operation IDs found: {set(duplicates)}"


# 13. Duplicate route registration prevention
def test_no_duplicate_route_registration():
    schema = app.openapi()
    # Check that booking and promotion endpoints exist in schema
    assert "/api/v1/bookings/me" in schema["paths"]
    assert "/api/v1/homestay-promotions" in schema["paths"]

    # Verify routers are mounted once without duplicate prefixes
    booking_routers = [
        r for r in app.routes
        if hasattr(r, "original_router") and getattr(r.original_router, "prefix", "") == "/api/v1/bookings"
    ]
    assert len(booking_routers) == 1, f"Expected 1 booking router, found {len(booking_routers)}"

    promotion_routers = [
        r for r in app.routes
        if hasattr(r, "original_router") and getattr(r.original_router, "prefix", "") == "/api/v1/homestay-promotions"
    ]
    assert len(promotion_routers) == 1, f"Expected 1 promotion router, found {len(promotion_routers)}"


# 14. Integration database identity guard
def test_integration_database_identity_guard():
    # 1. Live database rejection
    with pytest.raises(DatabaseSafetyError, match="LIVE DATABASE SAFETY VIOLATION"):
        verify_safe_integration_database("CeylonGoDB")

    with pytest.raises(DatabaseSafetyError, match="LIVE DATABASE SAFETY VIOLATION"):
        verify_safe_integration_database("  ceylongodb  ")

    # 2. Empty or unspecified database rejection
    for empty_val in [None, "", "   "]:
        with pytest.raises(DatabaseSafetyError, match="DATABASE SAFETY VIOLATION: Target database name is unspecified or empty"):
            verify_safe_integration_database(empty_val)

    # 3. Unapproved database rejection
    for unapproved in ["master", "tempdb", "RandomDB", "CeylonGoDB_Rehearsal"]:
        with pytest.raises(DatabaseSafetyError, match="UNAPPROVED INTEGRATION DATABASE"):
            verify_safe_integration_database(unapproved)

    # 4. Approved integration database acceptance
    assert verify_safe_integration_database("CeylonGoDB_IntegrationTest") is True
    assert verify_safe_integration_database("ceylongodb_integrationtest") is True
