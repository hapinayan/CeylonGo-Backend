


from datetime import UTC, date, datetime

from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database.connection import get_db
from app.models.booking import Booking
from app.models.homestay import Homestay
from app.models.user import User, Base
from app.routers.auth import get_current_user


from fastapi.testclient import TestClient

from app.main import app

from sqlalchemy import event
from app.models.promotion import HomestayPromotion

client = TestClient(app)


def test_booking_requires_authentication():
    response = client.get("/api/v1/bookings/me")

    assert response.status_code in (401, 403)

def test_create_booking_requires_authentication():
    booking_data = {
        "homestay_id": 1,
        "check_in": "2026-12-10",
        "check_out": "2026-12-12",
        "guests": 2,
    }

    response = client.post(
        "/api/v1/bookings",
        json=booking_data,
    )

    assert response.status_code in (401, 403)

def test_my_bookings_requires_authentication():
    response = client.get("/api/v1/bookings/me")

    assert response.status_code == 401
    assert response.json()["detail"] == "Not authenticated"

@pytest.fixture
def booking_test_db():
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

           lambda: datetime.now(UTC).replace(tzinfo=None).isoformat(sep=" "),

        )


    Base.metadata.create_all(
        bind=engine,
        tables=[
            User.__table__,
            Homestay.__table__,
            Booking.__table__,
            HomestayPromotion.__table__,
        ],
    )

    TestingSession = sessionmaker(bind=engine)

    try:
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()
    finally:
        engine.dispose()

@pytest.fixture
def booking_test_client(booking_test_db):
    def override_get_db():
        yield booking_test_db

    app.dependency_overrides[get_db] = override_get_db

    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.pop(get_db, None)

@pytest.fixture
def two_test_travelers(booking_test_db):
    traveler_a = User(
        full_name="Test Traveler A",
        email="traveler.a@example.com",
        role="traveler",
        is_active=True,
    )

    traveler_b = User(
        full_name="Test Traveler B",
        email="traveler.b@example.com",
        role="traveler",
        is_active=True,
    )

    booking_test_db.add_all([traveler_a, traveler_b])
    booking_test_db.commit()

    booking_test_db.refresh(traveler_a)
    booking_test_db.refresh(traveler_b)

    return traveler_a, traveler_b

@pytest.fixture
def test_homestay(booking_test_db):
    homestay = Homestay(
        name="Test Jaffna Homestay",
        description="Homestay for booking privacy tests",
        location="Jaffna",
        price_per_night=Decimal("5000.00"),
        max_guests=4,
        is_active=True,
    )

    booking_test_db.add(homestay)
    booking_test_db.commit()
    booking_test_db.refresh(homestay)

    return homestay

@pytest.fixture
def two_test_bookings(
    booking_test_db,
    two_test_travelers,
    test_homestay,
):
    traveler_a, traveler_b = two_test_travelers

    booking_a = Booking(
        homestay_id=test_homestay.id,
        user_id=traveler_a.id,
        check_in=date(2026, 12, 10),
        check_out=date(2026, 12, 12),
        guests=2,
        total_price=Decimal("10000.00"),
        status="confirmed",
        created_at=datetime(2026, 11, 15, 10, 0),
    )

    booking_b = Booking(
        homestay_id=test_homestay.id,
        user_id=traveler_b.id,
        check_in=date(2026, 12, 15),
        check_out=date(2026, 12, 17),
        guests=2,
        total_price=Decimal("10000.00"),
        status="confirmed",
        created_at=datetime(2026, 11, 15, 11, 0),
    )

    booking_test_db.add_all([booking_a, booking_b])
    booking_test_db.commit()

    return booking_a, booking_b

def test_traveler_can_only_see_own_bookings(
    booking_test_client,
    two_test_travelers,
    two_test_bookings,
):
    traveler_a, traveler_b = two_test_travelers
    booking_a, booking_b = two_test_bookings

    app.dependency_overrides[get_current_user] = lambda: traveler_a

    try:
        response = booking_test_client.get("/api/v1/bookings/me")

        assert response.status_code == 200

        bookings = response.json()

        assert len(bookings) == 1
        assert bookings[0]["id"] == booking_a.id
        assert bookings[0]["user_id"] == traveler_a.id

        assert all(
            booking["user_id"] != traveler_b.id
            for booking in bookings
        )

    finally:
        app.dependency_overrides.pop(get_current_user, None)

def test_traveler_b_can_only_see_own_bookings(
    booking_test_client,
    two_test_travelers,
    two_test_bookings,
):
    traveler_a, traveler_b = two_test_travelers
    booking_a, booking_b = two_test_bookings

    app.dependency_overrides[get_current_user] = lambda: traveler_b

    try:
        response = booking_test_client.get("/api/v1/bookings/me")

        assert response.status_code == 200

        bookings = response.json()

        assert len(bookings) == 1
        assert bookings[0]["id"] == booking_b.id
        assert bookings[0]["user_id"] == traveler_b.id

        assert all(
            booking["user_id"] != traveler_a.id
            for booking in bookings
        )

    finally:
        app.dependency_overrides.pop(get_current_user, None)

def test_booking_saves_highest_promotion_discount(
    booking_test_client,
    booking_test_db,
    two_test_travelers,
    test_homestay,
    monkeypatch,
):
    from datetime import timedelta
    from unittest.mock import MagicMock

    from app.routers import booking as booking_router

    traveler, _ = two_test_travelers

    check_in = date.today() + timedelta(days=10)
    check_out = check_in + timedelta(days=3)

    booking_test_db.add_all([
        HomestayPromotion(
            homestay_id=test_homestay.id,
            title="10 Percent Offer",
            discount_percent=Decimal("10.00"),
            start_date=check_in,
            end_date=check_out - timedelta(days=1),
            is_active=True,
        ),
        HomestayPromotion(
            homestay_id=test_homestay.id,
            title="25 Percent Offer",
            discount_percent=Decimal("25.00"),
            start_date=check_in,
            end_date=check_out - timedelta(days=1),
            is_active=True,
        ),
        HomestayPromotion(
            homestay_id=test_homestay.id,
            title="Expired 50 Percent Offer",
            discount_percent=Decimal("50.00"),
            start_date=check_in - timedelta(days=10),
            end_date=check_in - timedelta(days=1),
            is_active=True,
        ),
    ])
    booking_test_db.commit()

    # SQLite does not support SQL Server's sp_getapplock.
    # Mock only the lock statement; keep real SQLite queries.
    original_execute = booking_test_db.execute

    def execute_with_mock_lock(statement, params=None, **kwargs):
        if "sp_getapplock" in str(statement):
            lock_result = MagicMock()
            lock_result.scalar.return_value = 0
            return lock_result

        return original_execute(statement, params, **kwargs)

    monkeypatch.setattr(
        booking_test_db,
        "execute",
        execute_with_mock_lock,
    )

    app.dependency_overrides[get_current_user] = lambda: traveler

    try:
        response = booking_test_client.post(
            "/api/v1/bookings",
            json={
                "homestay_id": test_homestay.id,
                "check_in": check_in.isoformat(),
                "check_out": check_out.isoformat(),
                "guests": 2,
            },
        )

        assert response.status_code == 201, response.tex

        booking_id = response.json()["id"]

        assert Decimal(
            str(response.json()["total_price"])
        ) == Decimal("11250.00")

        booking_test_db.expire_all()

        saved_booking = booking_test_db.get(
            Booking,
            booking_id,
        )

        assert saved_booking is not None
        assert saved_booking.user_id == traveler.id
        assert saved_booking.status == "confirmed"
        assert saved_booking.total_price == Decimal("11250.00")

    finally:
        app.dependency_overrides.pop(get_current_user, None)

def test_booking_without_promotion_saves_original_price(
    booking_test_client,
    booking_test_db,
    two_test_travelers,
    test_homestay,
    monkeypatch,
):
    from datetime import timedelta
    from unittest.mock import MagicMock

    traveler, _ = two_test_travelers

    check_in = date.today() + timedelta(days=20)
    check_out = check_in + timedelta(days=3)

    # No promotions are created for this homestay.

    original_execute = booking_test_db.execute

    def execute_with_mock_lock(statement, params=None, **kwargs):
        if "sp_getapplock" in str(statement):
            lock_result = MagicMock()
            lock_result.scalar.return_value = 0
            return lock_result

        return original_execute(statement, params, **kwargs)

    monkeypatch.setattr(
        booking_test_db,
        "execute",
        execute_with_mock_lock,
    )

    app.dependency_overrides[get_current_user] = lambda: traveler

    try:
        response = booking_test_client.post(
            "/api/v1/bookings",
            json={
                "homestay_id": test_homestay.id,
                "check_in": check_in.isoformat(),
                "check_out": check_out.isoformat(),
                "guests": 2,
            },
        )

        assert response.status_code == 201, response.tex

        booking_id = response.json()["id"]

        assert Decimal(
            str(response.json()["total_price"])
        ) == Decimal("15000.00")

        booking_test_db.expire_all()

        saved_booking = booking_test_db.get(
            Booking,
            booking_id,
        )

        assert saved_booking is not None
        assert saved_booking.total_price == Decimal("15000.00")
        assert saved_booking.status == "confirmed"

    finally:
        app.dependency_overrides.pop(get_current_user, None)

@pytest.mark.parametrize(
    "promotion_type",
    ["expired", "inactive", "partial_dates"],
)
def test_booking_ignores_ineligible_promotions(
    booking_test_client,
    booking_test_db,
    two_test_travelers,
    test_homestay,
    monkeypatch,
    promotion_type,
):
    from datetime import timedelta
    from unittest.mock import MagicMock

    traveler, _ = two_test_travelers

    check_in = date.today() + timedelta(days=30)
    check_out = check_in + timedelta(days=3)

    if promotion_type == "expired":
        start_date = check_in - timedelta(days=10)
        end_date = check_in - timedelta(days=1)
        is_active = True

    elif promotion_type == "inactive":
        start_date = check_in
        end_date = check_out - timedelta(days=1)
        is_active = False

    else:
        # Covers only part of the booking.
        start_date = check_in + timedelta(days=1)
        end_date = check_out - timedelta(days=1)
        is_active = True

    booking_test_db.add(
        HomestayPromotion(
            homestay_id=test_homestay.id,
            title=f"Test {promotion_type} Offer",
            discount_percent=Decimal("50.00"),
            start_date=start_date,
            end_date=end_date,
            is_active=is_active,
        )
    )
    booking_test_db.commit()

    # Mock SQL Server-specific locking only.
    original_execute = booking_test_db.execute

    def execute_with_mock_lock(statement, params=None, **kwargs):
        if "sp_getapplock" in str(statement):
            lock_result = MagicMock()
            lock_result.scalar.return_value = 0
            return lock_result

        return original_execute(statement, params, **kwargs)

    monkeypatch.setattr(
        booking_test_db,
        "execute",
        execute_with_mock_lock,
    )

    app.dependency_overrides[get_current_user] = lambda: traveler

    try:
        response = booking_test_client.post(
            "/api/v1/bookings",
            json={
                "homestay_id": test_homestay.id,
                "check_in": check_in.isoformat(),
                "check_out": check_out.isoformat(),
                "guests": 2,
            },
        )

        assert response.status_code == 201, response.tex

        booking_id = response.json()["id"]

        # Ineligible promotion must not reduce the price.
        assert Decimal(
            str(response.json()["total_price"])
        ) == Decimal("15000.00")

        booking_test_db.expire_all()

        saved_booking = booking_test_db.get(
            Booking,
            booking_id,
        )

        assert saved_booking is not None
        assert saved_booking.total_price == Decimal("15000.00")
        assert saved_booking.status == "confirmed"

    finally:
        app.dependency_overrides.pop(get_current_user, None)

def test_overlapping_booking_is_rejected(
    booking_test_client,
    booking_test_db,
    two_test_travelers,
    test_homestay,
    monkeypatch,
):
    from datetime import timedelta
    from unittest.mock import MagicMock

    traveler_a, traveler_b = two_test_travelers

    check_in = date.today() + timedelta(days=40)
    check_out = check_in + timedelta(days=3)

    # An existing confirmed booking.
    existing_booking = Booking(
        homestay_id=test_homestay.id,
        user_id=traveler_a.id,
        check_in=check_in,
        check_out=check_out,
        guests=2,
        total_price=Decimal("15000.00"),
        status="confirmed",
    )

    booking_test_db.add(existing_booking)
    booking_test_db.commit()

    # Mock SQL Server-specific lock acquisition only.
    original_execute = booking_test_db.execute

    def execute_with_mock_lock(statement, params=None, **kwargs):
        if "sp_getapplock" in str(statement):
            lock_result = MagicMock()
            lock_result.scalar.return_value = 0
            return lock_result

        return original_execute(statement, params, **kwargs)

    monkeypatch.setattr(
        booking_test_db,
        "execute",
        execute_with_mock_lock,
    )

    app.dependency_overrides[get_current_user] = lambda: traveler_b

    try:
        response = booking_test_client.post(
            "/api/v1/bookings",
            json={
                "homestay_id": test_homestay.id,
                "check_in": (
                    check_in + timedelta(days=1)
                ).isoformat(),
                "check_out": (
                    check_out + timedelta(days=1)
                ).isoformat(),
                "guests": 2,
            },
        )

        assert response.status_code == 409, response.tex

        assert response.json()["detail"] == (
            "Homestay is already booked for these dates"
        )

        booking_test_db.expire_all()

        bookings = (
            booking_test_db.query(Booking)
            .filter(
                Booking.homestay_id == test_homestay.id
            )
            .all()
        )

        # The rejected request must not create a booking.
        assert len(bookings) == 1
        assert bookings[0].user_id == traveler_a.id

    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_create_booking_appears_in_my_bookings(
    booking_test_client,
    booking_test_db,
    two_test_travelers,
    test_homestay,
    monkeypatch,
):
    from datetime import timedelta
    from unittest.mock import MagicMock

    traveler_a, traveler_b = two_test_travelers

    check_in = date.today() + timedelta(days=30)
    check_out = check_in + timedelta(days=2)

    # SQLite does not support SQL Server's sp_getapplock.
    original_execute = booking_test_db.execute

    def execute_with_mock_lock(statement, params=None, **kwargs):
        if "sp_getapplock" in str(statement):
            result = MagicMock()
            result.scalar.return_value = 0
            return result

        return original_execute(statement, params, **kwargs)

    monkeypatch.setattr(
        booking_test_db,
        "execute",
        execute_with_mock_lock,
    )

    app.dependency_overrides[get_current_user] = lambda: traveler_a

    try:
        create_response = booking_test_client.post(
            "/api/v1/bookings",
            json={
                "homestay_id": test_homestay.id,
                "check_in": check_in.isoformat(),
                "check_out": check_out.isoformat(),
                "guests": 2,
            },
        )

        assert create_response.status_code == 201, create_response.tex

        created_booking = create_response.json()
        booking_id = created_booking["id"]

        my_response = booking_test_client.get(
            "/api/v1/bookings/me"
        )

        assert my_response.status_code == 200

        my_bookings = my_response.json()

        assert any(
            booking["id"] == booking_id
            and booking["user_id"] == traveler_a.id
            and booking["homestay_id"] == test_homestay.id
            for booking in my_bookings
        )

        # The second traveler must not see this booking.
        app.dependency_overrides[get_current_user] = lambda: traveler_b

        other_response = booking_test_client.get(
            "/api/v1/bookings/me"
        )

        assert other_response.status_code == 200

        assert all(
            booking["id"] != booking_id
            for booking in other_response.json()
        )

    finally:
        app.dependency_overrides.pop(get_current_user, None)
