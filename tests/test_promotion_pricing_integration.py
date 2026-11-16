
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.promotion_pricing import calculate_booking_price
from app.models.homestay import Homestay
from app.models.promotion import HomestayPromotion
from app.models.user import Base


def test_highest_eligible_discount_with_real_database_query():
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

    TestingSession = sessionmaker(bind=engine)

    check_in = date.today() + timedelta(days=5)
    check_out = check_in + timedelta(days=3)

    try:
        with TestingSession() as db:
            db.add(
                Homestay(
                    id=1,
                    name="Jaffna Test Homestay",
                    location="Jaffna",
                    price_per_night=Decimal("5000.00"),
                    max_guests=3,
                    is_active=True,
                )
            )

            db.add_all([
                HomestayPromotion(
                    homestay_id=1,
                    title="Valid Offer A",
                    discount_percent=Decimal("10.00"),
                    start_date=check_in,
                    end_date=check_out - timedelta(days=1),
                    is_active=True,
                ),
                HomestayPromotion(
                    homestay_id=1,
                    title="Valid Offer B",
                    discount_percent=Decimal("25.00"),
                    start_date=check_in,
                    end_date=check_out - timedelta(days=1),
                    is_active=True,
                ),
                HomestayPromotion(
                    homestay_id=1,
                    title="Expired Offer",
                    discount_percent=Decimal("50.00"),
                    start_date=check_in - timedelta(days=10),
                    end_date=check_in - timedelta(days=1),
                    is_active=True,
                ),
            ])

            db.commit()

            result = calculate_booking_price(
                db=db,
                homestay_id=1,
                check_in=check_in,
                check_out=check_out,
                price_per_night=Decimal("5000.00"),
            )

            assert result == Decimal("11250.00")

    finally:
        engine.dispose()
