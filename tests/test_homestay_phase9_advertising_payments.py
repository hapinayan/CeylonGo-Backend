"""
CeylonGo - Phase 09 Advertising Payments & Campaign Completion Targeted Tests
=============================================================================
Validates:
1. Authorized advertising checkout initiation.
2. Unauthorized campaign payment rejection (and unapproved campaign rejection).
3. Server-side amount protection.
4. Invalid gateway signature rejection.
5. Successful verified advertising callback.
6. Duplicate callback prevention (idempotency).
7. Paid campaign activation eligibility (future-scheduled remains approved until start).
8. Expired campaign exclusion (reconciliation-flagged, not sponsored).
9. Provider-level fallback removal (strict listing-specific sponsorship only).
10. Booking conversion deduplication and attribution protection.
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.payhere import payhere_service
from app.core.promotion_pricing import record_campaign_booking_conversion
from app.models.homestay import Homestay
from app.models.provider_profile import ProviderProfile
from app.models.shared import (
    AdvertisingPayment,
    BookingReference,
    ListingReference,
    PromotionCampaign,
    PromotionEvent,
)
from app.models.user import Base, User
from app.routers.homestay import populate_homestay_promotions_and_sponsorship
from app.routers.homestay_campaigns import (
    admin_approve_campaign,
    get_campaign_payments,
    get_my_advertising_payments,
    initiate_campaign_payment,
)
from app.routers.payments import handle_advertising_payment_callback


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
    """Seed providers, admin, and homestay listings."""
    admin_user = User(
        full_name="CeylonGo Admin",
        email="admin@ceylongo.test",
        role="admin",
        is_active=True,
    )
    provider_user_1 = User(
        full_name="Host Silva",
        email="silva@host.test",
        role="provider",
        is_active=True,
    )
    provider_user_2 = User(
        full_name="Host Perera",
        email="perera@host.test",
        role="provider",
        is_active=True,
    )
    traveler_user = User(
        full_name="Traveler Kamal",
        email="kamal@traveler.test",
        role="traveler",
        is_active=True,
    )
    db_session.add_all([admin_user, provider_user_1, provider_user_2, traveler_user])
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
        name="Kandy Riverside Villa",
        location="Kandy",
        district="Kandy",
        price_per_night=Decimal("6000.00"),
        max_guests=4,
        is_active=True,
        approval_status="approved",
        provider_profile_id=profile_1.id,
        owner_id=provider_user_1.id,
    )
    homestay_1b = Homestay(
        name="Kandy Mountain Bungalow",
        location="Kandy",
        district="Kandy",
        price_per_night=Decimal("9000.00"),
        max_guests=6,
        is_active=True,
        approval_status="approved",
        provider_profile_id=profile_1.id,
        owner_id=provider_user_1.id,
    )
    db_session.add_all([homestay_1a, homestay_1b])
    db_session.flush()

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
    db_session.add_all([ref_1a, ref_1b])
    db_session.commit()

    return {
        "admin": admin_user,
        "user_1": provider_user_1,
        "user_2": provider_user_2,
        "traveler": traveler_user,
        "profile_1": profile_1,
        "profile_2": profile_2,
        "homestay_1a": homestay_1a,
        "homestay_1b": homestay_1b,
        "ref_1a": ref_1a,
        "ref_1b": ref_1b,
    }


# ==============================================================================
# 1. Authorized advertising checkout initiation
# ==============================================================================
def test_authorized_advertising_checkout_initiation(db_session, seed_data):
    profile = seed_data["profile_1"]
    ref = seed_data["ref_1a"]
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    campaign = PromotionCampaign(
        provider_profile_id=profile.id,
        listing_reference_id=ref.id,
        campaign_name="Peak Season Promo",
        target_module="homestay",
        budget=Decimal("12500.00"),
        spent_amount=Decimal("0.00"),
        currency="LKR",
        status="approved",  # Approved by admin
        starts_at=now,
        ends_at=now + timedelta(days=14),
    )
    db_session.add(campaign)
    db_session.commit()

    resp = initiate_campaign_payment(
        campaign_id=campaign.id,
        db=db_session,
        current_provider=profile,
    )

    assert resp.campaign_id == campaign.id
    assert resp.order_id.startswith("AD-CAMP-")
    assert resp.amount == Decimal("12500.00")
    assert resp.currency == "LKR"
    assert resp.merchant_id == payhere_service.merchant_id
    assert resp.hash_value is not None
    assert resp.status == "pending"

    # Verify pending AdvertisingPayment created in ledger
    ad_payment = (
        db_session.query(AdvertisingPayment)
        .filter(AdvertisingPayment.order_reference == resp.order_id)
        .first()
    )
    assert ad_payment is not None
    assert ad_payment.campaign_id == campaign.id
    assert ad_payment.amount == Decimal("12500.00")
    assert ad_payment.payment_status == "pending"


# ==============================================================================
# 2. Unauthorized campaign payment rejection
# ==============================================================================
def test_unauthorized_campaign_payment_rejection(db_session, seed_data):
    profile_1 = seed_data["profile_1"]
    profile_2 = seed_data["profile_2"]
    ref = seed_data["ref_1a"]
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    campaign = PromotionCampaign(
        provider_profile_id=profile_1.id,
        listing_reference_id=ref.id,
        campaign_name="Host 1 Campaign",
        target_module="homestay",
        budget=Decimal("5000.00"),
        currency="LKR",
        status="approved",
        starts_at=now,
        ends_at=now + timedelta(days=7),
    )
    db_session.add(campaign)
    db_session.commit()

    # Provider 2 attempts to initiate payment for Provider 1's campaign -> 403 Forbidden
    with pytest.raises(HTTPException) as exc_info:
        initiate_campaign_payment(
            campaign_id=campaign.id,
            db=db_session,
            current_provider=profile_2,
        )
    assert exc_info.value.status_code == 403
    assert "You can only initiate payment for your own campaigns" in exc_info.value.detail

    # Unapproved campaign (pending_approval) -> 400 Bad Request
    unapproved_campaign = PromotionCampaign(
        provider_profile_id=profile_1.id,
        listing_reference_id=ref.id,
        campaign_name="Unapproved Campaign",
        target_module="homestay",
        budget=Decimal("5000.00"),
        currency="LKR",
        status="pending_approval",
        starts_at=now,
        ends_at=now + timedelta(days=7),
    )
    db_session.add(unapproved_campaign)
    db_session.commit()

    with pytest.raises(HTTPException) as exc_info2:
        initiate_campaign_payment(
            campaign_id=unapproved_campaign.id,
            db=db_session,
            current_provider=profile_1,
        )
    assert exc_info2.value.status_code == 400
    assert "Campaign requires administrator approval" in exc_info2.value.detail


# ==============================================================================
# 3. Server-side amount protection
# ==============================================================================
def test_server_side_amount_protection(db_session, seed_data):
    profile = seed_data["profile_1"]
    ref = seed_data["ref_1a"]
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    campaign = PromotionCampaign(
        provider_profile_id=profile.id,
        listing_reference_id=ref.id,
        campaign_name="Fixed Fee Ad",
        target_module="homestay",
        budget=Decimal("8000.00"),
        currency="LKR",
        status="approved",
        starts_at=now,
        ends_at=now + timedelta(days=7),
    )
    db_session.add(campaign)
    db_session.commit()

    # Initiate payment - amount is determined strictly server-side from campaign.budget
    resp = initiate_campaign_payment(
        campaign_id=campaign.id,
        db=db_session,
        current_provider=profile,
    )
    assert resp.amount == Decimal("8000.00")

    # Verify calculated hash matches the server-side budget of 8000.00
    expected_hash = payhere_service.generate_checkout_hash(
        order_id=resp.order_id,
        amount=Decimal("8000.00"),
        currency="LKR",
    )
    assert resp.hash_value == expected_hash


# ==============================================================================
# 4. Invalid gateway signature rejection
# ==============================================================================
def test_invalid_gateway_signature_rejection():
    # Attempt verification with tampered signature
    is_valid = payhere_service.verify_ipn_signature(
        order_id="AD-CAMP-100-12345",
        payhere_amount="8000.00",
        payhere_currency="LKR",
        status_code="2",
        received_signature="TAMPERED_INVALID_HASH_VALUE",
    )
    assert is_valid is False


# ==============================================================================
# 5. Successful verified advertising callback
# ==============================================================================
def test_successful_verified_advertising_callback(db_session, seed_data):
    profile = seed_data["profile_1"]
    ref = seed_data["ref_1a"]
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    campaign = PromotionCampaign(
        provider_profile_id=profile.id,
        listing_reference_id=ref.id,
        campaign_name="Active Verified Campaign",
        target_module="homestay",
        budget=Decimal("15000.00"),
        spent_amount=Decimal("0.00"),
        currency="LKR",
        status="approved",
        starts_at=now - timedelta(hours=1),
        ends_at=now + timedelta(days=7),
    )
    db_session.add(campaign)
    db_session.flush()

    order_ref = f"AD-CAMP-{campaign.id}-1001"
    ad_payment = AdvertisingPayment(
        campaign_id=campaign.id,
        provider_profile_id=profile.id,
        order_reference=order_ref,
        amount=Decimal("15000.00"),
        currency="LKR",
        gateway_name="payhere",
        payment_status="pending",
    )
    db_session.add(ad_payment)
    db_session.commit()

    # Simulate PayHere callback
    res = handle_advertising_payment_callback(
        db=db_session,
        order_id=order_ref,
        payment_id="PAYHERE_GATEWAY_TX_999",
        payhere_amount="15000.00",
        payhere_currency="LKR",
        status_code="2",
        form_data={},
    )
    assert res["status"] == "success"

    db_session.refresh(ad_payment)
    db_session.refresh(campaign)

    assert ad_payment.payment_status == "completed"
    assert ad_payment.gateway_transaction_id == "PAYHERE_GATEWAY_TX_999"
    assert ad_payment.verified_at is not None
    assert campaign.status == "active"
    assert campaign.spent_amount == Decimal("15000.00")


# ==============================================================================
# 6. Duplicate callback prevention (Idempotency)
# ==============================================================================
def test_duplicate_callback_prevention(db_session, seed_data):
    profile = seed_data["profile_1"]
    ref = seed_data["ref_1a"]
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    campaign = PromotionCampaign(
        provider_profile_id=profile.id,
        listing_reference_id=ref.id,
        campaign_name="Idempotency Test Campaign",
        target_module="homestay",
        budget=Decimal("5000.00"),
        currency="LKR",
        status="approved",
        starts_at=now - timedelta(hours=1),
        ends_at=now + timedelta(days=5),
    )
    db_session.add(campaign)
    db_session.flush()

    order_ref = f"AD-CAMP-{campaign.id}-IDEMPOTENT"
    ad_payment = AdvertisingPayment(
        campaign_id=campaign.id,
        provider_profile_id=profile.id,
        order_reference=order_ref,
        amount=Decimal("5000.00"),
        currency="LKR",
        gateway_name="payhere",
        payment_status="pending",
    )
    db_session.add(ad_payment)
    db_session.commit()

    # First callback
    res1 = handle_advertising_payment_callback(
        db=db_session,
        order_id=order_ref,
        payment_id="TX_1001",
        payhere_amount="5000.00",
        payhere_currency="LKR",
        status_code="2",
        form_data={},
    )
    assert res1["status"] == "success"

    # Second callback (duplicate)
    res2 = handle_advertising_payment_callback(
        db=db_session,
        order_id=order_ref,
        payment_id="TX_1001",
        payhere_amount="5000.00",
        payhere_currency="LKR",
        status_code="2",
        form_data={},
    )
    assert res2["status"] == "success"
    assert "already processed" in res2["message"]

    # Verify exactly 1 payment record remains
    count = (
        db_session.query(AdvertisingPayment)
        .filter(AdvertisingPayment.order_reference == order_ref)
        .count()
    )
    assert count == 1


# ==============================================================================
# 7. Paid campaign activation eligibility (Future-scheduled campaigns)
# ==============================================================================
def test_future_scheduled_campaign_eligibility(db_session, seed_data):
    profile = seed_data["profile_1"]
    ref = seed_data["ref_1a"]
    h = seed_data["homestay_1a"]
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # Starts in 3 days (future schedule)
    campaign = PromotionCampaign(
        provider_profile_id=profile.id,
        listing_reference_id=ref.id,
        campaign_name="Next Month Early Bird",
        target_module="homestay",
        budget=Decimal("7000.00"),
        currency="LKR",
        status="approved",
        starts_at=now + timedelta(days=3),
        ends_at=now + timedelta(days=10),
    )
    db_session.add(campaign)
    db_session.flush()

    order_ref = f"AD-CAMP-{campaign.id}-FUTURE"
    ad_payment = AdvertisingPayment(
        campaign_id=campaign.id,
        provider_profile_id=profile.id,
        order_reference=order_ref,
        amount=Decimal("7000.00"),
        payment_status="completed",
        verified_at=now,
    )
    db_session.add(ad_payment)
    db_session.commit()

    # Even though paid, campaign must NOT appear as sponsored today (starts_at > now)
    results = populate_homestay_promotions_and_sponsorship(db_session, [h], now=now)
    assert results[0].is_sponsored is False

    # Once starts_at is reached (now + 4 days), it must appear as sponsored
    future_now = now + timedelta(days=4)
    campaign.status = "active"  # activated on schedule
    db_session.commit()

    results_future = populate_homestay_promotions_and_sponsorship(db_session, [h], now=future_now)
    assert results_future[0].is_sponsored is True
    assert results_future[0].sponsored_campaign_id == campaign.id


# ==============================================================================
# 8. Expired campaign exclusion
# ==============================================================================
def test_expired_campaign_reconciliation_handling(db_session, seed_data):
    profile = seed_data["profile_1"]
    ref = seed_data["ref_1a"]
    h = seed_data["homestay_1a"]
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # Campaign that expired yesterday
    campaign = PromotionCampaign(
        provider_profile_id=profile.id,
        listing_reference_id=ref.id,
        campaign_name="Expired Campaign",
        target_module="homestay",
        budget=Decimal("6000.00"),
        currency="LKR",
        status="approved",
        starts_at=now - timedelta(days=7),
        ends_at=now - timedelta(days=1),
    )
    db_session.add(campaign)
    db_session.flush()

    order_ref = f"AD-CAMP-{campaign.id}-EXPIRED"
    ad_payment = AdvertisingPayment(
        campaign_id=campaign.id,
        provider_profile_id=profile.id,
        order_reference=order_ref,
        amount=Decimal("6000.00"),
        payment_status="pending",
    )
    db_session.add(ad_payment)
    db_session.commit()

    # Late payment callback arrives for expired campaign
    res = handle_advertising_payment_callback(
        db=db_session,
        order_id=order_ref,
        payment_id="LATE_TX_123",
        payhere_amount="6000.00",
        payhere_currency="LKR",
        status_code="2",
        form_data={},
    )
    assert res["status"] == "success"

    db_session.refresh(campaign)
    db_session.refresh(ad_payment)

    # Payment is retained for reconciliation, but campaign is marked completed (never active)
    assert ad_payment.payment_status == "completed"
    assert campaign.status == "completed"

    # Discovery must exclude expired campaign from sponsorship
    results = populate_homestay_promotions_and_sponsorship(db_session, [h], now=now)
    assert results[0].is_sponsored is False


# ==============================================================================
# 9. Provider-level fallback removal (Strict listing-specific sponsorship)
# ==============================================================================
def test_provider_level_fallback_removal(db_session, seed_data):
    profile = seed_data["profile_1"]
    ref1a = seed_data["ref_1a"]
    h1a = seed_data["homestay_1a"]
    h1b = seed_data["homestay_1b"]  # Same provider, no campaign
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # 1. Historical provider-level campaign with listing_reference_id = None
    historical_campaign = PromotionCampaign(
        provider_profile_id=profile.id,
        listing_reference_id=None,  # No listing attached
        campaign_name="Legacy Provider Campaign",
        target_module="homestay",
        budget=Decimal("10000.00"),
        status="active",
        starts_at=now - timedelta(days=1),
        ends_at=now + timedelta(days=7),
    )
    db_session.add(historical_campaign)
    db_session.flush()

    hist_payment = AdvertisingPayment(
        campaign_id=historical_campaign.id,
        provider_profile_id=profile.id,
        order_reference="AD-HIST-001",
        amount=Decimal("10000.00"),
        payment_status="completed",
        verified_at=now,
    )
    db_session.add(hist_payment)

    # 2. Listing-specific campaign targeting Homestay 1a only
    specific_campaign = PromotionCampaign(
        provider_profile_id=profile.id,
        listing_reference_id=ref1a.id,
        campaign_name="Specific 1a Boost",
        target_module="homestay",
        budget=Decimal("5000.00"),
        status="active",
        starts_at=now - timedelta(days=1),
        ends_at=now + timedelta(days=7),
    )
    db_session.add(specific_campaign)
    db_session.flush()

    spec_payment = AdvertisingPayment(
        campaign_id=specific_campaign.id,
        provider_profile_id=profile.id,
        order_reference="AD-SPEC-001",
        amount=Decimal("5000.00"),
        payment_status="completed",
        verified_at=now,
    )
    db_session.add(spec_payment)
    db_session.commit()

    results = populate_homestay_promotions_and_sponsorship(db_session, [h1a, h1b], now=now)
    res_map = {item.id: item for item in results}

    # Homestay 1a is sponsored via specific_campaign
    assert res_map[h1a.id].is_sponsored is True
    assert res_map[h1a.id].sponsored_campaign_id == specific_campaign.id

    # Homestay 1b MUST NOT be sponsored (provider-level fallback strictly removed)
    assert res_map[h1b.id].is_sponsored is False
    assert res_map[h1b.id].sponsored_campaign_id is None


# ==============================================================================
# 10. Booking conversion deduplication and attribution protection
# ==============================================================================
def test_booking_conversion_deduplication_and_attribution(db_session, seed_data):
    profile = seed_data["profile_1"]
    ref = seed_data["ref_1a"]
    h = seed_data["homestay_1a"]
    traveler = seed_data["traveler"]
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    campaign = PromotionCampaign(
        provider_profile_id=profile.id,
        listing_reference_id=ref.id,
        campaign_name="Conversion Campaign",
        target_module="homestay",
        budget=Decimal("10000.00"),
        status="active",
        starts_at=now - timedelta(days=1),
        ends_at=now + timedelta(days=7),
    )
    db_session.add(campaign)
    db_session.flush()

    ad_payment = AdvertisingPayment(
        campaign_id=campaign.id,
        provider_profile_id=profile.id,
        order_reference="AD-CONV-001",
        amount=Decimal("10000.00"),
        payment_status="completed",
        verified_at=now,
    )
    db_session.add(ad_payment)

    # Create dummy booking reference
    booking_ref = BookingReference(
        booking_reference="BK-REF-CONV-001",
        module_type="homestay",
        target_booking_id=1,
        user_id=traveler.id,
        provider_profile_id=profile.id,
        total_price=Decimal("12000.00"),
        booking_status="confirmed",
        payment_status="paid",
    )
    db_session.add(booking_ref)
    db_session.commit()

    # 1. Untrusted organic booking without campaign context is rejected
    untrusted_res = record_campaign_booking_conversion(
        db=db_session,
        homestay_id=h.id,
        user_id=traveler.id,
        booking_reference_id=None,
        campaign_id=None,
    )
    assert untrusted_res is False

    # 2. Trusted attribution records genuine conversion
    trusted_res = record_campaign_booking_conversion(
        db=db_session,
        homestay_id=h.id,
        user_id=traveler.id,
        booking_reference_id=booking_ref.id,
        campaign_id=campaign.id,
    )
    assert trusted_res is True
    db_session.commit()

    # Verify event record in database
    event_rec = (
        db_session.query(PromotionEvent)
        .filter(
            PromotionEvent.campaign_id == campaign.id,
            PromotionEvent.booking_reference_id == booking_ref.id,
            PromotionEvent.event_type == "booking",
        )
        .first()
    )
    assert event_rec is not None
    assert event_rec.booking_reference_id == booking_ref.id

    # 3. Duplicate conversion call is blocked by deduplication
    duplicate_res = record_campaign_booking_conversion(
        db=db_session,
        homestay_id=h.id,
        user_id=traveler.id,
        booking_reference_id=booking_ref.id,
        campaign_id=campaign.id,
    )
    assert duplicate_res is False
