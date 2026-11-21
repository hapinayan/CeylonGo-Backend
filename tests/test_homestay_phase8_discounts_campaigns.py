"""
CeylonGo - Phase 08 Homestay Discounts & Sponsored Campaigns Targeted Tests
=============================================================================
Validates:
1. Percentage discount calculation.
2. Fixed-amount discount calculation.
3. Invalid discount combinations (both percent and fixed, negative fixed, percent > 100, invalid dates).
4. Expired promotion exclusion.
5. Booking price calculation (non-negative total, savings).
6. Campaign listing ownership enforcement.
7. Unpaid campaign exclusion from sponsored listing.
8. Expired campaign exclusion from sponsored listing.
9. Listing-specific sponsored status verification (only the exact linked listing is sponsored).
10. Prevention of unauthorized booking event creation via public event endpoint.
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.promotion_pricing import (
    calculate_booking_price,
    calculate_detailed_booking_price,
    record_campaign_booking_conversion,
)
from app.models.homestay import Homestay
from app.models.promotion import HomestayPromotion
from app.models.provider_profile import ProviderProfile
from app.models.shared import (
    AdvertisingPayment,
    ListingReference,
    PromotionCampaign,
    PromotionEvent,
)
from app.models.user import Base, User
from app.routers.homestay import populate_homestay_promotions_and_sponsorship
from app.routers.homestay_campaigns import (
    create_homestay_campaign,
    log_promotion_event,
)
from app.schemas.promotion import (
    PromotionCreateRequest,
    PromotionUpdateRequest,
)
from app.schemas.promotion_campaign import (
    PromotionCampaignCreate,
    PromotionEventCreate,
)


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
        engine.dispose()


@pytest.fixture
def seed_data(db_session):
    """Seed provider profiles and homestays."""
    provider_user_1 = User(
        full_name="Host Silva",
        email="host.silva@test.com",
        role="provider",
        is_active=True,
    )
    provider_user_2 = User(
        full_name="Host Perera",
        email="host.perera@test.com",
        role="provider",
        is_active=True,
    )
    db_session.add_all([provider_user_1, provider_user_2])
    db_session.flush()

    profile_1 = ProviderProfile(
        user_id=provider_user_1.id,
        business_name="Silva Homestays",
        provider_type="homestay",
        phone="+94771112222",
        district="Kandy",
        approval_status="approved",
    )
    profile_2 = ProviderProfile(
        user_id=provider_user_2.id,
        business_name="Perera Lodges",
        provider_type="homestay",
        phone="+94773334444",
        district="Galle",
        approval_status="approved",
    )
    db_session.add_all([profile_1, profile_2])
    db_session.flush()

    homestay_1a = Homestay(
        name="Kandy Mountain View",
        location="Kandy",
        district="Kandy",
        price_per_night=Decimal("5000.00"),
        max_guests=4,
        is_active=True,
        approval_status="approved",
        provider_profile_id=profile_1.id,
        owner_id=provider_user_1.id,
    )
    homestay_1b = Homestay(
        name="Kandy River Retreat",
        location="Kandy",
        district="Kandy",
        price_per_night=Decimal("8000.00"),
        max_guests=6,
        is_active=True,
        approval_status="approved",
        provider_profile_id=profile_1.id,
        owner_id=provider_user_1.id,
    )
    homestay_2 = Homestay(
        name="Galle Fort Villa",
        location="Galle",
        district="Galle",
        price_per_night=Decimal("12000.00"),
        max_guests=5,
        is_active=True,
        approval_status="approved",
        provider_profile_id=profile_2.id,
        owner_id=provider_user_2.id,
    )
    db_session.add_all([homestay_1a, homestay_1b, homestay_2])
    db_session.flush()

    # Listing references
    ref_1a = ListingReference(
        module_type="homestay",
        target_id=homestay_1a.id,
        title=homestay_1a.name,
        district="Kandy",
        provider_profile_id=profile_1.id,
    )
    ref_1b = ListingReference(
        module_type="homestay",
        target_id=homestay_1b.id,
        title=homestay_1b.name,
        district="Kandy",
        provider_profile_id=profile_1.id,
    )
    ref_2 = ListingReference(
        module_type="homestay",
        target_id=homestay_2.id,
        title=homestay_2.name,
        district="Galle",
        provider_profile_id=profile_2.id,
    )
    db_session.add_all([ref_1a, ref_1b, ref_2])
    db_session.commit()

    return {
        "user_1": provider_user_1,
        "user_2": provider_user_2,
        "profile_1": profile_1,
        "profile_2": profile_2,
        "homestay_1a": homestay_1a,
        "homestay_1b": homestay_1b,
        "homestay_2": homestay_2,
        "ref_1a": ref_1a,
        "ref_1b": ref_1b,
        "ref_2": ref_2,
    }


# ==============================================================================
# 1. Percentage discount calculation
# ==============================================================================
def test_percentage_discount_calculation(db_session, seed_data):
    h = seed_data["homestay_1a"]
    today = date.today()
    check_in = today + timedelta(days=5)
    check_out = today + timedelta(days=7)  # 2 nights

    promo = HomestayPromotion(
        homestay_id=h.id,
        title="20% Off Mountain View",
        discount_type="percentage",
        discount_percent=Decimal("20.00"),
        start_date=check_in - timedelta(days=1),
        end_date=check_out + timedelta(days=1),
        is_active=True,
    )
    db_session.add(promo)
    db_session.commit()

    breakdown = calculate_detailed_booking_price(
        db=db_session,
        homestay_id=h.id,
        check_in=check_in,
        check_out=check_out,
        price_per_night=h.price_per_night,
    )

    # 2 nights * 5000 = 10000 base
    # 20% savings = 2000
    # Final = 8000
    assert breakdown["nights"] == 2
    assert breakdown["original_price"] == Decimal("10000.00")
    assert breakdown["savings"] == Decimal("2000.00")
    assert breakdown["final_price"] == Decimal("8000.00")
    assert breakdown["discount_type"] == "percentage"
    assert breakdown["discount_percent"] == Decimal("20.00")


# ==============================================================================
# 2. Fixed-amount discount calculation
# ==============================================================================
def test_fixed_amount_discount_calculation(db_session, seed_data):
    h = seed_data["homestay_1a"]
    today = date.today()
    check_in = today + timedelta(days=5)
    check_out = today + timedelta(days=7)  # 2 nights

    promo = HomestayPromotion(
        homestay_id=h.id,
        title="LKR 1,500 Off",
        discount_type="fixed",
        discount_amount=Decimal("1500.00"),
        start_date=check_in - timedelta(days=1),
        end_date=check_out + timedelta(days=1),
        is_active=True,
    )
    db_session.add(promo)
    db_session.commit()

    breakdown = calculate_detailed_booking_price(
        db=db_session,
        homestay_id=h.id,
        check_in=check_in,
        check_out=check_out,
        price_per_night=h.price_per_night,
    )

    # 2 nights * 5000 = 10000 base
    # Fixed savings = 1500
    # Final = 8500
    assert breakdown["original_price"] == Decimal("10000.00")
    assert breakdown["savings"] == Decimal("1500.00")
    assert breakdown["final_price"] == Decimal("8500.00")
    assert breakdown["discount_type"] == "fixed"
    assert breakdown["discount_amount"] == Decimal("1500.00")


# ==============================================================================
# 3. Invalid discount combinations
# ==============================================================================
def test_invalid_discount_combinations():
    today = date.today()
    tomorrow = today + timedelta(days=1)

    # Both percentage and fixed provided
    with pytest.raises(ValidationError):
        PromotionCreateRequest(
            title="Invalid Both",
            discount_type="percentage",
            discount_percent=Decimal("15.00"),
            discount_amount=Decimal("1000.00"),
            start_date=today,
            end_date=tomorrow,
        )

    # Percentage without discount_percent
    with pytest.raises(ValidationError):
        PromotionCreateRequest(
            title="Missing Percent",
            discount_type="percentage",
            discount_percent=None,
            discount_amount=None,
            start_date=today,
            end_date=tomorrow,
        )

    # Fixed without discount_amount
    with pytest.raises(ValidationError):
        PromotionCreateRequest(
            title="Missing Fixed Amount",
            discount_type="fixed",
            discount_percent=None,
            discount_amount=None,
            start_date=today,
            end_date=tomorrow,
        )

    # Negative fixed amount
    with pytest.raises(ValidationError):
        PromotionCreateRequest(
            title="Negative Fixed",
            discount_type="fixed",
            discount_amount=Decimal("-500.00"),
            start_date=today,
            end_date=tomorrow,
        )

    # Percent > 100
    with pytest.raises(ValidationError):
        PromotionCreateRequest(
            title="Percent > 100",
            discount_type="percentage",
            discount_percent=Decimal("120.00"),
            start_date=today,
            end_date=tomorrow,
        )

    # End date before start date
    with pytest.raises(ValidationError):
        PromotionCreateRequest(
            title="Invalid Date Range",
            discount_type="percentage",
            discount_percent=Decimal("20.00"),
            start_date=tomorrow,
            end_date=today,
        )


# ==============================================================================
# 4. Expired promotion exclusion
# ==============================================================================
def test_expired_promotion_exclusion(db_session, seed_data):
    h = seed_data["homestay_1a"]
    today = date.today()
    check_in = today + timedelta(days=10)
    check_out = today + timedelta(days=12)

    # Promotion ended yesterday
    promo = HomestayPromotion(
        homestay_id=h.id,
        title="Old Flash Sale",
        discount_type="percentage",
        discount_percent=Decimal("50.00"),
        start_date=today - timedelta(days=10),
        end_date=today - timedelta(days=1),
        is_active=True,
    )
    db_session.add(promo)
    db_session.commit()

    breakdown = calculate_detailed_booking_price(
        db=db_session,
        homestay_id=h.id,
        check_in=check_in,
        check_out=check_out,
        price_per_night=h.price_per_night,
    )

    assert breakdown["savings"] == Decimal("0.00")
    assert breakdown["final_price"] == Decimal("10000.00")
    assert breakdown["applied_promotion_id"] is None


# ==============================================================================
# 5. Booking price calculation (non-negative total, savings)
# ==============================================================================
def test_booking_price_non_negative_cap(db_session, seed_data):
    h = seed_data["homestay_1a"]
    today = date.today()
    check_in = today + timedelta(days=3)
    check_out = today + timedelta(days=4)  # 1 night * 5000 = 5000 base

    # Fixed discount of 15,000 exceeds base price of 5,000
    promo = HomestayPromotion(
        homestay_id=h.id,
        title="Gigantic Voucher",
        discount_type="fixed",
        discount_amount=Decimal("15000.00"),
        start_date=check_in - timedelta(days=1),
        end_date=check_out + timedelta(days=1),
        is_active=True,
    )
    db_session.add(promo)
    db_session.commit()

    breakdown = calculate_detailed_booking_price(
        db=db_session,
        homestay_id=h.id,
        check_in=check_in,
        check_out=check_out,
        price_per_night=h.price_per_night,
    )

    # Savings must be capped at base_price (5000), final_price must be 0.00, never negative
    assert breakdown["original_price"] == Decimal("5000.00")
    assert breakdown["savings"] == Decimal("5000.00")
    assert breakdown["discount_amount"] == Decimal("5000.00")
    assert breakdown["discount_fixed_amount"] == Decimal("15000.00")
    assert breakdown["final_price"] == Decimal("0.00")

    # Also test calculate_booking_price wrapper
    total = calculate_booking_price(
        db=db_session,
        homestay_id=h.id,
        check_in=check_in,
        check_out=check_out,
        price_per_night=h.price_per_night,
    )
    assert total == Decimal("0.00")


# ==============================================================================
# 6. Campaign listing ownership enforcement
# ==============================================================================
def test_campaign_listing_ownership_enforcement(db_session, seed_data):
    h1 = seed_data["homestay_1a"]
    profile_2 = seed_data["profile_2"]  # Provider 2 does NOT own homestay 1a

    now = datetime.now(timezone.utc)
    payload = PromotionCampaignCreate(
        campaign_name="Unauthorized Ad",
        homestay_id=h1.id,
        budget=Decimal("5000.00"),
        starts_at=now,
        ends_at=now + timedelta(days=7),
    )

    # Provider 2 attempts to create a campaign targeting Provider 1's homestay
    with pytest.raises(HTTPException) as exc_info:
        create_homestay_campaign(
            homestay_id=h1.id,
            payload=payload,
            db=db_session,
            current_provider=profile_2,
        )
    assert exc_info.value.status_code == 403
    assert "You can only create sponsored campaigns for your own homestays" in exc_info.value.detail


# ==============================================================================
# 7. Unpaid campaign exclusion from sponsored listing
# ==============================================================================
def test_unpaid_campaign_exclusion(db_session, seed_data):
    h = seed_data["homestay_1a"]
    ref = seed_data["ref_1a"]
    profile = seed_data["profile_1"]
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # Campaign is active and unexpired, but NO AdvertisingPayment exists
    campaign = PromotionCampaign(
        provider_profile_id=profile.id,
        listing_reference_id=ref.id,
        campaign_name="Unpaid Campaign",
        target_module="homestay",
        budget=Decimal("10000.00"),
        spent_amount=Decimal("0.00"),
        currency="LKR",
        status="active",
        starts_at=now - timedelta(days=1),
        ends_at=now + timedelta(days=10),
    )
    db_session.add(campaign)
    db_session.commit()

    results = populate_homestay_promotions_and_sponsorship(db_session, [h], now=now)
    assert len(results) == 1
    assert results[0].is_sponsored is False
    assert results[0].sponsored_campaign_id is None


# ==============================================================================
# 8. Expired campaign exclusion from sponsored listing
# ==============================================================================
def test_expired_campaign_exclusion(db_session, seed_data):
    h = seed_data["homestay_1a"]
    ref = seed_data["ref_1a"]
    profile = seed_data["profile_1"]
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # Campaign had verified payment, but ended 2 days ago
    campaign = PromotionCampaign(
        provider_profile_id=profile.id,
        listing_reference_id=ref.id,
        campaign_name="Expired Campaign",
        target_module="homestay",
        budget=Decimal("10000.00"),
        spent_amount=Decimal("10000.00"),
        currency="LKR",
        status="active",
        starts_at=now - timedelta(days=10),
        ends_at=now - timedelta(days=2),
    )
    db_session.add(campaign)
    db_session.flush()

    payment = AdvertisingPayment(
        campaign_id=campaign.id,
        provider_profile_id=profile.id,
        order_reference="ORD-EXP-001",
        amount=Decimal("10000.00"),
        payment_status="completed",
    )
    db_session.add(payment)
    db_session.commit()

    results = populate_homestay_promotions_and_sponsorship(db_session, [h], now=now)
    assert len(results) == 1
    assert results[0].is_sponsored is False
    assert results[0].sponsored_campaign_id is None


# ==============================================================================
# 9. Listing-specific sponsored status verification
# ==============================================================================
def test_listing_specific_sponsored_status(db_session, seed_data):
    h1a = seed_data["homestay_1a"]
    h1b = seed_data["homestay_1b"]  # Same provider, but NO campaign linked to 1b
    ref1a = seed_data["ref_1a"]
    profile = seed_data["profile_1"]
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # Valid paid campaign strictly linked to homestay 1a via ref1a
    campaign = PromotionCampaign(
        provider_profile_id=profile.id,
        listing_reference_id=ref1a.id,
        campaign_name="High Season Kandy Boost",
        target_module="homestay",
        budget=Decimal("15000.00"),
        spent_amount=Decimal("15000.00"),
        currency="LKR",
        status="active",
        starts_at=now - timedelta(days=1),
        ends_at=now + timedelta(days=7),
    )
    db_session.add(campaign)
    db_session.flush()

    payment = AdvertisingPayment(
        campaign_id=campaign.id,
        provider_profile_id=profile.id,
        order_reference="ORD-ACTIVE-001",
        amount=Decimal("15000.00"),
        payment_status="completed",
    )
    db_session.add(payment)
    db_session.commit()

    results = populate_homestay_promotions_and_sponsorship(db_session, [h1a, h1b], now=now)
    res_map = {item.id: item for item in results}

    # Homestay 1a MUST be sponsored
    assert res_map[h1a.id].is_sponsored is True
    assert res_map[h1a.id].sponsored_campaign_id == campaign.id

    # Homestay 1b MUST NOT be sponsored (listing-specific isolation)
    assert res_map[h1b.id].is_sponsored is False
    assert res_map[h1b.id].sponsored_campaign_id is None


# ==============================================================================
# 10. Prevention of unauthorized booking event creation via public endpoint
# ==============================================================================
def test_prevention_of_unauthorized_booking_events(db_session, seed_data):
    profile = seed_data["profile_1"]
    now = datetime.now(timezone.utc)

    campaign = PromotionCampaign(
        provider_profile_id=profile.id,
        campaign_name="Public Event Test Campaign",
        target_module="homestay",
        budget=Decimal("5000.00"),
        starts_at=now,
        ends_at=now + timedelta(days=5),
        status="active",
    )
    db_session.add(campaign)
    db_session.commit()

    # Public client attempts to directly log a 'booking' conversion event
    event_payload = PromotionEventCreate(event_type="booking")
    mock_request = MagicMock()
    mock_request.client.host = "192.168.1.100"

    with pytest.raises(HTTPException) as exc_info:
        log_promotion_event(
            campaign_id=campaign.id,
            payload=event_payload,
            request=mock_request,
            db=db_session,
            current_user=None,
        )

    assert exc_info.value.status_code == 403
    assert "Fabricated booking conversions cannot be recorded directly" in exc_info.value.detail
