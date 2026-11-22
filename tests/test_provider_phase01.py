from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.core.provider_onboarding import require_live_authorization
from app.models.homestay import Homestay
from app.models.promotion import HomestayPromotion
from app.models.provider_profile import ProviderProfile
from app.models.provider_registration_authorization import (
    ProviderRegistrationAuthorization,
)
from app.models.shared import ListingReference, Review
from app.models.user import User
from app.routers.admin_providers import approve_provider_application
from tests import test_interactive_map

catalog = test_interactive_map.catalog


def test_fixed_discount_metadata_and_preview_match_booking_price(catalog):
    client, actor, engine = catalog
    actor["id"] = 5
    start = date.today() + timedelta(days=10)
    end = start + timedelta(days=1)
    with Session(engine) as db:
        db.add(
            HomestayPromotion(
                homestay_id=1,
                title="Fixed discount",
                discount_type="fixed",
                discount_amount=Decimal("1500.00"),
                start_date=start,
                end_date=start,
                is_active=True,
            )
        )
        db.commit()

    dates = {"check_in": str(start), "check_out": str(end)}
    preview = client.get("/api/v1/homestays/1/price-preview", params=dates)
    assert preview.status_code == 200
    body = preview.json()
    assert body["discount_type"] == "fixed"
    assert body["discount_percent"] is None
    assert body["discount_fixed_amount"] == body["discount_amount"] == 1500
    assert body["original_price"] == 5000 and body["final_price"] == 3500
    booking = client.post(
        "/api/v1/bookings", json={"homestay_id": 1, "guests": 1, **dates},
    )
    assert booking.status_code == 201
    assert Decimal(str(booking.json()["total_price"])) == Decimal(str(body["final_price"]))


def test_hidden_homestay_rejects_public_reads_and_booking(catalog):
    client, actor, engine = catalog
    actor["id"] = 5
    start = date.today() + timedelta(days=10)
    end = start + timedelta(days=1)
    assert client.get("/api/v1/homestays/1").status_code == 200
    with Session(engine) as db:
        db.execute(
            update(Homestay).where(Homestay.id == 1).values(approval_status="pending")
        )
        db.commit()
    dates = {"check_in": str(start), "check_out": str(end)}
    assert client.get("/api/v1/homestays/1").status_code == 404
    assert (
        client.get("/api/v1/homestays/1/availability", params=dates).status_code == 404
    )
    assert (
        client.get("/api/v1/homestays/1/price-preview", params=dates).status_code == 404
    )
    assert (
        client.post(
            "/api/v1/bookings",
            json={"homestay_id": 1, "guests": 1, **dates},
        ).status_code
        == 404
    )
    actor["id"] = 11
    assert client.get("/api/v1/homestays/mine").status_code == 200
    assert client.post(
        "/api/v1/bookings", json={"homestay_id": 1, "guests": 1, **dates},
    ).status_code == 403


def test_suspended_provider_listing_hidden_across_rental_and_map(catalog):
    client, _, engine = catalog
    assert client.get("/api/v1/rental-vehicles/1").status_code == 200
    with Session(engine) as db:
        db.execute(update(User).where(User.id == 9).values(is_active=False))
        db.commit()
    assert client.get("/api/v1/rental-vehicles/1").status_code == 404
    assert all(row["id"] != 1 for row in client.get("/api/v1/rental-vehicles").json()["items"])
    assert (
        client.get("/api/v1/map/search", params={"q": "Rental 1"}).json()["items"] == []
    )


def test_dashboard_counts_owned_published_reviews(catalog):
    client, actor, engine = catalog
    actor["id"] = 11
    with Session(engine) as db:
        ref = ListingReference(
            module_type="homestay",
            target_id=1,
            provider_profile_id=11,
            title="Kandy Rooms",
            district="Kandy",
        )
        db.add(ref)
        db.flush()
        db.add_all(
            [
                Review(
                    listing_reference_id=ref.id, user_id=5, rating=4, is_published=True
                ),
                Review(
                    listing_reference_id=ref.id, user_id=3, rating=2, is_published=True
                ),
                Review(
                    listing_reference_id=ref.id, user_id=2, rating=1, is_published=False
                ),
            ]
        )
        db.commit()
    response = client.get("/api/v1/homestays/provider/dashboard")
    assert response.status_code == 200
    assert response.json()["ratings"] == {"average_rating": 3.0, "total_reviews": 2}
    actor["id"] = 9
    assert client.get("/api/v1/homestays/provider/dashboard").status_code == 403


def test_stale_authorization_cannot_be_consumed_twice(catalog):
    _, _, engine = catalog
    with Session(engine, expire_on_commit=False) as writer:
        record = ProviderRegistrationAuthorization(
            email="phase01@example.test",
            token_hash="a" * 64,
            purpose="registration",
            expires_at=datetime.now(UTC) + timedelta(minutes=15),
        )
        writer.add(record)
        writer.commit()
        with Session(engine) as stale:
            snapshot = stale.get(ProviderRegistrationAuthorization, record.id)
            require_live_authorization(writer, record, consume=True)
            writer.commit()
            with pytest.raises(HTTPException) as error:
                require_live_authorization(stale, snapshot, consume=True)
            assert error.value.status_code == 401


def test_admin_approval_cannot_overwrite_interleaved_rejection(catalog, monkeypatch):
    _, _, engine = catalog

    def rejection_commits_before_lock(_db, _email):
        with Session(engine) as competitor:
            competitor.execute(
                update(ProviderProfile).where(ProviderProfile.id == 3).values(
                    approval_status="rejected", rejection_reason="Earlier review",
                )
            )
            competitor.commit()

    monkeypatch.setattr(
        "app.routers.admin_providers.lock_onboarding", rejection_commits_before_lock,
    )
    with Session(engine) as db:
        admin = db.get(User, 4)
        with pytest.raises(HTTPException) as conflict:
            approve_provider_application(3, admin, db)
        assert conflict.value.status_code in (400, 409)
        assert db.get(ProviderProfile, 3).approval_status == "rejected"
