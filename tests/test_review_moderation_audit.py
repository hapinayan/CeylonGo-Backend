import json
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Event

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app.models.provider_profile import ProviderProfile
from app.models.shared import AdminAuditLog, ListingReference, Review
from app.models.tour_guide import TourGuide
from app.models.user import Base, User
from app.services import review_moderation, tour_guide_phase5
from tests.test_admin_rbac_matrix import client, db_session, role_headers
from tests.test_homestay_review_moderation import module_reviews


@pytest.mark.parametrize("module,method,url,reason_key", [
    ("homestay", "POST", "/api/v1/homestays/reviews/{id}/moderate", "reason"),
    ("artisan", "PATCH", "/api/v1/admin/artisan/reviews/{id}/publish", "moderation_reason"),
    ("tour_guide", "PATCH", "/api/v1/admin/tour-guides/reviews/{id}/moderate", "reason"),
])
def test_moderation_audit_reason_and_idempotency(client, db_session, role_headers, module_reviews,
                                                module, method, url, reason_key):
    review = module_reviews[module]
    uri = url.format(id=review.id)
    authorized = role_headers["content_moderator"]
    request = {"is_published": False, reason_key: " <b>Reviewed</b> "}
    assert client.request(method, uri, headers=authorized, json=request).status_code == 200
    event = db_session.query(AdminAuditLog).one()
    assert event.admin_user_id == db_session.query(User.id).filter_by(role="content_moderator").scalar()
    assert event.created_at and event.entity_id == review.id
    assert event.action == f"moderate_{module}_review"
    assert json.loads(event.details) == {"module": module, "is_published": False,
                                        "previous_is_published": True, "new_is_published": False,
                                        "reason": "Reviewed"}
    db_session.refresh(review)
    timestamp = review.updated_at
    assert client.request(method, uri, headers=authorized, json=request).status_code == 200
    assert client.request(method, uri, headers=role_headers["finance_admin"], json=request).status_code == 403
    secret = "password: do-not-log-this"
    invalid = client.request(method, uri, headers=authorized, json={"is_published": True, reason_key: secret})
    assert invalid.status_code == 422 and secret not in invalid.text
    foreign = next(row.id for key, row in module_reviews.items() if key != module)
    assert client.request(method, url.format(id=foreign), headers=authorized, json=request).status_code == 404
    db_session.refresh(review)
    assert review.is_published is False and review.updated_at == timestamp
    assert db_session.query(AdminAuditLog).count() == 1
    assert client.request(method, uri, headers=authorized, json={"is_published": True}).status_code == 200
    assert json.loads(db_session.query(AdminAuditLog).order_by(AdminAuditLog.id.desc()).first().details)["reason"] is None


@pytest.fixture
def guide_database(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'reviews.sqlite').as_posix()}", connect_args={"timeout": 15})

    @event.listens_for(engine, "connect")
    def configure(connection, _record):
        connection.create_function("sysutcdatetime", 0, lambda: "2026-01-01 00:00:00")

    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(User(id=1, full_name="Moderator", email="admin@example.com", role="content_moderator"))
        db.add(ProviderProfile(id=1, user_id=1, business_name="Guide", provider_type="tour_guide",
                               district="Kandy", phone="0771234567"))
        db.add(TourGuide(id=1, provider_profile_id=1, bio="Guide", daily_rate=100,
                         base_city="Kandy", base_district="Kandy", review_count=2, rating_average=4))
        db.add(ListingReference(id=1, module_type="tour_guide", target_id=1, provider_profile_id=1,
                                title="Guide", district="Kandy"))
        for review_id, score in ((1, 3), (2, 5)):
            db.add(Review(id=review_id, listing_reference_id=1, user_id=1, rating=score))
        db.commit()
    try:
        yield engine
    finally:
        engine.dispose()


def hide_guide_review(db, review_id):
    return tour_guide_phase5.admin_moderate_tour_guide_review(
        db, review_id=review_id, is_published=False, admin=db.get(User, 1),
    )


def test_audit_failure_rolls_back_review_and_guide_rating(guide_database, monkeypatch):
    real_audit = review_moderation.record_admin_audit_log

    def fail_after_flush(db, **kwargs):
        real_audit(db, **kwargs)
        db.flush()
        raise RuntimeError("audit failure")

    monkeypatch.setattr(review_moderation, "record_admin_audit_log", fail_after_flush)
    with Session(guide_database) as db, pytest.raises(RuntimeError, match="audit failure"):
        hide_guide_review(db, 1)
    with Session(guide_database) as db:
        assert db.get(Review, 1).is_published is True
        assert (db.get(TourGuide, 1).rating_average, db.get(TourGuide, 1).review_count) == (Decimal("4.00"), 2)
        assert db.query(AdminAuditLog).count() == 0


@pytest.mark.parametrize("second_review", [1, 2])
def test_concurrent_guide_moderation_serializes_state_and_ratings(guide_database, monkeypatch, second_review):
    held, waiting, release = Event(), Event(), Event()
    real_audit = review_moderation.record_admin_audit_log

    def hold_first(db, **kwargs):
        real_audit(db, **kwargs)
        if db.info["first"]:
            held.set()
            assert release.wait(10)

    @event.listens_for(guide_database, "before_cursor_execute")
    def observe_waiter(_connection, _cursor, statement, _params, context, _many):
        if context.execution_options.get("waiter") and statement.startswith("UPDATE reviews"):
            waiting.set()

    def moderate(review_id, first):
        with Session(guide_database) as db:
            db.info["first"] = first
            db.connection(execution_options={"waiter": not first})
            return hide_guide_review(db, review_id)

    monkeypatch.setattr(review_moderation, "record_admin_audit_log", hold_first)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(moderate, 1, True)
        try:
            assert held.wait(10)
            second = pool.submit(moderate, second_review, False)
            assert waiting.wait(10)
            assert not second.done()
        finally:
            release.set()
        assert not first.result(15).is_published
        assert not second.result(15).is_published
    with Session(guide_database) as db:
        guide = db.get(TourGuide, 1)
        expected = (Decimal("5.00"), 1) if second_review == 1 else (Decimal("0.00"), 0)
        assert (guide.rating_average, guide.review_count) == expected
        assert db.query(AdminAuditLog).count() == (1 if second_review == 1 else 2)
