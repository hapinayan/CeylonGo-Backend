"""
CeylonGo - Phase 02 Homestay Enhancements Targeted Tests
========================================================
Validates:
1. Existing Homestay record compatibility (all new fields have backward-compatible defaults).
2. Existing provider ownership preservation (both provider_profile_id and owner_id supported).
3. Geographic coordinate constraints (valid latitudes/longitudes, boundary checks, NULL allowed).
4. Homestay availability block date validation (end_date > start_date, valid block types).
5. Extended booking lifecycle statuses ('pending', 'confirmed', 'in_progress', 'completed', 'cancelled', 'rejected', 'expired').
6. Cancellation snapshot constraints (free_cancellation_hours_snapshot >= 0, nightly_rate_snapshot, cancellation_policy_snapshot).
7. Homestay availability blocks relationships and cascading deletion.
8. API compatibility of existing schemas with newly extended models.
"""

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.booking import Booking
from app.models.homestay import (
    Homestay,
    HomestayAmenity,
    HomestayAvailabilityBlock,
    HomestayImage,
)
from app.models.provider_profile import ProviderProfile
from app.models.user import Base, User
from app.schemas.homestay import HomestayResponse
from app.schemas.booking import BookingResponse


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def configure_sqlite_connection(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
        dbapi_connection.create_function(
            "sysutcdatetime",
            0,
            lambda: datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        )

    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()


def test_legacy_homestay_compatibility(db_session):
    """Verifies that a homestay created with only legacy fields is valid and adopts defaults."""
    owner = User(
        full_name="Legacy Owner",
        email="legacy_owner@test.com",
        role="provider",
    )
    db_session.add(owner)
    db_session.flush()

    homestay = Homestay(
        name="Ella Mountain View",
        location="Ella, Badulla",
        price_per_night=Decimal("8500.00"),
        max_guests=4,
        owner_id=owner.id,
    )
    db_session.add(homestay)
    db_session.commit()

    # Verify defaults
    assert homestay.id is not None
    assert homestay.approval_status == "approved"
    assert homestay.verification_status == "unverified"
    assert homestay.check_in_time == time(14, 0)
    assert homestay.check_out_time == time(11, 0)
    assert homestay.free_cancellation_hours == 24
    assert homestay.province is None
    assert homestay.latitude is None
    assert homestay.longitude is None
    assert homestay.owner.id == owner.id

    # Verify Pydantic schema serialization preserves backward compatibility
    dto = HomestayResponse.model_validate(homestay)
    assert dto.id == homestay.id
    assert dto.name == "Ella Mountain View"
    assert dto.price_per_night == Decimal("8500.00")
    assert dto.owner_id == owner.id
    assert dto.provider_profile_id is None


def test_provider_profile_ownership_preservation(db_session):
    """Verifies dual ownership architecture: provider_profile_id is authoritative and owner_id is preserved."""
    user = User(
        full_name="Provider Owner",
        email="provider_homestay@test.com",
        role="provider",
    )
    db_session.add(user)
    db_session.flush()

    profile = ProviderProfile(
        user_id=user.id,
        business_name="Hill Country Homestays Ltd",
        provider_type="homestay",
        phone="+94771234567",
        district="Nuwara Eliya",
        approval_status="approved",
        identity_document_type="nic",
    )
    db_session.add(profile)
    db_session.flush()

    homestay = Homestay(
        name="Nuwara Eliya Tea Estate Bungalow",
        location="Nuwara Eliya",
        price_per_night=Decimal("15000.00"),
        max_guests=6,
        owner_id=user.id,
        provider_profile_id=profile.id,
        province="Central",
        district="Nuwara Eliya",
        city="Nuwara Eliya",
        address="12 Tea Garden Lane",
        latitude=Decimal("6.949700"),
        longitude=Decimal("80.789100"),
    )
    db_session.add(homestay)
    db_session.commit()

    queried = db_session.execute(select(Homestay).where(Homestay.id == homestay.id)).scalar_one()
    assert queried.provider_business_name == "Hill Country Homestays Ltd"
    assert queried.owner_id == user.id
    assert queried.provider_profile_id == profile.id
    assert queried.latitude == Decimal("6.949700")
    assert queried.longitude == Decimal("80.789100")


def test_geographic_coordinate_constraints(db_session):
    """Verifies that invalid latitude/longitude violate coordinate check constraints."""
    # Valid coordinates
    h_valid = Homestay(
        name="Valid Coords",
        location="Galle",
        price_per_night=Decimal("6000.00"),
        max_guests=2,
        latitude=Decimal("6.053519"),
        longitude=Decimal("80.220978"),
    )
    db_session.add(h_valid)
    db_session.commit()

    # Invalid latitude (> 90)
    h_invalid_lat = Homestay(
        name="Invalid Lat",
        location="Galle",
        price_per_night=Decimal("6000.00"),
        max_guests=2,
        latitude=Decimal("95.000000"),
        longitude=Decimal("80.220978"),
    )
    db_session.add(h_invalid_lat)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    # Invalid longitude (> 180)
    h_invalid_lng = Homestay(
        name="Invalid Lng",
        location="Galle",
        price_per_night=Decimal("6000.00"),
        max_guests=2,
        latitude=Decimal("6.053519"),
        longitude=Decimal("195.000000"),
    )
    db_session.add(h_invalid_lng)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_availability_block_date_constraints(db_session):
    """Verifies availability blocks enforce end_date > start_date and valid block types."""
    homestay = Homestay(
        name="Mirissa Beach Homestay",
        location="Mirissa",
        price_per_night=Decimal("7000.00"),
        max_guests=3,
    )
    db_session.add(homestay)
    db_session.flush()

    # Valid block
    block = HomestayAvailabilityBlock(
        homestay_id=homestay.id,
        start_date=date(2026, 12, 1),
        end_date=date(2026, 12, 5),
        block_type="maintenance",
        reason="Annual painting and plumbing repair",
    )
    db_session.add(block)
    db_session.commit()
    assert block.id is not None
    assert block.is_active is True

    # Inverted dates (end_date <= start_date)
    invalid_block = HomestayAvailabilityBlock(
        homestay_id=homestay.id,
        start_date=date(2026, 12, 10),
        end_date=date(2026, 12, 5),
        block_type="owner_unavailable",
    )
    db_session.add(invalid_block)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    # Equal dates (end_date == start_date)
    zero_day_block = HomestayAvailabilityBlock(
        homestay_id=homestay.id,
        start_date=date(2026, 12, 10),
        end_date=date(2026, 12, 10),
        block_type="maintenance",
    )
    db_session.add(zero_day_block)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    # Invalid block_type
    bad_type_block = HomestayAvailabilityBlock(
        homestay_id=homestay.id,
        start_date=date(2026, 12, 15),
        end_date=date(2026, 12, 20),
        block_type="unsupported_block_category",
    )
    db_session.add(bad_type_block)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_booking_extended_lifecycle_and_snapshots(db_session):
    """Verifies booking lifecycle statuses, payment details, and policy snapshots."""
    traveler = User(full_name="Traveler One", email="traveler1@test.com", role="traveler")
    admin = User(full_name="Admin Reviewer", email="admin1@test.com", role="admin")
    db_session.add_all([traveler, admin])
    db_session.flush()

    homestay = Homestay(
        name="Kandy Riverside Homestay",
        location="Kandy",
        price_per_night=Decimal("9000.00"),
        max_guests=4,
        cancellation_policy="Full refund up to 48 hours before check-in.",
        free_cancellation_hours=48,
    )
    db_session.add(homestay)
    db_session.flush()

    # Create booking with snapshots
    booking = Booking(
        homestay_id=homestay.id,
        user_id=traveler.id,
        check_in=date(2026, 12, 20),
        check_out=date(2026, 12, 23),
        guests=2,
        total_price=Decimal("27000.00"),
        status="pending",
        currency="LKR",
        payment_method="online",
        payment_status="pending",
        expires_at=datetime(2026, 12, 19, 12, 0, 0),
        nightly_rate_snapshot=homestay.price_per_night,
        cancellation_policy_snapshot=homestay.cancellation_policy,
        free_cancellation_hours_snapshot=homestay.free_cancellation_hours,
    )
    db_session.add(booking)
    db_session.commit()

    assert booking.id is not None
    assert booking.status == "pending"
    assert booking.currency == "LKR"
    assert booking.payment_method == "online"
    assert booking.nightly_rate_snapshot == Decimal("9000.00")
    assert booking.free_cancellation_hours_snapshot == 48

    # Test lifecycle transitions: confirmed -> in_progress -> completed -> cancelled
    for valid_status in ["confirmed", "in_progress", "completed", "cancelled", "rejected", "expired"]:
        booking.status = valid_status
        db_session.commit()
        assert booking.status == valid_status

    # Test invalid status rejection
    booking.status = "invalid_status_value"
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    # Test backward-compatible Pydantic serialization
    queried_b = db_session.execute(select(Booking).where(Booking.id == booking.id)).scalar_one()
    dto = BookingResponse.model_validate(queried_b)
    assert dto.id == queried_b.id
    assert dto.total_price == Decimal("27000.00")
    assert dto.status == "expired"


def test_booking_cancellation_hours_snapshot_constraint(db_session):
    """Verifies free_cancellation_hours_snapshot >= 0 constraint."""
    user = User(full_name="Traveler Two", email="traveler2@test.com")
    db_session.add(user)
    db_session.flush()

    homestay = Homestay(
        name="Sigiriya Homestay",
        location="Sigiriya",
        price_per_night=Decimal("6500.00"),
        max_guests=2,
    )
    db_session.add(homestay)
    db_session.flush()

    invalid_booking = Booking(
        homestay_id=homestay.id,
        user_id=user.id,
        check_in=date(2026, 12, 10),
        check_out=date(2026, 12, 12),
        guests=1,
        total_price=Decimal("13000.00"),
        free_cancellation_hours_snapshot=-1,
    )
    db_session.add(invalid_booking)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_cascade_deletion_of_availability_blocks(db_session):
    """Verifies that deleting a homestay cascades and deletes its availability blocks."""
    homestay = Homestay(
        name="Trinco Beach Cabin",
        location="Trincomalee",
        price_per_night=Decimal("11000.00"),
        max_guests=5,
    )
    db_session.add(homestay)
    db_session.flush()

    block1 = HomestayAvailabilityBlock(
        homestay_id=homestay.id,
        start_date=date(2026, 12, 1),
        end_date=date(2026, 12, 5),
        block_type="maintenance",
    )
    block2 = HomestayAvailabilityBlock(
        homestay_id=homestay.id,
        start_date=date(2026, 12, 10),
        end_date=date(2026, 12, 15),
        block_type="seasonal_closure",
    )
    db_session.add_all([block1, block2])
    db_session.commit()

    assert len(homestay.availability_blocks) == 2

    # Delete homestay
    db_session.delete(homestay)
    db_session.commit()

    remaining_blocks = db_session.execute(
        select(HomestayAvailabilityBlock).where(HomestayAvailabilityBlock.homestay_id == homestay.id)
    ).scalars().all()
    assert len(remaining_blocks) == 0
