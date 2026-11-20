"""API and transaction coverage for provider rejection."""

from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.models.artisan import ArtisanReservation
from app.models.shared import (
    BookingReference,
    BookingStatusHistory,
    Notification,
    PaymentTransaction,
)
from tests.test_artisan_reservations import (
    approved_artisan_user,
    auth_header,
    client,
    db_session,
    reservable_product,
    second_artisan_user,
    setup_test_db,
    traveler_user,
)


@pytest.fixture
def pending_reservation(client, traveler_user, reservable_product):
    response = client.post(
        "/api/v1/artisan-reservations",
        headers=auth_header(traveler_user),
        json={
            "showcase_item_id": reservable_product.id,
            "quantity": 3,
            "pickup_date": (date.today() + timedelta(days=1)).isoformat(),
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_provider_rejection_restores_stock_and_notifies_once(
    client, db_session, approved_artisan_user, traveler_user,
    reservable_product, pending_reservation,
):
    owner, _, _ = approved_artisan_user
    url = f"/api/v1/artisan-reservations/{pending_reservation}/reject"
    headers = auth_header(owner)
    response = client.post(url, headers=headers, json={"rejection_reason": "  Closed today  "})
    assert response.status_code == 200, response.text
    assert response.json()["reservation_status"] == "rejected"
    assert response.json()["rejected_by"] == owner.id
    assert response.json()["rejected_at"]
    assert response.json()["rejection_reason"] == "Closed today"
    assert response.json()["cancelled_by"] is None
    assert client.post(url, headers=headers, json={"rejection_reason": "Repeated"}).status_code == 409
    from app.services.artisan_reservation_expiry import expire_single_reservation

    assert not expire_single_reservation(
        db_session, pending_reservation, eligible_statuses=("rejected",)
    )[0]
    db_session.refresh(reservable_product)
    assert reservable_product.stock_quantity == 10
    ref = db_session.scalar(
        select(BookingReference).where(BookingReference.target_booking_id == pending_reservation)
    )
    assert ref.booking_status == "rejected"
    history = db_session.scalars(
        select(BookingStatusHistory).where(
            BookingStatusHistory.booking_reference_id == ref.id,
            BookingStatusHistory.to_status == "rejected",
        )
    ).one()
    assert history.changed_by == owner.id
    notification = db_session.scalars(
        select(Notification).where(
            Notification.notification_type == "artisan_reservation_rejected",
        )
    ).one()
    assert notification.user_id == traveler_user.id
    assert notification.message == "Closed today"


def test_rejection_requires_owner_reason_and_unpaid_pending_booking(
    client, db_session, approved_artisan_user, second_artisan_user, traveler_user,
    pending_reservation,
):
    owner, _, _ = approved_artisan_user
    outsider, _, _ = second_artisan_user
    url = f"/api/v1/artisan-reservations/{pending_reservation}/reject"
    reason = {"rejection_reason": "Workshop closed"}
    assert client.post(url, headers=auth_header(outsider), json=reason).status_code == 403
    assert client.post(url, headers=auth_header(traveler_user), json=reason).status_code == 403
    assert client.post(
        url, headers=auth_header(owner), json={"rejection_reason": "   "}
    ).status_code == 422
    ref = db_session.scalar(
        select(BookingReference).where(BookingReference.target_booking_id == pending_reservation)
    )
    ref.payment_status = "paid"
    db_session.commit()
    assert client.post(url, headers=auth_header(owner), json=reason).status_code == 409
    assert db_session.get(ArtisanReservation, pending_reservation).reservation_status == "pending"
    ref.payment_status = "unpaid"
    db_session.add(
        PaymentTransaction(
            booking_reference_id=ref.id, user_id=traveler_user.id, amount=ref.total_price,
            payment_status="completed", payment_method="cash",
        )
    )
    db_session.commit()
    assert client.post(url, headers=auth_header(owner), json=reason).status_code == 409


def test_rejection_rolls_back_after_notification_error(
    client, db_session, monkeypatch, approved_artisan_user,
    reservable_product, pending_reservation,
):
    owner, provider, _ = approved_artisan_user
    from app.services import provider_reservations

    def fail_notification(db, *args):
        db.flush()
        raise RuntimeError("Notification write failed")

    monkeypatch.setattr(provider_reservations, "create_notification", fail_notification)
    with pytest.raises(RuntimeError, match="Notification write failed"):
        provider_reservations.reject_artisan_reservation(
            db_session, pending_reservation, provider, "Workshop closed"
        )
    db_session.expire_all()
    assert db_session.get(ArtisanReservation, pending_reservation).reservation_status == "pending"
    assert reservable_product.stock_quantity == 7
    ref = db_session.scalar(
        select(BookingReference).where(BookingReference.target_booking_id == pending_reservation)
    )
    assert ref.booking_status == "pending"
    assert not db_session.scalars(
        select(BookingStatusHistory).where(BookingStatusHistory.to_status == "rejected")
    ).all()


def test_concurrent_rejection_restores_stock_once(
    db_session, approved_artisan_user, reservable_product, pending_reservation,
    tmp_path,
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from fastapi import HTTPException
    from sqlalchemy import create_engine, event, func
    from sqlalchemy.orm import Session

    from app.models.provider_profile import ProviderProfile
    from app.models.showcase_item import ShowcaseItem
    from app.services import provider_reservations
    from tests.test_artisan_reservations import configure_sqlite_connection

    _, provider, _ = approved_artisan_user
    provider_id, product_id = provider.id, reservable_product.id
    engine = create_engine(f"sqlite:///{tmp_path / 'rejection.sqlite'}")
    event.listen(engine, "connect", configure_sqlite_connection)
    db_session.rollback()
    with engine.connect() as target:
        source = db_session.connection().connection.driver_connection
        source.backup(target.connection.driver_connection)
    both_read_pending = Barrier(2)
    # Synchronize immediately before both workers attempt their status update.
    @event.listens_for(engine, "before_cursor_execute")
    def interleave(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("UPDATE artisan_reservations"):
            both_read_pending.wait(timeout=10)

    def reject():
        with Session(engine) as db:
            actor = db.get(ProviderProfile, provider_id)
            try:
                provider_reservations.reject_artisan_reservation(
                    db, pending_reservation, actor, "Workshop closed"
                )
            except HTTPException as exc:
                return exc.status_code
            return 200

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [pool.submit(reject) for _ in range(2)]
            assert sorted(result.result(timeout=20) for result in results) == [200, 409]
        with Session(engine) as db:
            assert db.get(ShowcaseItem, product_id).stock_quantity == 10
            assert db.scalar(
                select(func.count()).select_from(Notification).where(
                    Notification.notification_type == "artisan_reservation_rejected"
                )
            ) == 1
    finally:
        engine.dispose()
