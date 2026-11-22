"""
CeylonGo Module 03: Activities & Experiences
Test Suite for B14 Provider Payouts & Settlement Integration.

Requirements Covered:
- B14 Provider Payouts & Earnings Breakdown
- Activity Online Earnings Component Aggregation
- Custom & Dynamic Commission Rate Handling
- Booking Lifecycle Balance Transitions (Confirmed/Pending -> Completed)
- Verified Refund Reductions & Platform Commission Reversals
- Activity Provider Earnings Endpoint (GET /api/v1/provider/activities/earnings)
- Activity Provider Settlements List & Tenant Isolation (GET /api/v1/provider/activities/settlements)
- Admin Activity Settlement Creation & Guardrails (POST /api/v1/payments/admin/settlements)
- Double-Settlement & Over-Settlement Protection
- Overlapping Settlement Period Prevention
- Sri Lankan Bank Account Format Validation (SLIPS / CEFTS)
- Mock Activity Payout Adapter (Offline Simulation)
- Multi-Tenant & RBAC Security Enforcement
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
    BookingStatusHistory,
    PaymentRefund,
    PaymentTransaction,
    ProviderSettlement,
)
from app.services.activity_payments import (
    MockActivityPayoutAdapter,
    calculate_activity_provider_earnings_balances,
    get_activity_provider_earnings_components,
    validate_sri_lanka_bank_account,
)
from tests import test_interactive_map

catalog = test_interactive_map.catalog
PAYMENTS_API = "/api/v1/payments"
PROVIDER_ACTIVITIES_API = "/api/v1/provider/activities"


def _create_activity_slot(
    db: Session,
    activity_id: int = 1,
    capacity: int = 10,
    days_ahead: int = 3,
) -> ActivitySlot:
    now = datetime.now(UTC).replace(tzinfo=None)
    slot = ActivitySlot(
        activity_id=activity_id,
        starts_at=now + timedelta(days=days_ahead),
        ends_at=now + timedelta(days=days_ahead, hours=3),
        capacity=capacity,
        reserved_count=2,
        is_active=True,
    )
    db.add(slot)
    db.commit()
    db.refresh(slot)
    return slot


def _create_paid_activity_booking(
    db: Session,
    *,
    slot_id: int,
    user_id: int = 5,
    provider_profile_id: int = 1,
    gross_amount: Decimal = Decimal("10000.00"),
    commission_rate: Decimal = Decimal("10.00"),
    booking_status: str = "completed",
    payment_status: str = "paid",
    tx_id_suffix: str = "01",
) -> tuple[ActivityBooking, BookingReference, PaymentTransaction]:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    comm_amount = (gross_amount * (commission_rate / Decimal("100"))).quantize(Decimal("0.01"))
    net_payable = gross_amount - comm_amount

    booking = ActivityBooking(
        activity_slot_id=slot_id,
        user_id=user_id,
        participants=2,
        total_price=gross_amount,
        currency="LKR",
        payment_method="online",
        payment_status=payment_status,
        booking_status=booking_status,
        created_at=now,
    )
    db.add(booking)
    db.flush()

    booking_ref = BookingReference(
        booking_reference=f"ACT-PAY-TEST-{booking.id:04d}-{tx_id_suffix}",
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

    tx = PaymentTransaction(
        booking_reference_id=booking_ref.id,
        user_id=user_id,
        gateway_name="payhere",
        gateway_transaction_id=f"PH-TX-ACT-{booking.id}-{tx_id_suffix}",
        payment_method="online",
        payment_status="paid",
        amount=gross_amount,
        currency="LKR",
        gross_amount=gross_amount,
        commission_rate=commission_rate,
        commission_amount=comm_amount,
        provider_payable_amount=net_payable,
        paid_at=now,
        created_at=now,
    )
    db.add(tx)
    db.commit()
    db.refresh(booking)
    db.refresh(booking_ref)
    db.refresh(tx)
    return booking, booking_ref, tx


# ==========================================================
# 1. PROVIDER EARNINGS AGGREGATION & COMMISSION CALCULATIONS
# ==========================================================

def test_activity_provider_earnings_calculation_completed_and_pending(catalog):
    """
    Test that calculate_activity_provider_earnings_balances correctly segregates:
    - completed booking earnings into available settlement balance
    - confirmed (uncompleted) bookings into pending_earnings
    """
    client, actor, engine = catalog
    with Session(engine) as db:
        slot1 = _create_activity_slot(db, activity_id=1, days_ahead=2)
        slot2 = _create_activity_slot(db, activity_id=1, days_ahead=5)

        # 1. Completed booking: 10,000.00 gross -> 1,000 commission, 9,000 payable
        _create_paid_activity_booking(
            db, slot_id=slot1.id, gross_amount=Decimal("10000.00"), booking_status="completed", tx_id_suffix="A"
        )

        # 2. Confirmed booking (not yet taken place): 5,000.00 gross -> 500 commission, 4,500 pending
        _create_paid_activity_booking(
            db, slot_id=slot2.id, gross_amount=Decimal("5000.00"), booking_status="confirmed", tx_id_suffix="B"
        )

        balances = calculate_activity_provider_earnings_balances(db, provider_profile_id=1)

        assert balances["total_gross_earnings"] == Decimal("15000.00")
        assert balances["completed_booking_earnings"] == Decimal("10000.00")
        assert balances["ceylongo_commission"] == Decimal("1000.00")
        assert balances["provider_net_earnings"] == Decimal("9000.00")
        assert balances["available_settlement_balance"] == Decimal("9000.00")
        assert balances["pending_earnings"] == Decimal("4500.00")
        assert balances["total_paid_bookings"] == 2
        assert balances["currency"] == "LKR"


def test_activity_provider_earnings_custom_commission_rate(catalog):
    """
    Test that dynamic commission rates on transactions (e.g. 15% rate)
    are respected rather than assuming a fixed 10%.
    """
    client, actor, engine = catalog
    with Session(engine) as db:
        slot = _create_activity_slot(db, activity_id=1, days_ahead=3)

        # Custom 15% commission rate: 20,000 gross -> 3,000 commission, 17,000 payable
        _create_paid_activity_booking(
            db,
            slot_id=slot.id,
            gross_amount=Decimal("20000.00"),
            commission_rate=Decimal("15.00"),
            booking_status="completed",
            tx_id_suffix="CUSTOM15",
        )

        balances = calculate_activity_provider_earnings_balances(db, provider_profile_id=1)

        assert balances["total_gross_earnings"] == Decimal("20000.00")
        assert balances["completed_booking_earnings"] == Decimal("20000.00")
        assert balances["ceylongo_commission"] == Decimal("3000.00")
        assert balances["provider_net_earnings"] == Decimal("17000.00")
        assert balances["available_settlement_balance"] == Decimal("17000.00")


def test_activity_provider_earnings_with_verified_refund_reduction(catalog):
    """
    Test that verified refunds reduce completed provider payable and reverse commission proportionally.
    """
    client, actor, engine = catalog
    with Session(engine) as db:
        slot = _create_activity_slot(db, activity_id=1, days_ahead=2)

        # Completed booking of 10,000 LKR
        booking, booking_ref, tx = _create_paid_activity_booking(
            db, slot_id=slot.id, gross_amount=Decimal("10000.00"), booking_status="completed", tx_id_suffix="REF"
        )

        # 4,000 LKR completed refund (at 10% commission: 400 commission reversal, 3,600 provider reduction)
        refund = PaymentRefund(
            payment_transaction_id=tx.id,
            amount=Decimal("4000.00"),
            currency="LKR",
            refund_reason="Partial customer cancellation agreement",
            refund_status="completed",
            processed_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
        db.add(refund)
        db.commit()

        balances = calculate_activity_provider_earnings_balances(db, provider_profile_id=1)

        assert balances["total_gross_earnings"] == Decimal("10000.00")
        assert balances["completed_booking_earnings"] == Decimal("10000.00")
        assert balances["verified_refunds"] == Decimal("4000.00")
        assert balances["commission_reversal"] == Decimal("400.00")
        assert balances["ceylongo_commission"] == Decimal("600.00")  # 1000 - 400
        assert balances["provider_refund_reduction"] == Decimal("3600.00")
        assert balances["provider_net_earnings"] == Decimal("5400.00")  # 9000 - 3600
        assert balances["available_settlement_balance"] == Decimal("5400.00")


# ==========================================================
# 2. GET /api/v1/provider/activities/earnings ENDPOINT
# ==========================================================

def test_get_activity_provider_earnings_endpoint_success(catalog):
    """
    Test activity provider successfully queries server-calculated earnings breakdown.
    """
    client, actor, engine = catalog
    with Session(engine) as db:
        slot = _create_activity_slot(db, activity_id=1, days_ahead=2)
        _create_paid_activity_booking(
            db, slot_id=slot.id, gross_amount=Decimal("12000.00"), booking_status="completed", tx_id_suffix="EP"
        )

    actor["id"] = 1  # Activity provider owning activity 1
    resp = client.get(f"{PROVIDER_ACTIVITIES_API}/earnings")
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert Decimal(str(data["total_gross_earnings"])) == Decimal("12000.00")
    assert Decimal(str(data["completed_booking_earnings"])) == Decimal("12000.00")
    assert Decimal(str(data["ceylongo_commission"])) == Decimal("1200.00")
    assert Decimal(str(data["provider_net_earnings"])) == Decimal("10800.00")
    assert Decimal(str(data["available_settlement_balance"])) == Decimal("10800.00")
    assert data["currency"] == "LKR"
    assert data["total_paid_bookings"] == 1


def test_get_activity_provider_earnings_date_filtering(catalog):
    """
    Test date filtering on /earnings endpoint with period_start and period_end.
    """
    client, actor, engine = catalog
    today = datetime.now(UTC).date()

    with Session(engine) as db:
        slot_near = _create_activity_slot(db, activity_id=1, days_ahead=2)
        slot_far = _create_activity_slot(db, activity_id=1, days_ahead=20)

        _create_paid_activity_booking(
            db, slot_id=slot_near.id, gross_amount=Decimal("8000.00"), booking_status="completed", tx_id_suffix="NEAR"
        )
        _create_paid_activity_booking(
            db, slot_id=slot_far.id, gross_amount=Decimal("15000.00"), booking_status="completed", tx_id_suffix="FAR"
        )

    actor["id"] = 1

    # Filter period: next 5 days only (captures near slot, excludes far slot)
    p_start = (today + timedelta(days=1)).isoformat()
    p_end = (today + timedelta(days=5)).isoformat()
    resp_filtered = client.get(f"{PROVIDER_ACTIVITIES_API}/earnings?period_start={p_start}&period_end={p_end}")
    assert resp_filtered.status_code == 200
    filtered_data = resp_filtered.json()
    assert Decimal(str(filtered_data["total_gross_earnings"])) == Decimal("8000.00")
    assert Decimal(str(filtered_data["available_settlement_balance"])) == Decimal("7200.00")

    # Inverted date range returns 422
    resp_inv = client.get(f"{PROVIDER_ACTIVITIES_API}/earnings?period_start={p_end}&period_end={p_start}")
    assert resp_inv.status_code == 422
    assert "period_start cannot be after period_end" in resp_inv.json()["detail"]


def test_get_activity_provider_earnings_rbac_isolation(catalog):
    """
    Test RBAC enforcement on /earnings:
    - Non-activity provider (tour guide, user 8) -> 403
    - Traveler (user 5) -> 403
    - Pending activity provider (user 3) -> 403
    - Platform Admin (user 4) -> 403
    """
    client, actor, engine = catalog

    # 1. Tour guide provider
    actor["id"] = 8
    res_tg = client.get(f"{PROVIDER_ACTIVITIES_API}/earnings")
    assert res_tg.status_code == 403
    assert "Activity provider access required" in res_tg.json()["detail"]

    # 2. Traveler
    actor["id"] = 5
    res_trav = client.get(f"{PROVIDER_ACTIVITIES_API}/earnings")
    assert res_trav.status_code == 403

    # 3. Pending provider
    actor["id"] = 3
    res_pending = client.get(f"{PROVIDER_ACTIVITIES_API}/earnings")
    assert res_pending.status_code == 403

    # 4. Platform Admin (must use admin endpoints, not provider portal)
    actor["id"] = 4
    res_admin = client.get(f"{PROVIDER_ACTIVITIES_API}/earnings")
    assert res_admin.status_code == 403


# ==========================================================
# 3. GET /api/v1/provider/activities/settlements ENDPOINT
# ==========================================================

def test_list_activity_provider_settlements_tenant_isolation_and_filters(catalog):
    """
    Test listing settlements for activity provider:
    - Provider 1 sees only Provider 1's settlements, not Provider 2's
    - Status filtering works
    - Invalid status returns 422
    - Pagination works
    """
    client, actor, engine = catalog
    today = datetime.now(UTC).date()

    with Session(engine) as db:
        # Create settlements for Provider 1
        s1 = ProviderSettlement(
            provider_profile_id=1,
            period_start=today - timedelta(days=30),
            period_end=today - timedelta(days=16),
            gross_amount=Decimal("20000.00"),
            platform_fee=Decimal("2000.00"),
            net_payable=Decimal("18000.00"),
            currency="LKR",
            settlement_status="completed",
            payout_reference="SETTLE-P1-COMPLETED",
            paid_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
        s2 = ProviderSettlement(
            provider_profile_id=1,
            period_start=today - timedelta(days=15),
            period_end=today - timedelta(days=1),
            gross_amount=Decimal("10000.00"),
            platform_fee=Decimal("1000.00"),
            net_payable=Decimal("9000.00"),
            currency="LKR",
            settlement_status="pending",
            payout_reference="SETTLE-P1-PENDING",
        )
        # Create settlement for Provider 2 (MUST NOT BE VISIBLE TO P1)
        s3 = ProviderSettlement(
            provider_profile_id=2,
            period_start=today - timedelta(days=30),
            period_end=today - timedelta(days=1),
            gross_amount=Decimal("50000.00"),
            platform_fee=Decimal("5000.00"),
            net_payable=Decimal("45000.00"),
            currency="LKR",
            settlement_status="completed",
            payout_reference="SETTLE-P2-SECRET",
            paid_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
        db.add_all([s1, s2, s3])
        db.commit()

    # Query as Provider 1
    actor["id"] = 1
    resp_all = client.get(f"{PROVIDER_ACTIVITIES_API}/settlements")
    assert resp_all.status_code == 200
    p1_items = resp_all.json()
    assert len(p1_items) == 2
    p1_refs = {item["payout_reference"] for item in p1_items}
    assert "SETTLE-P1-COMPLETED" in p1_refs
    assert "SETTLE-P1-PENDING" in p1_refs
    assert "SETTLE-P2-SECRET" not in p1_refs  # Strict tenant isolation!

    # Status filter: completed only
    resp_comp = client.get(f"{PROVIDER_ACTIVITIES_API}/settlements?status=completed")
    assert resp_comp.status_code == 200
    comp_items = resp_comp.json()
    assert len(comp_items) == 1
    assert comp_items[0]["payout_reference"] == "SETTLE-P1-COMPLETED"
    assert comp_items[0]["settlement_status"] == "completed"

    # Invalid status returns 422
    resp_invalid = client.get(f"{PROVIDER_ACTIVITIES_API}/settlements?status=unsupported_status")
    assert resp_invalid.status_code == 422
    assert "Invalid settlement status" in resp_invalid.json()["detail"]

    # Pagination: page_size=1
    resp_p1 = client.get(f"{PROVIDER_ACTIVITIES_API}/settlements?page=1&page_size=1")
    assert resp_p1.status_code == 200
    assert len(resp_p1.json()) == 1


# ==========================================================
# 4. POST /api/v1/payments/admin/settlements INTEGRATION
# ==========================================================

def test_admin_settlement_creation_for_activity_provider(catalog):
    """
    Test admin successfully initiates and completes settlement for an activity provider:
    - Verifies balance reduction after settlement (prevents double-settlement)
    - Rejects over-settlement (exceeding available balance)
    - Rejects non-positive net_payable
    - Rejects overlapping settlement period
    - Rejects duplicate payout reference
    - Rejects non-admin callers (403)
    """
    client, actor, engine = catalog
    today = datetime.now(UTC).date()
    p_start = (today - timedelta(days=7)).isoformat()
    p_end = (today + timedelta(days=7)).isoformat()

    with Session(engine) as db:
        slot = _create_activity_slot(db, activity_id=1, days_ahead=2)
        _create_paid_activity_booking(
            db, slot_id=slot.id, gross_amount=Decimal("10000.00"), booking_status="completed", tx_id_suffix="ADM"
        )

    # 1. Non-admin caller blocked with 403
    actor["id"] = 1  # Activity provider
    res_unauth = client.post(
        f"{PAYMENTS_API}/admin/settlements",
        json={
            "provider_profile_id": 1,
            "period_start": p_start,
            "period_end": p_end,
            "gross_amount": "10000.00",
            "platform_fee": "1000.00",
            "net_payable": "9000.00",
            "settlement_status": "pending",
        },
    )
    assert res_unauth.status_code == 403

    # Switch to Admin (user 4)
    actor["id"] = 4

    # 2. Over-settlement blocked: attempting to settle 12,000 when only 9,000 available
    res_over = client.post(
        f"{PAYMENTS_API}/admin/settlements",
        json={
            "provider_profile_id": 1,
            "period_start": p_start,
            "period_end": p_end,
            "gross_amount": "14000.00",
            "platform_fee": "2000.00",
            "net_payable": "12000.00",
            "settlement_status": "pending",
        },
    )
    assert res_over.status_code == 400
    assert "exceeds" in res_over.json()["detail"].lower()

    # 3. Non-positive net_payable blocked: 0.00 or negative
    res_zero = client.post(
        f"{PAYMENTS_API}/admin/settlements",
        json={
            "provider_profile_id": 1,
            "period_start": p_start,
            "period_end": p_end,
            "gross_amount": "1000.00",
            "platform_fee": "1000.00",
            "net_payable": "0.00",
            "settlement_status": "pending",
        },
    )
    assert res_zero.status_code == 400

    # 4. Valid settlement succeeds (201 Created)
    res_ok = client.post(
        f"{PAYMENTS_API}/admin/settlements",
        json={
            "provider_profile_id": 1,
            "period_start": p_start,
            "period_end": p_end,
            "gross_amount": "10000.00",
            "platform_fee": "1000.00",
            "net_payable": "9000.00",
            "payout_reference": "BANK-TX-ACT-778899",
            "settlement_status": "completed",
        },
    )
    assert res_ok.status_code == 201, res_ok.text
    settle_data = res_ok.json()
    assert Decimal(str(settle_data["net_payable"])) == Decimal("9000.00")
    assert settle_data["payout_reference"] == "BANK-TX-ACT-778899"

    # 5. Double-settlement prevention: Available settlement balance drops to 0.00!
    actor["id"] = 1  # Provider view
    earn_res = client.get(f"{PROVIDER_ACTIVITIES_API}/earnings")
    assert earn_res.status_code == 200
    assert Decimal(str(earn_res.json()["available_settlement_balance"])) == Decimal("0.00")
    assert Decimal(str(earn_res.json()["total_settled"])) == Decimal("9000.00")

    # 6. Overlapping settlement period blocked
    actor["id"] = 4  # Admin
    res_overlap = client.post(
        f"{PAYMENTS_API}/admin/settlements",
        json={
            "provider_profile_id": 1,
            "period_start": p_start,
            "period_end": p_end,
            "gross_amount": "5000.00",
            "platform_fee": "500.00",
            "net_payable": "4500.00",
            "settlement_status": "pending",
        },
    )
    assert res_overlap.status_code == 400
    assert "overlaps with existing settlement" in res_overlap.json()["detail"]

    # 7. Duplicate payout reference blocked (409 Conflict)
    # Create an activity and completed booking for Provider 2 within p_start to p_end
    with Session(engine) as db:
        act2 = Activity(
            title="Galle Coastal Safari",
            description="A scenic coastal safari experience in Galle.",
            category="cultural",
            district="Galle",
            city="Galle",
            meeting_point="Galle Fort Lighthouse",
            duration_minutes=120,
            pricing_type="per_person",
            price=Decimal("5000.00"),
            provider_profile_id=2,
            approval_status="approved",
            is_active=True,
        )
        db.add(act2)
        db.flush()
        slot2 = _create_activity_slot(db, activity_id=act2.id, days_ahead=2)
        _create_paid_activity_booking(
            db,
            slot_id=slot2.id,
            provider_profile_id=2,
            gross_amount=Decimal("10000.00"),
            booking_status="completed",
            tx_id_suffix="P2ADM",
        )

    # Attempt to settle Provider 2 using the same payout reference already recorded in step 4
    res_dup_ref = client.post(
        f"{PAYMENTS_API}/admin/settlements",
        json={
            "provider_profile_id": 2,
            "period_start": p_start,
            "period_end": p_end,
            "gross_amount": "10000.00",
            "platform_fee": "1000.00",
            "net_payable": "9000.00",
            "payout_reference": "BANK-TX-ACT-778899",  # Duplicate reference from step 4
            "settlement_status": "completed",
        },
    )
    assert res_dup_ref.status_code == 409
    assert "Payout reference" in res_dup_ref.json()["detail"]


# ==========================================================
# 5. SRI LANKAN BANK ACCOUNT VALIDATION UNIT TESTS
# ==========================================================

def test_sri_lankan_bank_account_validation():
    """
    Test SLIPS / CEFTS bank account format validator.
    """
    # Valid account details
    res_valid = validate_sri_lanka_bank_account(
        bank_code="7010",        # Bank of Ceylon
        branch_code="001",       # Colombo Fort
        account_number="123456789012",
        account_holder_name="A. B. C. Perera",
    )
    assert res_valid["is_valid"] is True
    assert res_valid["errors"] == []
    assert res_valid["bank_code"] == "7010"
    assert res_valid["branch_code"] == "001"
    assert res_valid["account_number_masked"] == "********9012"
    assert res_valid["account_holder_name"] == "A. B. C. Perera"

    # Invalid bank code (< 4 digits or non-numeric)
    res_bad_bank = validate_sri_lanka_bank_account("701", "001", "12345678", "Perera")
    assert res_bad_bank["is_valid"] is False
    assert any("bank_code" in err for err in res_bad_bank["errors"])

    # Invalid branch code (< 3 digits)
    res_bad_branch = validate_sri_lanka_bank_account("7010", "1", "12345678", "Perera")
    assert res_bad_branch["is_valid"] is False
    assert any("branch_code" in err for err in res_bad_branch["errors"])

    # Invalid account number (letters or out-of-range length)
    res_bad_acc = validate_sri_lanka_bank_account("7010", "001", "12345", "Perera")
    assert res_bad_acc["is_valid"] is False
    assert any("account_number" in err for err in res_bad_acc["errors"])

    # Invalid holder name (special characters, SQL injection characters)
    res_bad_name = validate_sri_lanka_bank_account("7010", "001", "12345678", "Perera <script>")
    assert res_bad_name["is_valid"] is False
    assert any("account_holder_name" in err for err in res_bad_name["errors"])


# ==========================================================
# 6. MOCK ACTIVITY PAYOUT ADAPTER UNIT TESTS
# ==========================================================

def test_mock_activity_payout_adapter():
    """
    Test MockActivityPayoutAdapter offline simulation.
    """
    adapter = MockActivityPayoutAdapter()

    res = adapter.execute_payout(
        provider_profile_id=1,
        amount=Decimal("9000.00"),
        currency="LKR",
        payout_reference="MOCK-REF-1234",
    )
    assert res["status"] == "simulated_success"
    assert res["provider_profile_id"] == 1
    assert res["amount"] == Decimal("9000.00")
    assert res["currency"] == "LKR"
    assert res["payout_reference"] == "MOCK-REF-1234"
    assert res["is_live"] is False

    # Negative amount raises ValueError
    with pytest.raises(ValueError, match="Payout amount must be positive"):
        adapter.execute_payout(provider_profile_id=1, amount=Decimal("-100.00"))

    # Non-LKR currency raises ValueError
    with pytest.raises(ValueError, match="Only LKR payouts are supported"):
        adapter.execute_payout(provider_profile_id=1, amount=Decimal("100.00"), currency="USD")
