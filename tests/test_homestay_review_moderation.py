import json
from decimal import Decimal

import pytest

from app.models.provider_profile import ProviderProfile
from app.models.shared import AdminAuditLog, ListingReference, Review
from app.models.user import User
from tests.test_admin_rbac_matrix import client, db_session, role_headers


MODULES = ("homestay", "artisan", "tour_guide", "rental_vehicle", "activity", "eco_experience")


@pytest.fixture
def module_reviews(db_session, role_headers):
    provider = db_session.query(User).filter_by(role="provider").one()
    traveler = db_session.query(User).filter_by(role="traveler").one()
    profile = ProviderProfile(user_id=provider.id, business_name="Review test", provider_type="homestay",
                              phone="0771234567", district="Kandy")
    db_session.add(profile)
    db_session.flush()
    reviews = {}
    for module in MODULES:
        listing = ListingReference(module_type=module, target_id=1, provider_profile_id=profile.id,
                                   title=module, district="Kandy")
        db_session.add(listing)
        db_session.flush()
        review = Review(listing_reference_id=listing.id, user_id=traveler.id, rating=Decimal("4.50"),
                        title="Review", comment="Helpful host", is_verified_booking=True)
        db_session.add(review)
        reviews[module] = review
    db_session.commit()
    return reviews


def _moderate(client, review, headers, published):
    return client.post(f"/api/v1/homestays/reviews/{review.id}/moderate", headers=headers,
                       json={"is_published": published})


@pytest.mark.parametrize("role", ["content_moderator", "super_admin", "admin"])
def test_homestay_moderation_preserves_response_and_audit(client, db_session, role_headers, module_reviews, role):
    review = module_reviews["homestay"]
    actor_id = db_session.query(User.id).filter_by(role=role).scalar()

    for published in (False, True):
        response = _moderate(client, review, role_headers[role], published)
        assert response.status_code == 200
        assert response.json() == {
            "id": review.id,
            "listing_reference_id": review.listing_reference_id,
            "booking_reference_id": None,
            "user_id": review.user_id,
            "rating": "4.50",
            "title": "Review",
            "comment": "Helpful host",
            "is_verified_booking": True,
            "is_published": published,
            "reviewer_name": None,
            "created_at": review.created_at.isoformat(),
        }
        db_session.refresh(review)
        assert review.is_published is published

    events = db_session.query(AdminAuditLog).order_by(AdminAuditLog.id).all()
    assert [json.loads(event.details) for event in events] == [
        {"module": "homestay", "is_published": published, "previous_is_published": not published,
         "new_is_published": published, "reason": None}
        for published in (False, True)
    ]
    for event in events:
        assert (event.admin_user_id, event.action, event.entity_name, event.entity_id) == (
            actor_id, "moderate_homestay_review", "reviews", review.id,
        )


@pytest.mark.parametrize("module", MODULES[1:])
def test_foreign_review_returns_not_found_without_changes(client, db_session, role_headers, module_reviews, module):
    review = module_reviews[module]
    before = {column.key: getattr(review, column.key) for column in Review.__mapper__.column_attrs}
    response = _moderate(client, review, role_headers["content_moderator"], False)

    assert response.status_code == 404
    assert response.json() == {"detail": "Review not found"}
    db_session.refresh(review)
    assert {key: getattr(review, key) for key in before} == before
    assert db_session.query(AdminAuditLog).count() == 0


@pytest.mark.parametrize("role", ["operations_admin", "finance_admin", "support_admin"])
def test_homestay_moderation_denies_other_admin_roles(client, db_session, role_headers, module_reviews, role):
    review = module_reviews["homestay"]
    assert _moderate(client, review, role_headers[role], False).status_code == 403
    db_session.refresh(review)
    assert review.is_published is True
    assert db_session.query(AdminAuditLog).count() == 0
