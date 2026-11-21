
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database.connection import get_db
from app.main import app
from app.models.homestay import Homestay
from app.models.promotion import HomestayPromotion
from app.models.user import Base, User
from app.models.provider_profile import ProviderProfile


def test_price_preview_applies_best_promotion():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def register_sqlite_functions(connection, record):
        connection.create_function(
            "sysutcdatetime",
            0,
            lambda: datetime.now(
                timezone.utc
            ).replace(tzinfo=None).isoformat(sep=" "),
        )

    Base.metadata.create_all(
        bind=engine,
        tables=[
            User.__table__,
            ProviderProfile.__table__,
            Homestay.__table__,
            HomestayPromotion.__table__,
        ],
    )

    TestingSession = sessionmaker(bind=engine)

    check_in = date.today() + timedelta(days=10)
    check_out = check_in + timedelta(days=3)

    try:
        with TestingSession() as db:
            homestay = Homestay(
                name="Jaffna Heritage Homestay",
                location="Jaffna",
                price_per_night=Decimal("5000.00"),
                max_guests=4,
                is_active=True,
            )

            db.add(homestay)
            db.flush()

            db.add_all([
                HomestayPromotion(
                    homestay_id=homestay.id,
                    title="10% Offer",
                    discount_percent=Decimal("10.00"),
                    start_date=check_in,
                    end_date=check_out,
                    is_active=True,
                ),
                HomestayPromotion(
                    homestay_id=homestay.id,
                    title="25% Offer",
                    discount_percent=Decimal("25.00"),
                    start_date=check_in,
                    end_date=check_out,
                    is_active=True,
                ),
            ])

            db.commit()
            homestay_id = homestay.id

        def override_get_db():
            with TestingSession() as db:
                yield db

        app.dependency_overrides[get_db] = override_get_db

        try:
            with TestClient(app) as client:
                response = client.get(
                    f"/api/v1/homestays/{homestay_id}/price-preview",
                    params={
                        "check_in": check_in.isoformat(),
                        "check_out": check_out.isoformat(),
                    },
                )

            assert response.status_code == 200, response.text

            data = response.json()

            assert data["homestay_id"] == homestay_id
            assert data["nights"] == 3
            assert Decimal(str(data["original_price"])) == Decimal("15000.00")
            assert Decimal(str(data["discount_amount"])) == Decimal("3750.00")
            assert Decimal(str(data["final_price"])) == Decimal("11250.00")
            assert data["discount_fixed_amount"] is None

        finally:
            app.dependency_overrides.pop(get_db, None)

    finally:
        engine.dispose()

from fastapi.testclient import TestClient


def test_price_preview_rejects_invalid_dates():
    with TestClient(app) as client:
        today = date.today()
        tomorrow = today + timedelta(days=1)
        yesterday = today - timedelta(days=1)

        # Check-in cannot be in the past.
        response = client.get(
            "/api/v1/homestays/1/price-preview",
            params={
                "check_in": yesterday.isoformat(),
                "check_out": tomorrow.isoformat(),
            },
        )
        assert response.status_code == 422

        # Check-out must be after check-in.
        response = client.get(
            "/api/v1/homestays/1/price-preview",
            params={
                "check_in": tomorrow.isoformat(),
                "check_out": tomorrow.isoformat(),
            },
        )
        assert response.status_code == 422

        # Missing required date.
        response = client.get(
            "/api/v1/homestays/1/price-preview",
            params={
                "check_in": tomorrow.isoformat(),
            },
        )
        assert response.status_code == 422

import pytest


@pytest.mark.parametrize(
    "is_active, expected_status, expected_price",
    [
        (True, 200, "15000.00"),
        (False, 404, None),
    ],
)
def test_price_preview_without_promotion_and_inactive_homestay(
    is_active,
    expected_status,
    expected_price,
):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def register_sqlite_functions(connection, record):
        connection.create_function(
            "sysutcdatetime",
            0,
            lambda: datetime.now(
                timezone.utc
            ).replace(tzinfo=None).isoformat(sep=" "),
        )

    Base.metadata.create_all(
        bind=engine,
        tables=[
            User.__table__,
            ProviderProfile.__table__,
            Homestay.__table__,
            HomestayPromotion.__table__,
        ],
    )

    TestingSession = sessionmaker(bind=engine)

    check_in = date.today() + timedelta(days=10)
    check_out = check_in + timedelta(days=3)

    try:
        with TestingSession() as db:
            homestay = Homestay(
                name="Test Homestay",
                location="Jaffna",
                price_per_night=Decimal("5000.00"),
                max_guests=4,
                is_active=is_active,
            )

            db.add(homestay)
            db.commit()
            homestay_id = homestay.id

        def override_get_db():
            with TestingSession() as db:
                yield db

        app.dependency_overrides[get_db] = override_get_db

        try:
            with TestClient(app) as client:
                response = client.get(
                    f"/api/v1/homestays/{homestay_id}/price-preview",
                    params={
                        "check_in": check_in.isoformat(),
                        "check_out": check_out.isoformat(),
                    },
                )

            assert response.status_code == expected_status, response.text

            if expected_status == 200:
                data = response.json()

                assert Decimal(
                    str(data["original_price"])
                ) == Decimal("15000.00")

                assert Decimal(
                    str(data["discount_amount"])
                ) == Decimal("0.00")
                assert data["discount_fixed_amount"] is None

                assert Decimal(
                    str(data["final_price"])
                ) == Decimal(expected_price)

        finally:
            app.dependency_overrides.pop(get_db, None)

    finally:
        engine.dispose()
