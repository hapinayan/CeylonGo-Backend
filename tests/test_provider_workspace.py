from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.core.security import create_access_token
from app.main import app
from app.models.activity_booking import ActivityBooking
from app.models.activity_slot import ActivitySlot
from app.models.booking import Booking
from app.models.eco_promotion import (
    EcoPromotionAnalytics,
    EcoPromotionPackage,
    EcoSponsoredPromotion,
)
from app.models.homestay import Homestay
from app.models.provider_profile import ProviderProfile
from app.models.rental_booking import RentalBooking
from app.models.shared import (
    BookingReference,
    BookingStatusHistory,
    ListingReference,
    Notification,
    PaymentRefund,
    PaymentTransaction,
    ProviderSettlement,
    Review,
)
from app.routers.auth import get_current_user
from tests import test_interactive_map

catalog = test_interactive_map.catalog
API = "/api/v1/provider"
WORKSPACE = f"{API}/workspace"
DASHBOARD = f"{API}/dashboard"
ANALYTICS = f"{DASHBOARD}/analytics"
ACTIVITY = f"{DASHBOARD}/recent-activity"


@pytest.mark.parametrize(
    ("actor_id", "category", "listings", "bookings", "finance"),
    [
        (1, "activity", (7, 4, 1), True, False),
        (7, "artisan", (1, 1, 0), True, False),
        (8, "tour_guide", (1, 1, 0), True, False),
        (9, "rental", (2, 2, 0), True, False),
        (11, "homestay", (1, 1, 0), True, True),
        (12, "eco_tourism", (1, 1, 0), True, False),
    ],
)
def test_workspace_and_empty_metrics_for_category(
    catalog, actor_id, category, listings, bookings, finance
):
    client, actor, _ = catalog
    actor["id"] = actor_id
    response = client.get(WORKSPACE)
    assert response.status_code == 200, response.text
    workspace = response.json()
    assert workspace["category"] == category
    assert workspace["provider_id"] == actor_id
    assert workspace["account_active"] and workspace["approval_status"] == "approved"
    assert set(workspace["personal"]) == {"user_id", "name", "email"}
    assert "kyc" not in response.text.lower()
    assert ("finance:read" in workspace["permissions"]) == finance
    operations = app.openapi()["paths"]
    for link in workspace["navigation"]:
        assert link["method"].lower() in operations.get(link["path"], {}), link
    response = client.get(DASHBOARD)
    assert response.status_code == 200, response.text
    dashboard = response.json()
    assert dashboard["listings"] == dict(
        zip(("total", "active", "pending_approval"), listings, strict=True)
    )
    assert (dashboard["bookings"] is not None) == bookings
    if bookings:
        assert set(dashboard["bookings"].values()) == {0}
    assert dashboard["finance"] == ([] if finance else None)
    assert dashboard["sponsored_impressions"] == (
        0 if category == "eco_tourism" else None
    )
    assert "profile_views" in dashboard["unavailable_metrics"]
    assert dashboard["unread_notifications"] == 0
    monthly = client.get(ANALYTICS)
    assert monthly.status_code == 200, monthly.text
    assert len(monthly.json()["months"]) == 6


@pytest.mark.parametrize("user_id", [3, 4, 5, 6, 10, None])
def test_workspace_denies_unapproved_inactive_unauthenticated_and_non_provider(
    catalog, monkeypatch, user_id
):
    client, _, engine = catalog
    with Session(engine) as db:
        db.get(ProviderProfile, 10).approval_status = "rejected"
        db.commit()
    monkeypatch.delitem(app.dependency_overrides, get_current_user)
    headers = {}
    if user_id is not None:
        token = create_access_token({"sub": str(user_id)})
        headers["Authorization"] = f"Bearer {token}"
    for path in (WORKSPACE, DASHBOARD, ANALYTICS, ACTIVITY):
        assert client.get(path, headers=headers).status_code in (401, 403)


def seed_homestay_dashboard(
    db,
    *,
    provider_id,
    listing_id,
    buyer_id,
    anchor,
    paid_status="paid",
    refund_status="completed",
):
    day = anchor.date()
    owned = []
    for status, offset in (
        ("completed", -5),
        ("pending", 2),
        ("confirmed", 2),
        ("confirmed", 0),
        ("cancelled", -5),
    ):
        booking = Booking(
            homestay_id=listing_id,
            user_id=buyer_id,
            check_in=day + timedelta(days=offset),
            check_out=day + timedelta(days=offset + 2),
            guests=1,
            total_price=1000,
            status=status,
            created_at=anchor,
        )
        owned.append(booking)
    db.add_all(owned)
    db.flush()
    reference = BookingReference(
        booking_reference=f"dashboard-{provider_id}-{owned[0].id}",
        module_type="homestay",
        target_booking_id=owned[0].id,
        user_id=buyer_id,
        provider_profile_id=provider_id,
        total_price=1000,
    )
    db.add(reference)
    db.flush()
    payment = PaymentTransaction(
        booking_reference_id=reference.id,
        user_id=buyer_id,
        amount=1000,
        payment_method="online",
        payment_status=paid_status,
        gross_amount=1000,
        commission_amount=100,
        provider_payable_amount=900,
        paid_at=anchor,
    )
    db.add_all(
        [
            payment,
            PaymentTransaction(
                booking_reference_id=reference.id,
                user_id=buyer_id,
                amount=300,
                payment_method="cash",
                payment_status=paid_status,
                paid_at=anchor,
            ),
            PaymentTransaction(
                booking_reference_id=reference.id,
                user_id=buyer_id,
                amount=9999,
                payment_method="online",
                payment_status="pending",
            ),
            BookingStatusHistory(
                booking_reference_id=reference.id,
                to_status="completed",
                notes="CUSTOMER SECRET",
                created_at=anchor,
            ),
            ProviderSettlement(
                provider_profile_id=provider_id,
                period_start=day,
                period_end=day,
                gross_amount=1000,
                platform_fee=100,
                net_payable=900,
                settlement_status="pending",
            ),
        ]
    )
    db.flush()
    db.add_all(
        [
            PaymentRefund(
                payment_transaction_id=payment.id,
                amount=200,
                refund_reason="test",
                refund_status=refund_status,
                processed_at=anchor,
            ),
            PaymentRefund(
                payment_transaction_id=payment.id,
                amount=500,
                refund_reason="test",
                refund_status="pending",
            ),
        ]
    )
    listing_ref = ListingReference(
        module_type="homestay",
        target_id=listing_id,
        provider_profile_id=provider_id,
        title="Test property",
        district="Kandy",
    )
    db.add(listing_ref)
    db.flush()
    db.add_all(
        [
            Review(
                listing_reference_id=listing_ref.id,
                user_id=buyer_id,
                rating=rating,
                is_published=published,
                created_at=anchor,
                updated_at=anchor,
            )
            for rating, published in [(4, True), (5, True), (1, False)]
        ]
    )
    return owned[2].id


def test_homestay_metrics_date_filter_finance_history_and_legacy_dashboard(catalog):
    client, actor, engine = catalog
    actor["id"] = 11
    anchor = datetime.now(UTC).replace(tzinfo=None)
    day = anchor.date().isoformat()
    with Session(engine) as db:
        upcoming_id = seed_homestay_dashboard(
            db, provider_id=11, listing_id=1, buyer_id=5, anchor=anchor
        )
        db.add_all(
            [
                Notification(
                    user_id=user_id,
                    is_read=is_read,
                    title="title",
                    message="private",
                    notification_type="booking",
                )
                for user_id, is_read in [(11, False), (11, True), (9, False)]
            ]
        )
        db.commit()

    filters = {"start_date": day, "end_date": day}
    response = client.get(DASHBOARD, params=filters)
    assert response.status_code == 200, response.text
    dashboard = response.json()
    assert dashboard["bookings"] == {
        "total": 5,
        "pending": 1,
        "upcoming": 1,
        "active": 1,
        "completed": 1,
        "cancelled": 1,
    }
    assert dashboard["finance"] == [
        {
            "currency": "LKR",
            "verified_revenue": "1000.00",
            "cash_collections": "300.00",
            "verified_online_refunds": "200.00",
            "verified_cash_refunds": "0.00",
            "commission": "80.00",
            "provider_earnings": "720.00",
        }
    ]
    assert dashboard["settlements"][0]["pending_amount"] == "900.00"
    assert dashboard["reviews"] == {"count": 2, "average_rating": "4.50"}
    assert dashboard["unread_notifications"] == 1
    assert [item["booking_id"] for item in dashboard["upcoming_reservations"]] == [
        upcoming_id
    ]
    assert dashboard["upcoming_reservations"][0]["starts_at"] is None
    assert dashboard["upcoming_reservations"][0]["starts_on"] is not None

    analytics = client.get(ANALYTICS, params=filters)
    assert analytics.status_code == 200, analytics.text
    assert analytics.json()["months"][0]["bookings"] == 5
    assert analytics.json()["months"][0]["revenue"] == dashboard["finance"]
    past = (anchor.date() - timedelta(days=40)).isoformat()
    empty = client.get(DASHBOARD, params={"start_date": past, "end_date": past}).json()
    assert empty["bookings"]["total"] == 0
    assert empty["finance"] == [] and empty["settlements"] == dashboard["settlements"]

    first = client.get(ACTIVITY, params={**filters, "page_size": 1}).json()
    second = client.get(ACTIVITY, params={**filters, "page_size": 1, "page": 2}).json()
    assert first["has_more"] and first["items"] != second["items"]
    events = client.get(ACTIVITY, params=filters)
    assert any(i["kind"] == "booking_status_changed" for i in events.json()["items"])
    assert "CUSTOMER SECRET" not in events.text
    legacy_path = "/api/v1/homestays/provider/dashboard"
    legacy = client.get(legacy_path)
    assert legacy.status_code == 200, legacy.text
    assert legacy.json()["finances"]["pending_settlements_amount"] == "900.00"

    actor["id"] = 10
    foreign = client.get(DASHBOARD, params={**filters, "provider_id": 11}).json()
    assert foreign["bookings"]["total"] == 0
    assert foreign["finance"] is None and foreign["reviews"] is None
    assert foreign["unread_notifications"] == 0
    assert all(event["booking_id"] is None for event in foreign["recent_activity"])

    # A conflicting listing owner must fail closed on both dashboard paths.
    with Session(engine) as db:
        db.get(Homestay, 1).owner_id = 10
        db.commit()
    actor["id"] = 11
    assert client.get(DASHBOARD).json()["bookings"]["total"] == 0
    assert client.get(legacy_path).json()["bookings"]["total"] == 0


def test_activity_and_rental_reservations_use_their_native_inventory(catalog):
    client, actor, engine = catalog
    now = datetime.now(UTC).replace(tzinfo=None)
    start, end = now + timedelta(days=2), now + timedelta(days=3)
    with Session(engine) as db:
        slot = ActivitySlot(activity_id=1, starts_at=start, ends_at=end, capacity=4)
        db.add(slot)
        db.flush()
        db.add_all(
            [
                ActivityBooking(
                    activity_slot_id=slot.id,
                    user_id=5,
                    participants=1,
                    total_price=Decimal(250),
                    booking_status="confirmed",
                    payment_method="pay_at_location",
                ),
                RentalBooking(
                    vehicle_id=1,
                    user_id=5,
                    pickup_at=start,
                    return_at=end,
                    total_price=Decimal(250),
                    status="confirmed",
                ),
            ]
        )
        db.commit()
    for actor_id in (1, 9):
        actor["id"] = actor_id
        response = client.get(DASHBOARD)
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["bookings"]["upcoming"] == 1
        assert payload["upcoming_reservations"][0]["starts_at"].endswith("Z")
        assert payload["upcoming_reservations"][0]["starts_on"] is None
    actor["id"] = 10
    assert client.get(DASHBOARD).json()["bookings"]["total"] == 0
    actor["id"] = 5
    legacy = client.get("/api/v1/rental-bookings/me")
    assert legacy.status_code == 200, legacy.text
    assert [item["pickup_at"] for item in legacy.json()] == [start.isoformat()]


def test_refund_posting_month_currency_and_foreign_owner(catalog):
    client, actor, engine = catalog
    actor["id"] = 11
    january = datetime(2026, 1, 20)
    february = datetime(2026, 2, 3)
    with Session(engine) as db:
        seed_homestay_dashboard(
            db, provider_id=11, listing_id=1, buyer_id=5, anchor=january
        )
        reference = db.query(BookingReference).one()
        online = (
            db.query(PaymentTransaction)
            .filter_by(payment_method="online", payment_status="paid")
            .one()
        )
        db.query(PaymentRefund).filter_by(
            refund_status="completed"
        ).one().processed_at = february
        # A mismatched refund currency must never affect the original receipt.
        db.add(
            PaymentRefund(
                payment_transaction_id=online.id,
                amount=50,
                currency="USD",
                refund_reason="mismatched currency",
                refund_status="completed",
                processed_at=february,
            )
        )
        db.add(
            PaymentTransaction(
                booking_reference_id=reference.id,
                user_id=5,
                amount=25,
                currency="USD",
                payment_method="online",
                payment_status="paid",
                paid_at=january,
            )
        )
        # Corrupt ledger link claims a booking owned by a different provider.
        foreign = BookingReference(
            booking_reference="foreign-reference",
            module_type="homestay",
            target_booking_id=reference.target_booking_id,
            user_id=5,
            provider_profile_id=2,
            total_price=500,
        )
        db.get(ProviderProfile, 2).provider_type = "homestay"
        db.add(foreign)
        db.flush()
        db.add(
            PaymentTransaction(
                booking_reference_id=foreign.id,
                user_id=5,
                amount=500,
                payment_method="online",
                payment_status="paid",
                paid_at=january,
            )
        )
        db.commit()

    dates = {"start_date": "2026-01-01", "end_date": "2026-02-28"}
    response = client.get(ANALYTICS, params=dates)
    assert response.status_code == 200, response.text
    jan, feb = response.json()["months"]
    lkr, usd = jan["revenue"]
    assert (lkr["currency"], lkr["verified_revenue"]) == ("LKR", "1000.00")
    assert (usd["currency"], usd["verified_revenue"]) == ("USD", "25.00")
    assert lkr["verified_online_refunds"] == "0.00"
    (refunded,) = feb["revenue"]
    assert refunded["verified_online_refunds"] == "200.00"
    assert refunded["provider_earnings"] == "-180.00"
    actor["id"] = 2
    assert client.get(DASHBOARD, params=dates).json()["finance"] == []


def test_eco_sponsored_impressions_require_both_campaign_and_listing_ownership(catalog):
    client, actor, engine = catalog
    actor["id"] = 12
    today = datetime.now(UTC).date()
    with Session(engine) as db:
        package = EcoPromotionPackage(name="Test", duration_days=1, price=100)
        db.add(package)
        db.flush()
        for owner, total in [(12, 3), (1, 40)]:
            campaign = EcoSponsoredPromotion(
                eco_experience_id=1,
                provider_profile_id=owner,
                package_id=package.id,
                package_price_snapshot=100,
                duration_days_snapshot=1,
            )
            db.add(campaign)
            db.flush()
            db.add(
                EcoPromotionAnalytics(
                    promotion_id=campaign.id,
                    eco_experience_id=1,
                    metric_date=today,
                    impressions_count=total,
                )
            )
        db.commit()
    filters = {"start_date": today.isoformat(), "end_date": today.isoformat()}
    result = client.get(ANALYTICS, params=filters)
    assert result.status_code == 200, result.text
    assert result.json()["months"][0]["sponsored_impressions"] == 3
    assert client.get(DASHBOARD).json()["sponsored_impressions"] == 3


@pytest.mark.parametrize(
    ("path", "query"),
    [
        (DASHBOARD, {"start_date": "2026-01-01"}),
        (DASHBOARD, {"activity_limit": 21}),
        (ANALYTICS, {"start_date": "2027-01-01", "end_date": "2026-01-01"}),
        (ANALYTICS, {"start_date": "2024-01-01", "end_date": "2026-01-01"}),
        (ACTIVITY, {"page_size": 51}),
        (ACTIVITY, {"page": 0}),
    ],
)
def test_date_and_page_bounds(catalog, path, query):
    client, actor, _ = catalog
    actor["id"] = 11
    assert client.get(path, params=query).status_code == 422
