from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.mvp_security import create_mvp_access_token
from app.database.mvp_session import get_mvp_db
from app.main_mvp import create_mvp_app
from app.models.mvp import (
    MVPBase, MVPBooking, MVPBookingPayment, MVPListing, MVPProviderProfile, MVPUser,
)


@pytest.fixture
def directory():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False},
    )
    MVPBase.metadata.create_all(engine)
    application = create_mvp_app()

    def database():
        with Session(engine) as db:
            yield db

    application.dependency_overrides[get_mvp_db] = database
    headers = {}
    now = datetime.now(UTC).replace(tzinfo=None)
    with Session(engine) as db:
        roles = {}
        for role in ("admin", "super_admin", "traveler", "provider"):
            user = MVPUser(email=f"{role}@example.test", role=role,
                           full_name=f"Test {role}", hashed_password="unused")
            roles[role] = user
            db.add(user)
        db.flush()
        for role, user in roles.items():
            headers[role] = {"Authorization": "Bearer " + create_mvp_access_token(
                user.id, user.email, user.role,
            )}
        profile = MVPProviderProfile(
            user_id=roles["provider"].id, business_name="Test provider",
            district="Kandy", phone="0771234567", verification_status="approved",
        )
        db.add(profile)
        db.flush()
        for category in ("homestay", "adventure"):
            listing = MVPListing(provider_id=profile.id, category=category,
                                 title=f"{category} service", description="Listing description",
                                 district="Kandy", locality="Kandy", price=Decimal("100.00"),
                                 approval_status="approved", is_active=True)
            db.add(listing)
            db.flush()
            booking = MVPBooking(
                listing_id=listing.id, traveler_id=roles["traveler"].id,
                request_key=f"booking-{category}", request_hash=bytes(32), starts_at=now,
                ends_at=now+timedelta(days=1), quantity=1, total_amount=Decimal("100.00"),
                status="confirmed" if category == "homestay" else "pending",
            )
            db.add(booking)
            db.flush()
            db.add(MVPBookingPayment(
                booking_id=booking.id, request_key=f"payment-{category}", request_hash=bytes(32),
                attempt_no=1, method="simulated",
                status="demo_succeeded" if category == "homestay" else "demo_failed",
                display_amount=Decimal("100.00"),
            ))
        db.commit()
    with TestClient(application) as client:
        yield client, headers
    engine.dispose()


@pytest.mark.parametrize("role", ["admin", "super_admin"])
def test_admin_directory_uses_persisted_counts_filters_and_payments(directory, role):
    client, headers = directory
    auth = headers[role]
    base = "/api/v1/admin"
    travelers = client.get(base + "/travelers", params={"search": "Traveler"}, headers=auth)
    assert travelers.status_code == 200
    assert travelers.json()["total"] == 1
    providers = client.get(base + "/provider-directory", headers=auth)
    assert providers.status_code == 200
    assert providers.json()["total"] == 1
    bookings = client.get(base + "/bookings", params={"category": "activity"}, headers=auth)
    assert bookings.status_code == 200
    assert bookings.json()["total"] == 1
    booking = bookings.json()["items"][0]
    assert booking["payment_status"] == "failed"
    assert client.get(base + f'/bookings/{booking["id"]}', headers=auth).status_code == 200
    stats = client.get(base + "/bookings/summary", headers=auth).json()
    assert (stats["total_bookings"], stats["confirmed_bookings"], stats["pending_bookings"]) == (2, 1, 1)
    payments = client.get(base + "/payments", params={"status": "captured"}, headers=auth)
    assert payments.status_code == 200
    assert payments.json()["total"] == 1
    assert payments.json()["items"][0]["refund_status"] is None
    listing = client.get(
        base + "/listing-directory", params={"category": "activity"}, headers=auth,
    ).json()
    assert listing["counts"] == {"approved": 1}
    assert listing["items"][0]["listing"]["title"] == "adventure service"


def test_admin_directory_rejects_travelers_and_reserves_access_control(directory):
    client, headers = directory
    base = "/api/v1/admin"
    for path in ("/travelers", "/provider-directory", "/bookings", "/payments", "/listing-directory?category=activity"):
        assert client.get(base + path, headers=headers["traveler"]).status_code == 403
    assert client.get(base + "/management/invitations", headers=headers["admin"]).status_code == 403
