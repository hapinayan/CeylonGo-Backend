
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.database.connection import get_db
from app.models.homestay import Homestay
from app.models.provider_profile import ProviderProfile
from app.models.user import Base, User
from app.routers.auth import get_current_user


def make_homestay_profile(user_id: int, business_name: str = "Test Homestay") -> ProviderProfile:
    return ProviderProfile(
        user_id=user_id,
        business_name=business_name,
        provider_type="homestay",
        phone="+94771234567",
        district="Jaffna",
        approval_status="approved",
    )


@pytest.mark.parametrize("role", ["owner", "admin"])
def test_manager_can_create_homestay(role):

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def register_sqlite_functions(
        dbapi_connection,
        connection_record,
    ):
        dbapi_connection.create_function(
            "sysutcdatetime",
            0,
            lambda: datetime.now(
                timezone.utc
            ).replace(tzinfo=None).isoformat(sep=" "),
        )

    Base.metadata.create_all(bind=engine)

    TestingSession = sessionmaker(
        bind=engine,
        expire_on_commit=False,
    )

    manager = User(
        id=201,
        full_name="Test Manager",
        email="manager@example.com",
        role=role,
        is_active=True,
    )

    with TestingSession() as db:
        db.add(manager)
        if role != "admin":
            db.add(make_homestay_profile(manager.id, "Manager Homestay"))
        db.commit()

    def override_db():
        with TestingSession() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = (
        lambda: manager
    )

    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/homestays",
                json={
                    "name": "Jaffna Family Homestay",
                    "description": "Family accommodation",
                    "location": "Jaffna",
                    "price_per_night": "5500.00",
                    "max_guests": 4,
                    "images": [
                        {
                            "image_url": "https://example.com/room.jpg",
                            "is_primary": True,
                        }
                    ],
                    "amenities": ["WiFi", "Parking"],
                },
            )

        assert response.status_code == 201, response.text

        data = response.json()

        assert data["name"] == "Jaffna Family Homestay"
        assert len(data["images"]) == 1
        assert len(data["amenities"]) == 2

        with TestingSession() as db:
            saved = db.query(Homestay).first()

            assert saved is not None
            assert saved.owner_id == manager.id

    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(
            get_current_user,
            None,
        )
        engine.dispose()


def test_traveler_cannot_create_homestay():

    traveler = User(
        id=101,
        full_name="Test Traveler",
        email="traveler-test@example.com",
        role="traveler",
        is_active=True,
    )

    app.dependency_overrides[get_current_user] = (
        lambda: traveler
    )

    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/homestays",
                json={
                    "name": "Test Homestay",
                    "description": "Security test",
                    "location": "Jaffna",
                    "price_per_night": "5000.00",
                    "max_guests": 2,
                    "images": [],
                    "amenities": [],
                },
            )

        assert response.status_code == 403
        assert response.json()["detail"] == (
            "Owner or admin access required"
        )

    finally:
        app.dependency_overrides.pop(
            get_current_user,
            None,
        )

from unittest.mock import MagicMock

from app.models.homestay import Homestay
from app.routers.homestay import update_homestay
from app.schemas.homestay import HomestayUpdateRequest
from fastapi import HTTPException


def make_update_request():
    return HomestayUpdateRequest(
        name="Updated Jaffna Homestay",
        description="Updated description",
        location="Jaffna",
        price_per_night="6500.00",
        max_guests=5,
        is_active=True,
        images=[],
        amenities=[],
    )


@pytest.mark.parametrize(
    "role,owner_id,expected_status",
    [
        ("owner", 201, 200),
        ("owner", 999, 403),
        ("admin", 999, 200),
    ],
)
def test_update_homestay_permissions(
    role,
    owner_id,
    expected_status,
):
    current_user = User(
        id=201,
        full_name="Test Manager",
        email="manager@example.com",
        role=role,
        is_active=True,
    )

    homestay = Homestay(
        id=1,
        owner_id=owner_id,
        name="Original Homestay",
        description="Original description",
        location="Jaffna",
        price_per_night=5000,
        max_guests=2,
        is_active=True,
    )
    homestay.images = []
    homestay.amenities = []

    db = MagicMock()
    db.query.return_value.options.return_value.filter.return_value.first.return_value = homestay

    if expected_status == 403:
        with pytest.raises(HTTPException) as exc:
            update_homestay(
                homestay_id=1,
                request=make_update_request(),
                db=db,
                current_user=current_user,
            )

        assert exc.value.status_code == 403
        db.commit.assert_not_called()

    else:
        result = update_homestay(
            homestay_id=1,
            request=make_update_request(),
            db=db,
            current_user=current_user,
        )

        assert result.name == "Updated Jaffna Homestay"
        assert result.price_per_night == 6500
        db.commit.assert_called_once()

def test_owner_can_update_homestay_via_api():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def register_sqlite_functions(
        dbapi_connection,
        connection_record,
    ):
        dbapi_connection.create_function(
            "sysutcdatetime",
            0,
            lambda: datetime.now(
                timezone.utc
            ).replace(tzinfo=None).isoformat(sep=" "),
        )

    Base.metadata.create_all(bind=engine)

    TestingSession = sessionmaker(
        bind=engine,
        expire_on_commit=False,
    )

    owner = User(
        id=301,
        full_name="Update Test Owner",
        email="update-owner@example.com",
        role="owner",
        is_active=True,
    )

    with TestingSession() as db:
        db.add(owner)
        db.add(make_homestay_profile(owner.id, "Update Homestay"))
        db.add(
            Homestay(
                id=1,
                owner_id=owner.id,
                name="Original Homestay",
                description="Original description",
                location="Jaffna",
                price_per_night=5000,
                max_guests=2,
                is_active=True,
            )
        )
        db.commit()

    def override_db():
        with TestingSession() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = (
        lambda: owner
    )

    try:
        with TestClient(app) as client:
            response = client.put(
                "/api/v1/homestays/1",
                json={
                    "name": "Updated Homestay",
                    "description": "Updated description",
                    "location": "Kandy",
                    "price_per_night": "7500.00",
                    "max_guests": 4,
                    "is_active": True,
                    "images": [],
                    "amenities": ["WiFi"],
                },
            )

        assert response.status_code == 200, response.text

        data = response.json()
        assert data["name"] == "Updated Homestay"
        assert data["location"] == "Kandy"
        assert len(data["amenities"]) == 1

        with TestingSession() as db:
            saved = db.get(Homestay, 1)
            assert saved.name == "Updated Homestay"
            assert saved.owner_id == owner.id

    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(
            get_current_user,
            None,
        )
        engine.dispose()

def test_other_owner_cannot_update_via_api():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def register_sqlite_functions(
        dbapi_connection,
        connection_record,
    ):
        dbapi_connection.create_function(
            "sysutcdatetime",
            0,
            lambda: datetime.now(
                timezone.utc
            ).replace(tzinfo=None).isoformat(sep=" "),
        )

    Base.metadata.create_all(bind=engine)

    TestingSession = sessionmaker(
        bind=engine,
        expire_on_commit=False,
    )

    owner = User(
        id=401,
        full_name="Original Owner",
        email="original-owner@example.com",
        role="owner",
        is_active=True,
    )

    other_owner = User(
        id=402,
        full_name="Other Owner",
        email="other-owner@example.com",
        role="owner",
        is_active=True,
    )

    with TestingSession() as db:
        db.add_all([owner, other_owner])
        db.add(make_homestay_profile(owner.id, "Owner Homestay"))
        db.add(make_homestay_profile(other_owner.id, "Other Homestay"))
        db.add(
            Homestay(
                id=1,
                owner_id=owner.id,
                name="Original Homestay",
                description="Original description",
                location="Jaffna",
                price_per_night=5000,
                max_guests=2,
                is_active=True,
            )
        )
        db.commit()

    def override_db():
        with TestingSession() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = (
        lambda: other_owner
    )

    try:
        with TestClient(app) as client:
            response = client.put(
                "/api/v1/homestays/1",
                json={
                    "name": "Unauthorized Update",
                    "description": "Should not save",
                    "location": "Colombo",
                    "price_per_night": "1000.00",
                    "max_guests": 10,
                    "is_active": True,
                    "images": [],
                    "amenities": [],
                },
            )

        assert response.status_code == 403
        assert response.json()["detail"] == (
            "You cannot manage this homestay"
        )

        with TestingSession() as db:
            saved = db.get(Homestay, 1)

            assert saved.name == "Original Homestay"
            assert saved.owner_id == owner.id

    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(
            get_current_user,
            None,
        )
        engine.dispose()

def test_admin_can_update_other_owner_homestay_via_api():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def register_sqlite_functions(
        dbapi_connection,
        connection_record,
    ):
        dbapi_connection.create_function(
            "sysutcdatetime",
            0,
            lambda: datetime.now(
                timezone.utc
            ).replace(tzinfo=None).isoformat(sep=" "),
        )

    Base.metadata.create_all(bind=engine)

    TestingSession = sessionmaker(
        bind=engine,
        expire_on_commit=False,
    )

    owner = User(
        id=501,
        full_name="Homestay Owner",
        email="owner501@example.com",
        role="owner",
        is_active=True,
    )

    admin = User(
        id=502,
        full_name="Test Admin",
        email="admin502@example.com",
        role="admin",
        is_active=True,
    )

    with TestingSession() as db:
        db.add_all([owner, admin])
        db.add(
            Homestay(
                id=1,
                owner_id=owner.id,
                name="Original Homestay",
                description="Original description",
                location="Jaffna",
                price_per_night=5000,
                max_guests=2,
                is_active=True,
            )
        )
        db.commit()

    def override_db():
        with TestingSession() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = (
        lambda: admin
    )

    try:
        with TestClient(app) as client:
            response = client.put(
                "/api/v1/homestays/1",
                json={
                    "name": "Admin Updated Homestay",
                    "description": "Updated by admin",
                    "location": "Kandy",
                    "price_per_night": "8500.00",
                    "max_guests": 5,
                    "is_active": True,
                    "images": [],
                    "amenities": ["WiFi"],
                },
            )

        assert response.status_code == 200, response.text

        data = response.json()

        assert data["name"] == "Admin Updated Homestay"
        assert data["location"] == "Kandy"

        with TestingSession() as db:
            saved = db.get(Homestay, 1)

            assert saved.name == "Admin Updated Homestay"
            assert saved.owner_id == owner.id

    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(
            get_current_user,
            None,
        )
        engine.dispose()

def test_owner_can_deactivate_homestay_via_api():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def register_sqlite_functions(
        dbapi_connection,
        connection_record,
    ):
        dbapi_connection.create_function(
            "sysutcdatetime",
            0,
            lambda: datetime.now(
                timezone.utc
            ).replace(tzinfo=None).isoformat(sep=" "),
        )

    Base.metadata.create_all(bind=engine)

    TestingSession = sessionmaker(
        bind=engine,
        expire_on_commit=False,
    )

    owner = User(
        id=601,
        full_name="Deactivation Test Owner",
        email="owner601@example.com",
        role="owner",
        is_active=True,
    )

    with TestingSession() as db:
        db.add(owner)
        db.add(make_homestay_profile(owner.id, "Deactivation Homestay"))
        db.add(
            Homestay(
                id=1,
                owner_id=owner.id,
                name="Jaffna Test Homestay",
                description="Deactivation test",
                location="Jaffna",
                price_per_night=5000,
                max_guests=3,
                is_active=True,
            )
        )
        db.commit()

    def override_db():
        with TestingSession() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = (
        lambda: owner
    )

    try:
        with TestClient(app) as client:
            update_response = client.put(
                "/api/v1/homestays/1",
                json={
                    "name": "Jaffna Test Homestay",
                    "description": "Deactivation test",
                    "location": "Jaffna",
                    "price_per_night": "5000.00",
                    "max_guests": 3,
                    "is_active": False,
                    "images": [],
                    "amenities": [],
                },
            )

            assert update_response.status_code == 200
            assert update_response.json()["is_active"] is False

            listing_response = client.get(
                "/api/v1/homestays"
            )

            assert listing_response.status_code == 200
            assert listing_response.json()["total"] == 0

            detail_response = client.get(
                "/api/v1/homestays/1"
            )

            assert detail_response.status_code == 404

        with TestingSession() as db:
            saved = db.get(Homestay, 1)

            assert saved is not None
            assert saved.is_active is False
            assert saved.owner_id == owner.id

    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(
            get_current_user,
            None,
        )
        engine.dispose()

def test_owner_can_list_only_own_homestays():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def register_sqlite_functions(
        dbapi_connection,
        connection_record,
    ):
        dbapi_connection.create_function(
            "sysutcdatetime",
            0,
            lambda: datetime.now(
                timezone.utc
            ).replace(tzinfo=None).isoformat(sep=" "),
        )

    Base.metadata.create_all(bind=engine)

    TestingSession = sessionmaker(
        bind=engine,
        expire_on_commit=False,
    )

    owner = User(
        id=701,
        full_name="Listing Test Owner",
        email="owner701@example.com",
        role="owner",
        is_active=True,
    )

    other_owner = User(
        id=702,
        full_name="Other Listing Owner",
        email="owner702@example.com",
        role="owner",
        is_active=True,
    )

    with TestingSession() as db:
        db.add_all([owner, other_owner])
        db.add(make_homestay_profile(owner.id, "Owner 701 Stay"))
        db.add(make_homestay_profile(other_owner.id, "Owner 702 Stay"))

        db.add_all([
            Homestay(
                owner_id=701,
                name="My Active Homestay",
                location="Jaffna",
                price_per_night=5000,
                max_guests=2,
                is_active=True,
            ),
            Homestay(
                owner_id=701,
                name="My Inactive Homestay",
                location="Kandy",
                price_per_night=6000,
                max_guests=3,
                is_active=False,
            ),
            Homestay(
                owner_id=702,
                name="Other Owner Homestay",
                location="Colombo",
                price_per_night=7000,
                max_guests=4,
                is_active=True,
            ),
        ])

        db.commit()

    def override_db():
        with TestingSession() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = (
        lambda: owner
    )

    try:
        with TestClient(app) as client:
            response = client.get(
                "/api/v1/homestays/mine"
            )

        assert response.status_code == 200, response.text

        data = response.json()

        assert data["total"] == 2

        names = {
            item["name"]
            for item in data["items"]
        }

        assert names == {
            "My Active Homestay",
            "My Inactive Homestay",
        }

        active_statuses = {
            item["is_active"]
            for item in data["items"]
        }

        assert active_statuses == {True, False}

    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(
            get_current_user,
            None,
        )
        engine.dispose()

def test_traveler_cannot_list_owner_homestays():
    traveler = User(
        id=801,
        full_name="Test Traveler",
        email="traveler801@example.com",
        role="traveler",
        is_active=True,
    )

    app.dependency_overrides[get_current_user] = (
        lambda: traveler
    )

    try:
        with TestClient(app) as client:
            response = client.get(
                "/api/v1/homestays/mine"
            )

        assert response.status_code == 403
        assert response.json()["detail"] == (
            "Owner or admin access required"
        )

    finally:
        app.dependency_overrides.pop(
            get_current_user,
            None,
        )


def test_unauthenticated_user_cannot_list_owner_homestays():
    app.dependency_overrides.pop(
        get_current_user,
        None,
    )

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/homestays/mine"
        )

    assert response.status_code == 401


def test_owner_create_list_update_flow():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def register_sqlite_functions(dbapi_connection, connection_record):
        dbapi_connection.create_function(
            "sysutcdatetime",
            0,
            lambda: datetime.now(timezone.utc)
            .replace(tzinfo=None)
            .isoformat(sep=" "),
        )

    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(bind=engine, expire_on_commit=False)

    owner = User(
        id=301,
        full_name="Integration Test Owner",
        email="integration.owner@example.com",
        role="owner",
        is_active=True,
    )

    admin = User(
        id=302,
        full_name="Integration Test Reviewer",
        email="integration.reviewer@example.com",
        role="admin",
        is_active=True,
    )
    with TestingSession() as db:
        db.add_all([owner, admin])
        db.add(make_homestay_profile(owner.id, "Integration Stay"))
        db.commit()

    def override_db():
        with TestingSession() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: owner

    try:
        with TestClient(app) as client:
            create_response = client.post(
                "/api/v1/homestays",
                json={
                    "name": "Integration Jaffna Homestay",
                    "description": "Owner integration test",
                    "location": "Jaffna",
                    "price_per_night": "5000.00",
                    "max_guests": 4,
                    "images": [],
                    "amenities": [],
                },
            )

            assert create_response.status_code == 201, create_response.text
            homestay_id = create_response.json()["id"]

            list_response = client.get("/api/v1/homestays/mine")
            assert list_response.status_code == 200, list_response.text

            assert any(
                item["id"] == homestay_id
                for item in list_response.json()["items"]
            )

            update_response = client.put(
                f"/api/v1/homestays/{homestay_id}",
                json={
                    "name": "Updated Integration Homestay",
                    "description": "Updated by owner",
                    "location": "Jaffna",
                    "price_per_night": "6500.00",
                    "max_guests": 5,
                    "is_active": True,
                    "images": [],
                    "amenities": [],
                },
            )

            assert update_response.status_code == 200, update_response.text
            assert update_response.json()["name"] == "Updated Integration Homestay"

            private_response = client.get("/api/v1/homestays/mine")
            assert private_response.status_code == 200, private_response.text
            assert any(
                item["id"] == homestay_id
                and item["name"] == "Updated Integration Homestay"
                for item in private_response.json()["items"]
            )
            assert client.get(f"/api/v1/homestays/{homestay_id}").status_code == 404

            submission = client.post(f"/api/v1/homestays/{homestay_id}/submit-approval")
            assert submission.status_code == 200, submission.text
            assert submission.json()["approval_status"] == "pending"
            assert client.get(f"/api/v1/homestays/{homestay_id}").status_code == 404

            app.dependency_overrides[get_current_user] = lambda: admin
            review = client.post(
                f"/api/v1/admin/homestays/{homestay_id}/review",
                json={"decision": "approved"},
            )
            assert review.status_code == 200, review.text
            assert review.json()["approval_status"] == "approved"
            app.dependency_overrides.pop(get_current_user)
            detail_response = client.get(
                f"/api/v1/homestays/{homestay_id}"
            )

            assert detail_response.status_code == 200
            assert detail_response.json()["name"] == "Updated Integration Homestay"

    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)
        engine.dispose()
