
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


def test_public_promotions_filter_by_homestay():
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
            Homestay.__table__,
            HomestayPromotion.__table__,
        ],
    )

    TestingSession = sessionmaker(bind=engine)

    today = date.today()

    try:
        with TestingSession() as db:
            homestay_a = Homestay(
                name="Jaffna Homestay",
                location="Jaffna",
                price_per_night=Decimal("5000.00"),
                max_guests=3,
                is_active=True,
            )

            homestay_b = Homestay(
                name="Kandy Homestay",
                location="Kandy",
                price_per_night=Decimal("6000.00"),
                max_guests=4,
                is_active=True,
            )

            db.add_all([homestay_a, homestay_b])
            db.flush()

            db.add_all([
                HomestayPromotion(
                    homestay_id=homestay_a.id,
                    title="Jaffna Offer",
                    discount_percent=Decimal("20.00"),
                    start_date=today - timedelta(days=1),
                    end_date=today + timedelta(days=10),
                    is_active=True,
                ),
                HomestayPromotion(
                    homestay_id=homestay_b.id,
                    title="Kandy Offer",
                    discount_percent=Decimal("30.00"),
                    start_date=today - timedelta(days=1),
                    end_date=today + timedelta(days=10),
                    is_active=True,
                ),
            ])

            db.commit()
            homestay_a_id = homestay_a.id
            homestay_b_id = homestay_b.id

        def override_get_db():
            with TestingSession() as db:
                yield db

        app.dependency_overrides[get_db] = override_get_db

        try:
            with TestClient(app) as client:
                response = client.get(
                    "/api/v1/homestay-promotions",
                    params={"homestay_id": homestay_a_id},
                )

            assert response.status_code == 200, response.text

            data = response.json()

            assert data["total"] == 1
            assert len(data["items"]) == 1
            assert data["items"][0]["homestay_id"] == homestay_a_id
            assert data["items"][0]["title"] == "Jaffna Offer"

            assert all(
                item["homestay_id"] != homestay_b_id
                for item in data["items"]
            )

        finally:
            app.dependency_overrides.pop(get_db, None)

    finally:
        engine.dispose()
