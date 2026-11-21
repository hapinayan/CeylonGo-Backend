"""Workspace query verification on the explicitly isolated SQL Server target."""

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.homestay import Homestay
from app.models.provider_profile import ProviderProfile
from app.models.rental_booking import RentalBooking
from app.models.rental_vehicle import RentalVehicle
from app.models.user import User
from app.schemas.provider_workspace import DashboardPeriod
from app.services.provider_workspace import LISTING_MODELS, WorkspaceReader
from tests import test_provider_phase01_sqlserver
from tests.test_provider_workspace import seed_homestay_dashboard

engine = test_provider_phase01_sqlserver.engine
transaction = test_provider_phase01_sqlserver.transaction
pytestmark = pytest.mark.skipif(
    os.getenv("PROVIDER_WORKSPACE_SQLSERVER") != "1",
    reason="Enable explicitly for the isolated SQL Server test database",
)


def profile(db, category):
    user = User(
        full_name="Phase 02 SQL fixture",
        email=f"phase02-{uuid4().hex}@example.test",
        is_active=True,
        role="provider",
    )
    db.add(user)
    db.flush()
    provider = ProviderProfile(
        user_id=user.id,
        provider_type=category,
        approval_status="approved",
        business_name="Phase 02 SQL fixture",
        district="Colombo",
        phone="0771234567",
    )
    db.add(provider)
    db.flush()
    return provider


def traveler(db):
    user = User(
        full_name="Phase 02 SQL buyer",
        email=f"phase02-{uuid4().hex}@example.test",
        role="traveler",
        is_active=True,
    )
    db.add(user)
    db.flush()
    return user


def test_sqlserver_homestay_finance_groups_reviews_and_activity(transaction):
    db = transaction
    owner = profile(db, "homestay")
    buyer = traveler(db)
    listing = Homestay(
        owner_id=owner.user_id,
        provider_profile_id=owner.id,
        name="Phase 02 SQL homestay",
        location="Colombo",
        address="Public road",
        district="Colombo",
        city="Fort",
        approval_status="approved",
        is_active=True,
        price_per_night=1000,
        max_guests=2,
    )
    db.add(listing)
    db.flush()
    anchor = datetime.now(UTC).replace(tzinfo=None)
    seed_homestay_dashboard(
        db,
        provider_id=owner.id,
        listing_id=listing.id,
        buyer_id=buyer.id,
        anchor=anchor,
        paid_status="completed",
        refund_status="processed",
    )
    db.flush()
    reader = WorkspaceReader(db, owner)
    period = DashboardPeriod(start_date=anchor.date(), end_date=anchor.date())
    dashboard = reader.dashboard(period, activity_limit=2, reservation_limit=1)
    assert dashboard.listings.active == 1
    assert dashboard.bookings.total == 5
    assert dashboard.reviews.count == 2
    assert dashboard.reviews.average_rating == Decimal("4.50")
    assert dashboard.finance[0].verified_revenue == Decimal("1000.00")
    assert dashboard.finance[0].cash_collections == Decimal("300.00")
    assert dashboard.finance[0].verified_online_refunds == Decimal("200.00")
    assert dashboard.finance[0].commission == Decimal("80.00")
    assert dashboard.finance[0].provider_earnings == Decimal("720.00")
    assert dashboard.settlements[0].pending_amount == Decimal("900.00")
    assert reader.analytics(period).months[0].revenue == dashboard.finance
    assert reader.recent_activity(period, 2, 2).items
    other = WorkspaceReader(db, profile(db, "homestay"))
    assert other.dashboard(period, 1, 1).finance == []


def test_sqlserver_rental_mapped_timestamps_and_owned_reservations(transaction):
    db = transaction
    owner = profile(db, "rental")
    buyer = traveler(db)
    rental = RentalVehicle(
        provider_id=owner.id,
        vehicle_type="bicycle",
        vehicle_name="Phase 02 SQL rental",
        status="available",
        approval_status="approved",
        price_per_day=300,
    )
    db.add(rental)
    db.flush()
    start = datetime.now(UTC).replace(tzinfo=None) + timedelta(days=2)
    db.add(
        RentalBooking(
            vehicle_id=rental.id,
            user_id=buyer.id,
            total_price=300,
            status="confirmed",
            pickup_at=start,
            return_at=start + timedelta(days=1),
        )
    )
    db.flush()
    dashboard = WorkspaceReader(db, owner).dashboard(DashboardPeriod(), 5, 5)
    assert dashboard.bookings.upcoming == 1
    assert dashboard.upcoming_reservations[0].starts_at == start.replace(tzinfo=UTC)


@pytest.mark.parametrize("category", list(LISTING_MODELS))
def test_sqlserver_workspace_empty_read_plans_and_fixture_rollback(engine, category):
    with engine.connect() as connection:
        outer = connection.begin()
        test_provider_phase01_sqlserver.verify_target(connection)
        try:
            with Session(bind=connection) as db:
                owner = profile(db, category)
                owner_id = owner.id
                reader = WorkspaceReader(db, owner)
                assert reader.workspace().category == category
                assert reader.dashboard(DashboardPeriod(), 1, 1).listings.total == 0
                today = datetime.now(UTC).date()
                assert (
                    len(
                        reader.analytics(
                            DashboardPeriod(start_date=today, end_date=today)
                        ).months
                    )
                    == 1
                )
        finally:
            assert outer.is_active
            outer.rollback()
    with engine.connect() as connection:
        test_provider_phase01_sqlserver.verify_target(connection)
        assert (
            connection.scalar(
                select(ProviderProfile.id).where(ProviderProfile.id == owner_id)
            )
            is None
        )
