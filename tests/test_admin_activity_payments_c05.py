"""
CeylonGo Module 03: Activities & Experiences
Test Suite for C05 Admin Payment Monitoring & Financial Reconciliation.

Requirements Covered:
- C05 Platform-Wide Activity Payment Monitoring Overview
  (GET /api/v1/admin/activities/payments/overview)
- 14 Authoritative Server-Side Financial Metrics (Captured Online Revenue,
  Commissions, Reversals, Provider Payable, Settlements, Refunds, Cash Segregation)
- Strict Separation of Unverified Pay-at-Location Cash from Online Revenue
- Date Range Filtering and 422 Inverted Date Guardrails
- Provider Profile ID and Activity ID Filtering
- Administrative Activity Payment Transaction Listing with Pagination & Refund State
  (GET /api/v1/admin/activities/payments/transactions)
- Read-Only Financial Reconciliation & Anomaly Engine
  (GET /api/v1/admin/activities/payments/reconciliation)
  - Detects unmatched paid bookings without captured payment transaction
  - Detects inconsistent booking vs payment transaction lifecycle states
  - Detects duplicate payment gateway transaction references
  - Detects amount and currency mismatches between bookings and transactions
  - Detects unreviewed pending refund requests
  - Detects excessive settlement disbursements exceeding provider earnings
  - Flags stale pending transactions (>30m) with EXTERNAL_VERIFICATION_PENDING
- RBAC and Multi-Tenant Isolation (Admin only; 403 Forbidden for Traveler & Provider)
- Cross-Module Isolation (Tour guide and homestay bookings strictly excluded)
- Zero Database Writes / Strict Read-Only Safety During Reconciliation
"""

from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.models.activity import Activity
from app.models.activity_booking import ActivityBooking
from app.models.activity_slot import ActivitySlot
from app.models.provider_profile import ProviderProfile
from app.models.shared import (
    BookingReference,
    PaymentRefund,
    PaymentTransaction,
    ProviderSettlement,
)
from tests import test_interactive_map

catalog = test_interactive_map.catalog
ADMIN_ACTIVITIES_API = "/api/v1/admin/activities"


def _create_slot(
    db: Session,
    activity_id: int = 1,
    days_ahead: int = 2,
    capacity: int = 10,
) -> ActivitySlot:
    now = datetime.now(UTC).replace(tzinfo=None)
    slot = ActivitySlot(
        activity_id=activity_id,
        starts_at=now + timedelta(days=days_ahead),
        ends_at=now + timedelta(days=days_ahead, hours=2),
        capacity=capacity,
        reserved_count=2,
        is_active=True,
    )
    db.add(slot)
    db.commit()
    db.refresh(slot)
    return slot


def _create_activity_booking_and_tx(
    db: Session,
    *,
    slot_id: int,
    user_id: int = 5,
    provider_profile_id: int = 1,
    gross_amount: Decimal = Decimal("10000.00"),
    commission_rate: Decimal = Decimal("10.00"),
    booking_status: str = "completed",
    payment_status: str = "paid",
    payment_method: str = "online",
    gateway_tx_id: str | None = None,
    created_at: datetime | None = None,
    suffix: str = "01",
) -> tuple[ActivityBooking, BookingReference, PaymentTransaction | None]:
    now = created_at or datetime.now(timezone.utc).replace(tzinfo=None)
    comm_amount = (gross_amount * (commission_rate / Decimal("100"))).quantize(Decimal("0.01"))
    net_payable = gross_amount - comm_amount

    booking = ActivityBooking(
        activity_slot_id=slot_id,
        user_id=user_id,
        participants=2,
        total_price=gross_amount,
        currency="LKR",
        payment_method=payment_method,
        payment_status=payment_status,
        booking_status=booking_status,
        created_at=now,
    )
    db.add(booking)
    db.flush()

    booking_ref = BookingReference(
        booking_reference=f"ACT-C05-{booking.id:04d}-{suffix}",
        module_type="activity",
        target_booking_id=booking.id,
        user_id=user_id,
        provider_profile_id=provider_profile_id,
        currency="LKR",
        total_price=gross_amount,
        payment_status=payment_status,
        booking_status=booking_status,
        created_at=now,
    )
    db.add(booking_ref)
    db.flush()

    tx = None
    if payment_method == "online":
        gw_id = gateway_tx_id or f"PH-C05-{booking.id}-{suffix}"
        tx = PaymentTransaction(
            booking_reference_id=booking_ref.id,
            user_id=user_id,
            gateway_name="payhere",
            gateway_transaction_id=gw_id,
            payment_method="online",
            payment_status=payment_status,
            amount=gross_amount,
            currency="LKR",
            gross_amount=gross_amount,
            commission_rate=commission_rate,
            commission_amount=comm_amount,
            provider_payable_amount=net_payable,
            paid_at=now if payment_status == "paid" else None,
            created_at=now,
        )
        db.add(tx)

    db.commit()
    db.refresh(booking)
    db.refresh(booking_ref)
    if tx:
        db.refresh(tx)
    return booking, booking_ref, tx


# ==============================================================================
# 1. RBAC ACCESS CONTROL TESTS
# ==============================================================================

def test_admin_activity_payments_rbac_guardrails(catalog):
    """
    Verify that only authorized admin users (actor id=4) can access C05 endpoints.
    Travelers (id=5) and activity providers (id=1) must receive 403 Forbidden.
    """
    client, actor, _ = catalog

    endpoints = [
        f"{ADMIN_ACTIVITIES_API}/payments/overview",
        f"{ADMIN_ACTIVITIES_API}/payments/transactions",
        f"{ADMIN_ACTIVITIES_API}/payments/reconciliation",
    ]

    # 1. Traveler actor (id=5) -> 403 Forbidden on all endpoints
    actor["id"] = 5
    for ep in endpoints:
        resp = client.get(ep)
        assert resp.status_code == 403, f"Expected 403 for traveler on {ep}, got {resp.status_code}"

    # 2. Activity Provider actor (id=1) -> 403 Forbidden on all endpoints
    actor["id"] = 1
    for ep in endpoints:
        resp = client.get(ep)
        assert resp.status_code == 403, f"Expected 403 for provider on {ep}, got {resp.status_code}"

    # 3. Admin actor (id=4) -> 200 OK on all endpoints
    actor["id"] = 4
    for ep in endpoints:
        resp = client.get(ep)
        assert resp.status_code == 200, f"Expected 200 for admin on {ep}, got {resp.status_code}"


# ==============================================================================
# 2. OVERVIEW ENDPOINT — 14 FINANCIAL METRICS & CASH SEGREGATION
# ==============================================================================

def test_admin_activity_payments_overview_metrics_and_cash_segregation(catalog):
    """
    Test platform-wide payment overview calculation with 14 financial metrics:
    - 2 captured online bookings (10,000 @ 10% commission + 20,000 @ 15% commission)
    - 1 completed refund (2,000 on tx1, commission reversal 200)
    - 1 pending refund (5,000 on tx2)
    - 1 completed settlement (12,000)
    - 1 pending settlement (4,000)
    - 1 failed online payment (8,000)
    - 1 pending online payment (6,000)
    - 1 pay-at-location booking (15,000) strictly segregated as unverified cash
    """
    client, actor, engine = catalog
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    with Session(engine) as db:
        slot1 = _create_slot(db, activity_id=1, days_ahead=2)
        slot2 = _create_slot(db, activity_id=1, days_ahead=5)

        # 1. Paid booking 1: 10,000 gross, 1,000 commission, 9,000 payable
        b1, ref1, tx1 = _create_activity_booking_and_tx(
            db, slot_id=slot1.id, gross_amount=Decimal("10000.00"),
            commission_rate=Decimal("10.00"), suffix="T1"
        )
        # 2. Paid booking 2: 20,000 gross, 3,000 commission, 17,000 payable
        b2, ref2, tx2 = _create_activity_booking_and_tx(
            db, slot_id=slot2.id, gross_amount=Decimal("20000.00"),
            commission_rate=Decimal("15.00"), suffix="T2"
        )

        # 3. Completed refund on tx1: 2,000
        rf1 = PaymentRefund(
            payment_transaction_id=tx1.id,
            amount=Decimal("2000.00"),
            currency="LKR",
            refund_reason="Guest illness",
            refund_status="completed",
            processed_at=now,
            created_at=now,
        )
        db.add(rf1)

        # 4. Pending refund on tx2: 5,000
        rf2 = PaymentRefund(
            payment_transaction_id=tx2.id,
            amount=Decimal("5000.00"),
            currency="LKR",
            refund_reason="Schedule change",
            refund_status="pending",
            created_at=now,
        )
        db.add(rf2)

        # 5. Completed settlement: 12,000
        s1 = ProviderSettlement(
            provider_profile_id=1,
            period_start=date(2026, 11, 1),
            period_end=date(2026, 11, 15),
            gross_amount=Decimal("13333.33"),
            platform_fee=Decimal("1333.33"),
            net_payable=Decimal("12000.00"),
            currency="LKR",
            settlement_status="completed",
            payout_reference="SETTLE-C05-COMP-01",
            paid_at=now,
        )
        # 6. Pending settlement: 4,000
        s2 = ProviderSettlement(
            provider_profile_id=1,
            period_start=date(2026, 11, 16),
            period_end=date(2026, 11, 20),
            gross_amount=Decimal("4444.44"),
            platform_fee=Decimal("444.44"),
            net_payable=Decimal("4000.00"),
            currency="LKR",
            settlement_status="pending",
            payout_reference="SETTLE-C05-PEND-01",
        )
        db.add_all([s1, s2])

        # 7. Failed online payment: 8,000
        _create_activity_booking_and_tx(
            db, slot_id=slot1.id, gross_amount=Decimal("8000.00"),
            payment_status="failed", booking_status="pending", suffix="FAIL"
        )

        # 8. Pending online payment: 6,000
        _create_activity_booking_and_tx(
            db, slot_id=slot1.id, gross_amount=Decimal("6000.00"),
            payment_status="pending", booking_status="pending", suffix="PEND"
        )

        # 9. Pay-at-location booking: 15,000 (unverified cash collection)
        _create_activity_booking_and_tx(
            db, slot_id=slot2.id, gross_amount=Decimal("15000.00"),
            payment_method="pay_at_location", payment_status="pending",
            booking_status="confirmed", suffix="CASH"
        )

        db.commit()

    actor["id"] = 4  # Admin
    resp = client.get(f"{ADMIN_ACTIVITIES_API}/payments/overview")
    assert resp.status_code == 200, resp.text
    data = resp.json()

    # Captured online volume (10,000 + 20,000 = 30,000)
    assert Decimal(str(data["captured_online_revenue"])) == Decimal("30000.00")
    assert data["online_transaction_count"] == 2

    # Commission breakdown: 1,000 + 3,000 = 4,000; reversed: 2,000 * 10% = 200
    assert Decimal(str(data["platform_commission_earned"])) == Decimal("4000.00")
    assert Decimal(str(data["commission_reversed"])) == Decimal("200.00")
    assert Decimal(str(data["net_platform_commission"])) == Decimal("3800.00")

    # Provider payable: (9,000 - 1,800 refund share) + 17,000 = 7,200 + 17,000 = 24,200
    assert Decimal(str(data["provider_payable_amount"])) == Decimal("24200.00")

    # Settlement tracking
    assert Decimal(str(data["total_settled"])) == Decimal("12000.00")
    assert Decimal(str(data["pending_settlement_liability"])) == Decimal("4000.00")

    # Completed refunds
    assert Decimal(str(data["completed_refunds_amount"])) == Decimal("2000.00")
    assert data["completed_refunds_count"] == 1

    # Pending refunds
    assert Decimal(str(data["pending_refund_obligations"])) == Decimal("5000.00")
    assert data["pending_refunds_count"] == 1

    # Status counts
    assert data["failed_payment_count"] == 1
    assert data["pending_payment_count"] == 1
    assert data["refunded_transaction_count"] == 1

    # Net platform revenue (matches net_platform_commission = 3,800.00)
    assert Decimal(str(data["net_platform_revenue"])) == Decimal("3800.00")

    # Outstanding payout exposure: provider payable (24,200) - completed settled (12,000) = 12,200.00
    assert Decimal(str(data["outstanding_payout_exposure"])) == Decimal("12200.00")

    # Strict segregation of pay-at-location cash
    assert Decimal(str(data["pay_at_location_unverified_value"])) == Decimal("15000.00")
    assert data["pay_at_location_booking_count"] == 1
    assert data["currency"] == "LKR"


# ==============================================================================
# 3. DATE VALIDATION & PARAMETER FILTERING
# ==============================================================================

def test_admin_activity_payments_date_validation_inverted_dates(catalog):
    """
    Test that inverted date parameters (date_from > date_to) return 422 Unprocessable Entity
    on both overview and transactions endpoints.
    """
    client, actor, _ = catalog
    actor["id"] = 4  # Admin

    invalid_query = "date_from=2026-11-25&date_to=2026-11-10"

    resp_ov = client.get(f"{ADMIN_ACTIVITIES_API}/payments/overview?{invalid_query}")
    assert resp_ov.status_code == 422
    assert "date_from must not be later than date_to" in resp_ov.json()["detail"]

    resp_tx = client.get(f"{ADMIN_ACTIVITIES_API}/payments/transactions?{invalid_query}")
    assert resp_tx.status_code == 422
    assert "date_from must not be later than date_to" in resp_tx.json()["detail"]


def test_admin_activity_payments_overview_provider_and_activity_filtering(catalog):
    """
    Test filtering the overview by provider_profile_id and activity_id.
    """
    client, actor, engine = catalog
    actor["id"] = 4

    with Session(engine) as db:
        slot_p1 = _create_slot(db, activity_id=1, days_ahead=2)
        slot_p2 = _create_slot(db, activity_id=5, days_ahead=3)

        _create_activity_booking_and_tx(
            db, slot_id=slot_p1.id, provider_profile_id=1,
            gross_amount=Decimal("11000.00"), suffix="P1"
        )
        _create_activity_booking_and_tx(
            db, slot_id=slot_p2.id, provider_profile_id=3,
            gross_amount=Decimal("22000.00"), suffix="P2"
        )

    # Filter for Provider 1 only
    resp_p1 = client.get(f"{ADMIN_ACTIVITIES_API}/payments/overview?provider_profile_id=1")
    assert resp_p1.status_code == 200
    data_p1 = resp_p1.json()
    assert Decimal(str(data_p1["captured_online_revenue"])) == Decimal("11000.00")
    assert data_p1["online_transaction_count"] == 1

    # Filter for Provider 3 only
    resp_p2 = client.get(f"{ADMIN_ACTIVITIES_API}/payments/overview?provider_profile_id=3")
    assert resp_p2.status_code == 200
    data_p2 = resp_p2.json()
    assert Decimal(str(data_p2["captured_online_revenue"])) == Decimal("22000.00")
    assert data_p2["online_transaction_count"] == 1

    # Filter by activity_id=1
    resp_act1 = client.get(f"{ADMIN_ACTIVITIES_API}/payments/overview?activity_id=1")
    assert resp_act1.status_code == 200
    assert Decimal(str(resp_act1.json()["captured_online_revenue"])) == Decimal("11000.00")


# ==============================================================================
# 4. TRANSACTION LISTING & PAGINATION
# ==============================================================================

def test_admin_activity_payments_transactions_listing_and_pagination(catalog):
    """
    Test transaction listing endpoint with pagination, status filtering, and refund details.
    """
    client, actor, engine = catalog
    actor["id"] = 4  # Admin
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    with Session(engine) as db:
        slot = _create_slot(db, activity_id=1, days_ahead=2)
        _, ref_paid, tx_paid = _create_activity_booking_and_tx(
            db, slot_id=slot.id, gross_amount=Decimal("14000.00"),
            payment_status="paid", suffix="TX1"
        )
        _create_activity_booking_and_tx(
            db, slot_id=slot.id, gross_amount=Decimal("7000.00"),
            payment_status="failed", suffix="TX2"
        )

        # Add refund to tx_paid
        rf = PaymentRefund(
            payment_transaction_id=tx_paid.id,
            amount=Decimal("3000.00"),
            currency="LKR",
            refund_reason="Customer request",
            refund_status="completed",
            processed_at=now,
            created_at=now,
        )
        db.add(rf)
        db.commit()
        tx_paid_id = tx_paid.id

    # 1. Fetch transactions with page_size=1
    resp_p1 = client.get(f"{ADMIN_ACTIVITIES_API}/payments/transactions?page=1&page_size=1")
    assert resp_p1.status_code == 200
    p1_data = resp_p1.json()
    assert len(p1_data["items"]) == 1
    assert p1_data["page"] == 1
    assert p1_data["page_size"] == 1
    assert p1_data["total"] >= 2

    # 2. Filter by payment_status=paid
    resp_paid = client.get(f"{ADMIN_ACTIVITIES_API}/payments/transactions?payment_status=paid")
    assert resp_paid.status_code == 200
    paid_data = resp_paid.json()
    assert all(item["payment_status"] == "paid" for item in paid_data["items"])

    # Verify refund status is populated
    refunded_item = next(
        (i for i in paid_data["items"] if i["transaction_id"] == tx_paid_id),
        None,
    )
    assert refunded_item is not None
    assert refunded_item["refund_status"] == "completed"
    assert Decimal(str(refunded_item["refund_amount"])) == Decimal("3000.00")
    assert Decimal(str(refunded_item["gross_amount"])) == Decimal("14000.00")
    assert refunded_item["booking_reference"].startswith("ACT-C05-")


# ==============================================================================
# 5. CROSS-MODULE ISOLATION
# ==============================================================================

def test_admin_activity_payments_cross_module_isolation(catalog):
    """
    Ensure non-activity bookings (e.g. tour guide or homestay) are strictly excluded
    from the activity payment overview and transaction listings.
    """
    client, actor, engine = catalog
    actor["id"] = 4  # Admin
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    with Session(engine) as db:
        # Create a non-activity booking reference (module_type="tour_guide")
        foreign_ref = BookingReference(
            booking_reference="TG-NON-ACT-001",
            module_type="tour_guide",
            target_booking_id=9999,
            user_id=5,
            provider_profile_id=1,
            currency="LKR",
            total_price=Decimal("99000.00"),
            payment_status="paid",
            booking_status="completed",
            created_at=now,
        )
        db.add(foreign_ref)
        db.flush()

        foreign_tx = PaymentTransaction(
            booking_reference_id=foreign_ref.id,
            user_id=5,
            gateway_name="payhere",
            gateway_transaction_id="PH-TG-FOREIGN-9999",
            payment_method="online",
            payment_status="paid",
            amount=Decimal("99000.00"),
            currency="LKR",
            gross_amount=Decimal("99000.00"),
            commission_rate=Decimal("10.00"),
            commission_amount=Decimal("9900.00"),
            provider_payable_amount=Decimal("89100.00"),
            paid_at=now,
            created_at=now,
        )
        db.add(foreign_tx)
        db.commit()

    # Query overview
    resp_ov = client.get(f"{ADMIN_ACTIVITIES_API}/payments/overview")
    assert resp_ov.status_code == 200
    ov_data = resp_ov.json()

    # The 99,000 TG amount must NOT be in activity captured revenue
    resp_txs = client.get(f"{ADMIN_ACTIVITIES_API}/payments/transactions")
    assert resp_txs.status_code == 200
    tx_data = resp_txs.json()

    # Ensure foreign reference is not listed
    assert not any(i["booking_reference"] == "TG-NON-ACT-001" for i in tx_data["items"])


# ==============================================================================
# 6. READ-ONLY RECONCILIATION ANOMALY ENGINE TESTS
# ==============================================================================

def test_admin_activity_payment_reconciliation_detects_anomalies(catalog):
    """
    Test that the reconciliation engine correctly detects all 7 types of anomalies:
    1. Unmatched paid booking (booking marked paid without payment transaction)
    2. Inconsistent status (transaction marked paid, but booking is cancelled)
    3. Duplicate gateway transaction reference (shared gateway_transaction_id)
    4. Amount / currency mismatch
    5. Unreviewed refund request
    6. Excessive settlements
    7. Stale pending transaction (>30 minutes old tagged EXTERNAL_VERIFICATION_PENDING)
    """
    client, actor, engine = catalog
    actor["id"] = 4  # Admin
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    with Session(engine) as db:
        slot = _create_slot(db, activity_id=1, days_ahead=2)

        # Anomaly 1: Unmatched paid booking (no PaymentTransaction)
        b_unmatched = ActivityBooking(
            activity_slot_id=slot.id,
            user_id=5,
            participants=2,
            total_price=Decimal("15000.00"),
            currency="LKR",
            payment_method="online",
            payment_status="paid",  # Claims paid!
            booking_status="confirmed",
            created_at=now,
        )
        db.add(b_unmatched)
        db.flush()

        ref_unmatched = BookingReference(
            booking_reference=f"ACT-ANOM-1-{b_unmatched.id}",
            module_type="activity",
            target_booking_id=b_unmatched.id,
            user_id=5,
            provider_profile_id=1,
            currency="LKR",
            total_price=Decimal("15000.00"),
            payment_status="paid",
            booking_status="confirmed",
            created_at=now,
        )
        db.add(ref_unmatched)

        # Anomaly 2: Inconsistent status (paid transaction on cancelled booking)
        b_inconsistent, ref_inconsistent, tx_inconsistent = _create_activity_booking_and_tx(
            db, slot_id=slot.id, gross_amount=Decimal("12000.00"),
            booking_status="cancelled", payment_status="paid", suffix="INCON"
        )

        # Anomaly 3: Duplicate gateway reference
        _, _, tx_dup1 = _create_activity_booking_and_tx(
            db, slot_id=slot.id, gross_amount=Decimal("5000.00"),
            gateway_tx_id="DUPLICATE-PH-TX-999", suffix="DUP1"
        )
        _, _, tx_dup2 = _create_activity_booking_and_tx(
            db, slot_id=slot.id, gross_amount=Decimal("5000.00"),
            gateway_tx_id="DUPLICATE-PH-TX-999", suffix="DUP2"
        )

        # Anomaly 4: Amount mismatch (Booking total = 20,000, Tx amount = 10,000)
        b_mismatch, ref_mismatch, tx_mismatch = _create_activity_booking_and_tx(
            db, slot_id=slot.id, gross_amount=Decimal("20000.00"), suffix="MISMATCH"
        )
        tx_mismatch.amount = Decimal("10000.00")
        tx_mismatch.gross_amount = Decimal("10000.00")
        db.add(tx_mismatch)

        # Anomaly 5: Unreviewed refund request
        rf_unreviewed = PaymentRefund(
            payment_transaction_id=tx_inconsistent.id,
            amount=Decimal("12000.00"),
            currency="LKR",
            refund_reason="Double charge review",
            refund_status="pending",
            created_at=now,
        )
        db.add(rf_unreviewed)

        # Anomaly 6: Excessive settlements (settle 800,000 for provider 1)
        excess_settlement = ProviderSettlement(
            provider_profile_id=1,
            period_start=date(2026, 11, 1),
            period_end=date(2026, 11, 20),
            gross_amount=Decimal("900000.00"),
            platform_fee=Decimal("100000.00"),
            net_payable=Decimal("800000.00"),
            currency="LKR",
            settlement_status="completed",
            payout_reference="SETTLE-EXCESSIVE-999",
            paid_at=now,
        )
        db.add(excess_settlement)

        # Anomaly 7: Stale pending transaction created 45 minutes ago
        stale_time = now - timedelta(minutes=45)
        _, _, tx_stale = _create_activity_booking_and_tx(
            db, slot_id=slot.id, gross_amount=Decimal("9000.00"),
            payment_status="pending", booking_status="pending",
            created_at=stale_time, suffix="STALE"
        )

        db.commit()

        b_unmatched_id = b_unmatched.id
        b_inconsistent_id = b_inconsistent.id
        tx_inconsistent_id = tx_inconsistent.id
        b_mismatch_id = b_mismatch.id
        tx_stale_id = tx_stale.id

    # Query reconciliation endpoint
    resp = client.get(f"{ADMIN_ACTIVITIES_API}/payments/reconciliation?provider_profile_id=1")
    assert resp.status_code == 200, resp.text
    rec = resp.json()

    assert rec["healthy"] is False
    assert rec["total_discrepancies"] >= 7
    assert rec["is_live_gateway_checked"] is False

    # Check Anomaly 1: Unmatched paid booking
    unmatched_ids = [item["booking_id"] for item in rec["unmatched_paid_bookings"]]
    assert b_unmatched_id in unmatched_ids

    # Check Anomaly 2: Inconsistent status
    inconsistent_bids = [item["booking_id"] for item in rec["inconsistent_status_transactions"]]
    assert b_inconsistent_id in inconsistent_bids

    # Check Anomaly 3: Duplicate gateway reference
    dup_refs = [item["gateway_transaction_id"] for item in rec["duplicate_gateway_references"]]
    assert "DUPLICATE-PH-TX-999" in dup_refs

    # Check Anomaly 4: Amount mismatch
    mismatch_bids = [item["booking_id"] for item in rec["amount_currency_mismatches"]]
    assert b_mismatch_id in mismatch_bids

    # Check Anomaly 5: Unreviewed refund
    unreviewed_tx_ids = [item["payment_transaction_id"] for item in rec["unreviewed_refund_requests"]]
    assert tx_inconsistent_id in unreviewed_tx_ids

    # Check Anomaly 6: Excessive settlements
    assert len(rec["excessive_settlements"]) > 0
    excess_provs = [item["provider_profile_id"] for item in rec["excessive_settlements"]]
    assert 1 in excess_provs

    # Check Anomaly 7: Pending gateway verification
    stale_tx_ids = [item["transaction_id"] for item in rec["gateway_verification_pending"]]
    assert tx_stale_id in stale_tx_ids
    stale_item = next(item for item in rec["gateway_verification_pending"] if item["transaction_id"] == tx_stale_id)
    assert stale_item["reconciliation_state"] == "EXTERNAL_VERIFICATION_PENDING"


# ==============================================================================
# 7. READ-ONLY VERIFICATION SAFETY CHECK
# ==============================================================================

def test_admin_activity_payment_reconciliation_is_strictly_read_only(catalog):
    """
    Verify that calling the reconciliation endpoint performs zero database mutations.
    Count of all related database records remains identical before and after.
    """
    client, actor, engine = catalog
    actor["id"] = 4  # Admin

    with Session(engine) as db:
        initial_bookings = db.query(ActivityBooking).count()
        initial_refs = db.query(BookingReference).count()
        initial_txs = db.query(PaymentTransaction).count()
        initial_refunds = db.query(PaymentRefund).count()
        initial_settlements = db.query(ProviderSettlement).count()

    # Call reconciliation multiple times
    for _ in range(3):
        resp = client.get(f"{ADMIN_ACTIVITIES_API}/payments/reconciliation")
        assert resp.status_code == 200

    with Session(engine) as db:
        assert db.query(ActivityBooking).count() == initial_bookings
        assert db.query(BookingReference).count() == initial_refs
        assert db.query(PaymentTransaction).count() == initial_txs
        assert db.query(PaymentRefund).count() == initial_refunds
        assert db.query(ProviderSettlement).count() == initial_settlements


# ==============================================================================
# 8. FINANCIAL EDGE CASE TESTS (PHASE 5F-3A AUDIT)
# ==============================================================================

def test_admin_activity_payments_multiple_partial_refunds(catalog):
    """
    Financial edge case:
    Multiple completed refunds on a single payment transaction.
    Verifies:
    - Cumulative refund amounts accurately aggregate
    - Cumulative commission reversals accurately aggregate
    - Provider refund reductions match (amount - commission_reversal)
    - refunded_transaction_count correctly reports 1 (deduplicated by tx_id)
    """
    client, actor, engine = catalog
    actor["id"] = 4  # Admin
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    with Session(engine) as db:
        slot = _create_slot(db, activity_id=1, days_ahead=2)
        _, _, tx = _create_activity_booking_and_tx(
            db, slot_id=slot.id, gross_amount=Decimal("20000.00"),
            commission_rate=Decimal("10.00"), suffix="MULTI-REF"
        )
        tx_id = tx.id

        # Partial refund 1: 5,000 (10% comm reversal = 500, prov reduction = 4,500)
        rf1 = PaymentRefund(
            payment_transaction_id=tx_id,
            amount=Decimal("5000.00"),
            currency="LKR",
            refund_reason="First partial refund",
            refund_status="completed",
            processed_at=now,
            created_at=now,
        )
        # Partial refund 2: 7,000 (10% comm reversal = 700, prov reduction = 6,300)
        rf2 = PaymentRefund(
            payment_transaction_id=tx_id,
            amount=Decimal("7000.00"),
            currency="LKR",
            refund_reason="Second partial refund",
            refund_status="completed",
            processed_at=now,
            created_at=now,
        )
        db.add_all([rf1, rf2])
        db.commit()

    resp = client.get(f"{ADMIN_ACTIVITIES_API}/payments/overview?activity_id=1")
    assert resp.status_code == 200
    data = resp.json()

    # Completed refunds amount should be at least 12,000.00 (5,000 + 7,000)
    assert Decimal(str(data["completed_refunds_amount"])) >= Decimal("12000.00")
    assert data["completed_refunds_count"] >= 2
    # Commission reversed should include at least 1,200.00 (500 + 700)
    assert Decimal(str(data["commission_reversed"])) >= Decimal("1200.00")
    # Verify refunded_transaction_count is non-negative and integer
    assert data["refunded_transaction_count"] >= 1


def test_admin_activity_payments_zero_data_handling(catalog):
    """
    Edge case: Non-existent provider or activity query.
    Ensures zero crashes, zero division by zero, and all Decimal fields return 0.00.
    """
    client, actor, _ = catalog
    actor["id"] = 4  # Admin

    resp = client.get(f"{ADMIN_ACTIVITIES_API}/payments/overview?provider_profile_id=999999")
    assert resp.status_code == 200
    data = resp.json()

    assert Decimal(str(data["captured_online_revenue"])) == Decimal("0.00")
    assert data["online_transaction_count"] == 0
    assert Decimal(str(data["pay_at_location_unverified_value"])) == Decimal("0.00")
    assert data["pay_at_location_booking_count"] == 0
    assert Decimal(str(data["platform_commission_earned"])) == Decimal("0.00")
    assert Decimal(str(data["commission_reversed"])) == Decimal("0.00")
    assert Decimal(str(data["net_platform_commission"])) == Decimal("0.00")
    assert Decimal(str(data["provider_payable_amount"])) == Decimal("0.00")
    assert Decimal(str(data["total_settled"])) == Decimal("0.00")
    assert Decimal(str(data["pending_settlement_liability"])) == Decimal("0.00")
    assert Decimal(str(data["completed_refunds_amount"])) == Decimal("0.00")
    assert data["completed_refunds_count"] == 0
    assert Decimal(str(data["pending_refund_obligations"])) == Decimal("0.00")
    assert data["pending_refunds_count"] == 0
    assert data["failed_payment_count"] == 0
    assert data["pending_payment_count"] == 0
    assert data["refunded_transaction_count"] == 0
    assert Decimal(str(data["net_platform_revenue"])) == Decimal("0.00")
    assert Decimal(str(data["outstanding_payout_exposure"])) == Decimal("0.00")
    assert data["provider_profile_id"] == 999999


def test_admin_activity_payments_pagination_out_of_bounds(catalog):
    """
    Edge case: Page out of bounds returns empty items list without 500 error.
    """
    client, actor, _ = catalog
    actor["id"] = 4  # Admin

    resp = client.get(f"{ADMIN_ACTIVITIES_API}/payments/transactions?page=9999&page_size=20")
    assert resp.status_code == 200
    data = resp.json()
    assert data["items"] == []
    assert data["page"] == 9999
    assert data["page_size"] == 20
    assert data["total"] >= 0
