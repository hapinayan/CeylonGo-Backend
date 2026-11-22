from datetime import date, datetime, timedelta
import re

import pytest
from sqlalchemy import event

from app.models.activity import Activity
from app.models.artisan_listing import ArtisanListing
from app.models.booking import Booking
from app.models.eco_experience import EcoExperience
from app.models.homestay import Homestay
from app.models.provider_profile import ProviderProfile
from app.models.rental import RentalEligibilityCheck
from app.models.rental_booking import RentalBooking
from app.models.rental_vehicle import RentalVehicle
from app.models.tour_guide import TourGuide, TourGuideBooking
from app.models.user import User
from tests.test_admin_rbac_matrix import (
    _create_user_and_headers, client, db_session, role_headers,
)


DASHBOARD = "/api/v1/admin/dashboard"
FULL_EMPTY = {
    "providers": {"total_profiles": 0, "pending_applications": 0},
    "bookings": {"homestay": 0, "rental_vehicle": 0, "tour_guide": 0},
    "pending_approvals": {
        "homestay_listings": 0, "activity_listings": 0, "eco_experience_listings": 0,
        "artisan_listings": 0, "rental_vehicles": 0, "tour_guide_profiles": 0,
    },
    "traveler_eligibility": {"pending_rental_checks": 0},
}
OPERATIONS_TABLES = {
    "provider_profiles", "homestays", "activities", "eco_experiences", "artisan_listings",
    "rental_vehicles", "tour_guides",
}
SUPPORT_TABLES = {"rental_bookings", "tour_guide_bookings", "rental_eligibility_checks"}
FINANCE_TABLES = {"homestay_bookings"}


@pytest.fixture
def dashboard_sql(db_session):
    statements = []
    bind = db_session.get_bind()

    def capture(_connection, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement.strip().lower())

    event.listen(bind, "before_cursor_execute", capture)
    try:
        yield statements
    finally:
        event.remove(bind, "before_cursor_execute", capture)


def assert_dashboard_sql(statements, expected_tables):
    # The only non-aggregate statement is the existing authentication user lookup.
    tables = set()
    allowed_tables = expected_tables | {"users"}
    if "rental_bookings" in expected_tables:
        allowed_tables |= {"rental_vehicles", "provider_profiles"}
    for statement in statements:
        assert statement.startswith("select "), statement
        assert set(re.findall(r"\b(?:from|join) (\w+)", statement)) <= allowed_tables, statement
        if "count(" in statement:
            tables.add(re.search(r"\bfrom (\w+)", statement).group(1))
        else:
            assert re.search(r"\bfrom users\b", statement), statement
    assert tables == expected_tables


@pytest.mark.parametrize("role", ["super_admin", "admin", "operations_admin", "support_admin", "finance_admin"])
def test_dashboard_visible_empty_counts_and_query_scope(client, role_headers, dashboard_sql, role):
    if role == "support_admin":
        expected = {
            "bookings": {"rental_vehicle": 0, "tour_guide": 0},
            "traveler_eligibility": {"pending_rental_checks": 0},
        }
        tables = SUPPORT_TABLES
    elif role == "finance_admin":
        expected = {"bookings": {"homestay": 0}}
        tables = FINANCE_TABLES
    else:
        expected = FULL_EMPTY
        tables = OPERATIONS_TABLES | SUPPORT_TABLES | FINANCE_TABLES

    response = client.get(DASHBOARD, headers=role_headers[role])

    assert response.status_code == 200
    assert response.json() == expected
    assert_dashboard_sql(dashboard_sql, tables)


def test_dashboard_denies_unauthorized_callers_without_counting(client, db_session, role_headers, dashboard_sql):
    _, inactive_headers = _create_user_and_headers(
        db_session, email="inactive-dashboard@example.com", role="super_admin", is_active=False,
    )
    dashboard_sql.clear()
    for role in ("content_moderator", "traveler", "provider"):
        assert client.get(DASHBOARD, headers=role_headers[role]).status_code == 403
    assert client.get(DASHBOARD, headers=inactive_headers).status_code == 403
    assert client.get(DASHBOARD).status_code == 401
    assert client.get(DASHBOARD, headers={"Authorization": "Bearer invalid"}).status_code == 401
    assert_dashboard_sql(dashboard_sql, set())


def test_dashboard_counts_match_operational_row_scopes(client, db_session, role_headers, dashboard_sql):
    provider_id = db_session.query(User.id).filter_by(role="provider").scalar()
    traveler_id = db_session.query(User.id).filter_by(role="traveler").scalar()
    other_user_id = db_session.query(User.id).filter_by(role="support_admin").scalar()
    # The SQLite fixture permits legacy orphan rows. These distinguish inner joins from bare counts.
    missing_id = 99999

    def add(model, **fields):
        db_session.execute(model.__table__.insert().values(**fields))

    for profile_id, user_id, status in ((1, provider_id, "pending"), (2, other_user_id, "approved"),
                                        (3, missing_id, "pending")):
        add(ProviderProfile, id=profile_id, user_id=user_id, business_name="Provider", provider_type="homestay",
            phone="0771234567", district="Kandy", approval_status=status)

    for row_id, status in ((1, "pending"), (2, "approved")):
        add(Homestay, id=row_id, name="Stay", location="Kandy", price_per_night=100, max_guests=2,
            approval_status=status)
        add(Activity, id=row_id, provider_profile_id=1, title="Walk", description="Guided walk",
            category="local", district="Kandy", city="Kandy", meeting_point="Station", duration_minutes=60,
            pricing_type="per_person", price=100, approval_status=status)
        add(EcoExperience, id=row_id, provider_profile_id=1, title="Nature walk", eco_category="Eco-Friendly Stays",
            duration_hours=1, district="Kandy", city="Kandy", meeting_point="Station", base_price=100,
            max_group_size=5, environmental_guidelines="Stay on path", wildlife_safety_summary="Keep distance",
            listing_status="pending_approval" if status == "pending" else status)

    for row_id, profile_id, status in ((1, 1, "pending"), (2, 2, "approved"), (3, missing_id, "pending")):
        add(ArtisanListing, id=row_id, provider_profile_id=profile_id, title="Craft", craft_category="wood",
            district="Kandy", city="Kandy", approval_status=status)
        add(RentalVehicle, id=row_id, provider_id=profile_id, vehicle_type="bike", vehicle_name="Bike",
            price_per_day=100, approval_status=status)
        add(TourGuide, id=row_id, provider_profile_id=3 if row_id == 3 else profile_id,
            bio="Local guide", daily_rate=100, base_city="Kandy", base_district="Kandy", approval_status=status)

    start = datetime(2025, 1, 1, 9)
    end = start + timedelta(days=1)
    add(Booking, id=1, homestay_id=1, user_id=traveler_id, check_in=start.date(), check_out=end.date(),
        guests=1, total_price=100)
    for row_id, vehicle_id, user_id in ((1, 1, traveler_id), (2, 2, traveler_id), (3, 3, traveler_id),
                                        (4, 1, missing_id)):
        add(RentalBooking, id=row_id, vehicle_id=vehicle_id, user_id=user_id, pickup_at=start,
            return_at=end, total_price=100)
    for row_id in (1, 2, 3):
        add(TourGuideBooking, id=row_id, tour_guide_id=1, traveler_id=traveler_id, booking_date=start.date(),
            start_datetime=start, end_datetime=end, number_of_people=1, total_price=100)
    for user_id, status in ((traveler_id, "pending"), (other_user_id, "approved"), (missing_id, "pending")):
        add(RentalEligibilityCheck, user_id=user_id, driver_license_number=f"TEST-{user_id}",
            license_expiry_date=date(2030, 1, 1), verification_status=status)
    db_session.commit()
    dashboard_sql.clear()

    response = client.get(DASHBOARD, headers=role_headers["operations_admin"])

    assert response.status_code == 200
    assert response.json() == {
        "providers": {"total_profiles": 2, "pending_applications": 1},
        "bookings": {"homestay": 1, "rental_vehicle": 2, "tour_guide": 3},
        "pending_approvals": dict.fromkeys(FULL_EMPTY["pending_approvals"], 1),
        "traveler_eligibility": {"pending_rental_checks": 1},
    }
    assert_dashboard_sql(dashboard_sql, OPERATIONS_TABLES | SUPPORT_TABLES | FINANCE_TABLES)
