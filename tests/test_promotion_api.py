
from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.models.homestay import Homestay
from app.models.user import User
from app.routers.promotion import create_promotion
from app.schemas.promotion import PromotionCreateRequest
from app.models.promotion import HomestayPromotion

def make_user(user_id, role):
    return User(
        id=user_id,
        full_name="Test User",
        email=f"user{user_id}@example.com",
        role=role,
        is_active=True,
    )


def make_promotion_request():
    start = date.today() + timedelta(days=7)

    return PromotionCreateRequest(
        homestay_id=1,
        title="Weekend Special",
        description="Special discount",
        discount_percent="20.00",
        start_date=start,
        end_date=start + timedelta(days=7),
    )


@pytest.mark.parametrize(
    "role,user_id,owner_id,expected_status",
    [
        ("owner", 101, 101, 201),
        ("owner", 102, 101, 403),
        ("admin", 103, 101, 201),
    ],
)
def test_create_promotion_ownership(
    role,
    user_id,
    owner_id,
    expected_status,
):
    db = MagicMock()

    homestay = Homestay(
        id=1,
        owner_id=owner_id,
        name="Test Homestay",
        location="Jaffna",
        price_per_night=5000,
        max_guests=2,
        is_active=True,
    )

    db.query.return_value.filter.return_value.first.return_value = (
        homestay
    )

    current_user = make_user(user_id, role)
    request = make_promotion_request()

    if expected_status == 403:
        with pytest.raises(HTTPException) as error:
            create_promotion(
                request=request,
                db=db,
                current_user=current_user,
            )

        assert error.value.status_code == 403
        db.add.assert_not_called()
        db.commit.assert_not_called()

    else:
        result = create_promotion(
            request=request,
            db=db,
            current_user=current_user,
        )

        assert result.homestay_id == 1
        assert result.title == "Weekend Special"

        db.add.assert_called_once()
        db.commit.assert_called_once()


def test_create_promotion_homestay_not_found():
    db = MagicMock()

    db.query.return_value.filter.return_value.first.return_value = (
        None
    )

    current_user = make_user(201, "owner")
    request = make_promotion_request()

    with pytest.raises(HTTPException) as error:
        create_promotion(
            request=request,
            db=db,
            current_user=current_user,
        )

    assert error.value.status_code == 404
    db.add.assert_not_called()
    db.commit.assert_not_called()

def test_owner_can_create_promotion_via_api():
    from datetime import datetime, timezone

    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.database.connection import get_db
    from app.main import app
    from app.models.user import Base
    from app.routers.auth import get_current_user

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

    owner = make_user(301, "owner")

    with TestingSession() as db:
        db.add(owner)
        db.add(
            Homestay(
                id=1,
                owner_id=owner.id,
                name="Jaffna Heritage Homestay",
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
        start = date.today() + timedelta(days=7)
        end = start + timedelta(days=7)

        with TestClient(app) as client:
            response = client.post(
                "/api/v1/homestay-promotions",
                json={
                    "homestay_id": 1,
                    "title": "Weekend Special",
                    "description": "Special discount",
                    "discount_percent": "20.00",
                    "start_date": start.isoformat(),
                    "end_date": end.isoformat(),
                },
            )

        assert response.status_code == 201, response.text

        data = response.json()

        assert data["homestay_id"] == 1
        assert data["title"] == "Weekend Special"
        assert data["discount_percent"] == "20.00"
        assert data["is_active"] is True

        with TestingSession() as db:
            saved = db.query(
                HomestayPromotion
            ).first()

            assert saved is not None
            assert saved.homestay_id == 1
            assert saved.title == "Weekend Special"

    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(
            get_current_user,
            None,
        )
        engine.dispose()

from app.routers.promotion import update_promotion
from app.schemas.promotion import PromotionUpdateRequest
from app.models.promotion import HomestayPromotion


def make_promotion_update_request():
    start = date.today() + timedelta(days=10)

    return PromotionUpdateRequest(
        title="Updated Weekend Offer",
        description="Updated discount",
        discount_percent="25.00",
        start_date=start,
        end_date=start + timedelta(days=5),
        is_active=False,
    )


@pytest.mark.parametrize(
    "role,user_id,owner_id,expected_status",
    [
        ("owner", 101, 101, 200),
        ("owner", 102, 101, 403),
        ("admin", 103, 101, 200),
    ],
)
def test_update_promotion_permissions(
    role,
    user_id,
    owner_id,
    expected_status,
):
    db = MagicMock()

    promotion = HomestayPromotion(
        id=1,
        homestay_id=5,
        title="Original Offer",
        discount_percent="10.00",
        start_date=date.today(),
        end_date=date.today() + timedelta(days=7),
        is_active=True,
    )

    homestay = Homestay(
        id=5,
        owner_id=owner_id,
        name="Test Homestay",
        location="Jaffna",
        price_per_night=5000,
        max_guests=2,
        is_active=True,
    )

    db.query.return_value.filter.return_value.first.side_effect = [
        promotion,
        homestay,
    ]

    current_user = make_user(user_id, role)
    request = make_promotion_update_request()

    if expected_status == 403:
        with pytest.raises(HTTPException) as error:
            update_promotion(
                promotion_id=1,
                request=request,
                db=db,
                current_user=current_user,
            )

        assert error.value.status_code == 403
        db.commit.assert_not_called()

    else:
        result = update_promotion(
            promotion_id=1,
            request=request,
            db=db,
            current_user=current_user,
        )

        assert result.title == "Updated Weekend Offer"
        assert result.is_active is False
        db.commit.assert_called_once()


def test_update_promotion_not_found():
    db = MagicMock()

    db.query.return_value.filter.return_value.first.return_value = (
        None
    )

    with pytest.raises(HTTPException) as error:
        update_promotion(
            promotion_id=999,
            request=make_promotion_update_request(),
            db=db,
            current_user=make_user(101, "owner"),
        )

    assert error.value.status_code == 404
    db.commit.assert_not_called()

def test_owner_can_update_promotion_via_api():
    from datetime import datetime, timezone

    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.database.connection import get_db
    from app.main import app
    from app.models.user import Base
    from app.routers.auth import get_current_user

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

    owner = make_user(401, "owner")
    start = date.today() + timedelta(days=7)

    with TestingSession() as db:
        db.add(owner)

        db.add(
            Homestay(
                id=1,
                owner_id=owner.id,
                name="Jaffna Heritage Homestay",
                location="Jaffna",
                price_per_night=5000,
                max_guests=3,
                is_active=True,
            )
        )

        db.add(
            HomestayPromotion(
                id=1,
                homestay_id=1,
                title="Original Offer",
                discount_percent="10.00",
                start_date=start,
                end_date=start + timedelta(days=7),
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
                "/api/v1/homestay-promotions/1",
                json={
                    "title": "Updated Special Offer",
                    "description": "Updated by owner",
                    "discount_percent": "25.00",
                    "start_date": start.isoformat(),
                    "end_date": (
                        start + timedelta(days=10)
                    ).isoformat(),
                    "is_active": False,
                },
            )

        assert response.status_code == 200, response.text

        data = response.json()

        assert data["title"] == "Updated Special Offer"
        assert data["discount_percent"] == "25.00"
        assert data["is_active"] is False

        with TestingSession() as db:
            saved = db.get(HomestayPromotion, 1)

            assert saved is not None
            assert saved.title == "Updated Special Offer"
            assert saved.is_active is False
            assert saved.homestay_id == 1

    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(
            get_current_user,
            None,
        )
        engine.dispose()

def test_public_promotions_show_only_current_active_offers():
    from datetime import datetime, timezone

    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.database.connection import get_db
    from app.main import app
    from app.models.user import Base
    from app.routers.auth import get_current_user

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

    today = date.today()

    with TestingSession() as db:
        db.add_all([
            Homestay(
                id=1,
                name="Active Homestay",
                location="Jaffna",
                price_per_night=5000,
                max_guests=2,
                is_active=True,
            ),
            Homestay(
                id=2,
                name="Inactive Homestay",
                location="Kandy",
                price_per_night=6000,
                max_guests=3,
                is_active=False,
            ),
        ])

        db.add_all([
            HomestayPromotion(
                homestay_id=1,
                title="Current Active Offer",
                discount_percent="20.00",
                start_date=today - timedelta(days=1),
                end_date=today + timedelta(days=7),
                is_active=True,
            ),
            HomestayPromotion(
                homestay_id=1,
                title="Expired Offer",
                discount_percent="10.00",
                start_date=today - timedelta(days=10),
                end_date=today - timedelta(days=1),
                is_active=True,
            ),
            HomestayPromotion(
                homestay_id=1,
                title="Inactive Offer",
                discount_percent="15.00",
                start_date=today - timedelta(days=1),
                end_date=today + timedelta(days=7),
                is_active=False,
            ),
            HomestayPromotion(
                homestay_id=2,
                title="Inactive Homestay Offer",
                discount_percent="25.00",
                start_date=today - timedelta(days=1),
                end_date=today + timedelta(days=7),
                is_active=True,
            ),
            HomestayPromotion(
                homestay_id=1,
                title="Future Offer",
                discount_percent="30.00",
                start_date=today + timedelta(days=5),
                end_date=today + timedelta(days=10),
                is_active=True,
            ),
        ])

        db.commit()

    def override_db():
        with TestingSession() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides.pop(
        get_current_user,
        None,
    )

    try:
        with TestClient(app) as client:
            response = client.get(
                "/api/v1/homestay-promotions"
            )

        assert response.status_code == 200, response.text

        data = response.json()

        assert data["total"] == 1
        assert len(data["items"]) == 1
        assert data["items"][0]["title"] == (
            "Current Active Offer"
        )
        assert data["items"][0]["discount_percent"] == (
            "20.00"
        )

    finally:
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()


def test_owner_can_list_only_own_promotions():
    from datetime import datetime, timezone

    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.database.connection import get_db
    from app.main import app
    from app.models.user import Base
    from app.routers.auth import get_current_user

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

    owner = make_user(501, "owner")
    other_owner = make_user(502, "owner")
    today = date.today()

    with TestingSession() as db:
        db.add_all([owner, other_owner])

        db.add_all([
            Homestay(
                id=1,
                owner_id=501,
                name="My Homestay",
                location="Jaffna",
                price_per_night=5000,
                max_guests=3,
                is_active=True,
            ),
            Homestay(
                id=2,
                owner_id=502,
                name="Other Homestay",
                location="Kandy",
                price_per_night=6000,
                max_guests=2,
                is_active=True,
            ),
        ])

        db.add_all([
            HomestayPromotion(
                homestay_id=1,
                title="My Active Offer",
                discount_percent="20.00",
                start_date=today - timedelta(days=1),
                end_date=today + timedelta(days=7),
                is_active=True,
            ),
            HomestayPromotion(
                homestay_id=1,
                title="My Inactive Offer",
                discount_percent="15.00",
                start_date=today - timedelta(days=1),
                end_date=today + timedelta(days=7),
                is_active=False,
            ),
            HomestayPromotion(
                homestay_id=1,
                title="My Expired Offer",
                discount_percent="10.00",
                start_date=today - timedelta(days=10),
                end_date=today - timedelta(days=1),
                is_active=True,
            ),
            HomestayPromotion(
                homestay_id=2,
                title="Other Owner Offer",
                discount_percent="25.00",
                start_date=today,
                end_date=today + timedelta(days=7),
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
                "/api/v1/homestay-promotions/mine"
            )

        assert response.status_code == 200, response.text

        data = response.json()

        assert data["total"] == 3
        assert len(data["items"]) == 3

        names = {
            item["title"]
            for item in data["items"]
        }

        assert names == {
            "My Active Offer",
            "My Inactive Offer",
            "My Expired Offer",
        }

        assert data["page"] == 1
        assert data["page_size"] == 10

    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(
            get_current_user,
            None,
        )
        engine.dispose()

def test_traveler_cannot_access_owner_promotions():
    from fastapi.testclient import TestClient

    from app.main import app
    from app.routers.auth import get_current_user

    traveler = make_user(601, "traveler")

    app.dependency_overrides[get_current_user] = (
        lambda: traveler
    )

    try:
        with TestClient(app) as client:
            response = client.get(
                "/api/v1/homestay-promotions/mine"
            )

        assert response.status_code == 403

    finally:
        app.dependency_overrides.pop(
            get_current_user,
            None,
        )


def test_unauthenticated_user_cannot_access_owner_promotions():
    from fastapi.testclient import TestClient

    from app.main import app
    from app.routers.auth import get_current_user

    app.dependency_overrides.pop(
        get_current_user,
        None,
    )

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/homestay-promotions/mine"
        )

    assert response.status_code == 401

def test_public_promotions_pagination():
    from datetime import datetime, timezone

    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.database.connection import get_db
    from app.main import app
    from app.models.user import Base

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

    today = date.today()

    with TestingSession() as db:
        db.add(
            Homestay(
                id=1,
                name="Pagination Test Homestay",
                location="Jaffna",
                price_per_night=5000,
                max_guests=3,
                is_active=True,
            )
        )

        db.add_all([
            HomestayPromotion(
                homestay_id=1,
                title=f"Offer {i}",
                discount_percent="10.00",
                start_date=today - timedelta(days=1),
                end_date=today + timedelta(days=7),
                is_active=True,
            )
            for i in range(1, 6)
        ])

        db.commit()

    def override_db():
        with TestingSession() as db:
            yield db

    app.dependency_overrides[get_db] = override_db

    try:
        with TestClient(app) as client:
            response = client.get(
                "/api/v1/homestay-promotions",
                params={
                    "page": 2,
                    "page_size": 2,
                },
            )

        assert response.status_code == 200, response.text

        data = response.json()

        assert data["total"] == 5
        assert data["page"] == 2
        assert data["page_size"] == 2
        assert len(data["items"]) == 2

        assert [
            item["title"]
            for item in data["items"]
        ] == ["Offer 3", "Offer 4"]

    finally:
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()

@pytest.mark.parametrize(
    "params",
    [
        {"page": 0},
        {"page": -1},
        {"page_size": 0},
        {"page_size": -5},
        {"page_size": 100},
        {"page": "abc"},
        {"page_size": "invalid"},
    ],
)
def test_public_promotions_reject_invalid_pagination(params):
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/homestay-promotions",
            params=params,
        )

    assert response.status_code == 422

def test_other_owner_cannot_update_promotion_via_api():
    from datetime import datetime, timezone

    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.database.connection import get_db
    from app.main import app
    from app.models.user import Base
    from app.routers.auth import get_current_user

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

    owner = make_user(701, "owner")
    other_owner = make_user(702, "owner")
    start = date.today() + timedelta(days=7)

    with TestingSession() as db:
        db.add_all([owner, other_owner])

        db.add(
            Homestay(
                id=1,
                owner_id=owner.id,
                name="Owner Homestay",
                location="Jaffna",
                price_per_night=5000,
                max_guests=3,
                is_active=True,
            )
        )

        db.add(
            HomestayPromotion(
                id=1,
                homestay_id=1,
                title="Original Promotion",
                discount_percent="10.00",
                start_date=start,
                end_date=start + timedelta(days=7),
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
                "/api/v1/homestay-promotions/1",
                json={
                    "title": "Unauthorized Update",
                    "description": "Should not be saved",
                    "discount_percent": "50.00",
                    "start_date": start.isoformat(),
                    "end_date": (
                        start + timedelta(days=10)
                    ).isoformat(),
                    "is_active": False,
                },
            )

        assert response.status_code == 403

        with TestingSession() as db:
            saved = db.get(HomestayPromotion, 1)

            assert saved is not None
            assert saved.title == "Original Promotion"
            assert saved.is_active is True
            assert str(saved.discount_percent) == "10.00"

    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(
            get_current_user,
            None,
        )
        engine.dispose()

def test_active_promotions_filter_by_homestay_id():
    from datetime import date
    from unittest.mock import MagicMock

    from app.routers.promotion import list_active_promotions

    db = MagicMock()

    # Simulate the filtered database query.
    query = db.query.return_value.join.return_value.filter.return_value
    query.filter.return_value = query

    query.count.return_value = 1

    promotion = MagicMock()
    promotion.id = 1
    promotion.homestay_id = 5
    promotion.title = "Test Promotion"
    promotion.description = None
    promotion.discount_percent = "20.00"
    promotion.start_date = date.today()
    promotion.end_date = date.today()
    promotion.is_active = True
    promotion.created_at = __import__(
        "datetime"
    ).datetime.now()

    query.order_by.return_value.offset.return_value.limit.return_value.all.return_value = [
        promotion
    ]

    result = list_active_promotions(
        db=db,
        page=1,
        page_size=10,
        homestay_id=5,
    )

    assert result.total == 1
    assert len(result.items) == 1
    assert result.items[0].homestay_id == 5

    # Verify the optional homestay filter was applied.
    assert query.filter.called


def test_owner_promotion_complete_flow():
    from datetime import datetime, timezone
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.database.connection import get_db
    from app.main import app
    from app.models.user import Base
    from app.routers.auth import get_current_user

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

    owner = make_user(501, "owner")

    with TestingSession() as db:
        db.add(owner)
        db.add(
            Homestay(
                id=1,
                owner_id=owner.id,
                name="Promotion Integration Homestay",
                location="Jaffna",
                price_per_night=5000,
                max_guests=4,
                is_active=True,
            )
        )
        db.commit()

    def override_db():
        with TestingSession() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: owner

    try:
        start = date.today()
        end = start + timedelta(days=10)

        with TestClient(app) as client:
            create_response = client.post(
                "/api/v1/homestay-promotions",
                json={
                    "homestay_id": 1,
                    "title": "Integration Special",
                    "description": "Initial offer",
                    "discount_percent": "10.00",
                    "start_date": start.isoformat(),
                    "end_date": end.isoformat(),
                },
            )

            assert create_response.status_code == 201, create_response.text
            promotion_id = create_response.json()["id"]

            mine_response = client.get("/api/v1/homestay-promotions/mine")
            assert mine_response.status_code == 200, mine_response.text

            assert any(
                item["id"] == promotion_id
                for item in mine_response.json()["items"]
            )

            update_response = client.put(
                f"/api/v1/homestay-promotions/{promotion_id}",
                json={
                    "title": "Updated Integration Special",
                    "description": "Updated offer",
                    "discount_percent": "25.00",
                    "start_date": start.isoformat(),
                    "end_date": end.isoformat(),
                    "is_active": True,
                },
            )

            assert update_response.status_code == 200, update_response.text
            assert update_response.json()["title"] == "Updated Integration Special"

            public_response = client.get(
                "/api/v1/homestay-promotions",
                params={"homestay_id": 1},
            )

            assert public_response.status_code == 200, public_response.text

            assert any(
                item["id"] == promotion_id
                and item["title"] == "Updated Integration Special"
                for item in public_response.json()["items"]
            )

    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)
        engine.dispose()
