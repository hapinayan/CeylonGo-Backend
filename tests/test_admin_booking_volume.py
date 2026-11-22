from datetime import datetime, timedelta
import socket

import pytest
from sqlalchemy import event
from sqlalchemy.exc import OperationalError

from app.models.booking import Booking
from app.models.homestay import Homestay
from app.models.provider_profile import ProviderProfile
from app.models.rental_booking import RentalBooking
from app.models.rental_vehicle import RentalVehicle
from app.models.user import User
from tests.test_admin_dashboard import dashboard_sql
from tests.test_admin_rbac_matrix import _create_user_and_headers, client, db_session, role_headers


PATH = "/api/v1/admin/reports/booking-volume"
START = datetime(2026, 1, 1)
END = START + timedelta(days=90)
DATES = {"created_from": "2026-01-01T05:30:00+05:30", "created_before": "2026-04-01T00:00:00Z"}
MODULES = ("homestay", "rental_vehicle")


@pytest.fixture
def bookings(db_session, role_headers):
    db = db_session
    provider = db.query(User.id).filter_by(role="provider").scalar()
    traveler = db.query(User.id).filter_by(role="traveler").scalar()

    def insert(model, **fields):
        db.execute(model.__table__.insert().values(**fields))

    insert(ProviderProfile, id=1, user_id=provider, provider_type="rental", business_name="Private name",
           phone="0771234567", district="Kandy")
    insert(RentalVehicle, id=1, provider_id=1, vehicle_type="bike", vehicle_name="Bike", price_per_day=100)
    insert(RentalVehicle, id=2, provider_id=9999, vehicle_type="bike", vehicle_name="Orphan", price_per_day=100)
    insert(Homestay, id=1, name="Stay", location="Kandy", price_per_night=100, max_guests=2)
    for row_id, status, created in ((1, "confirmed", START), (2, "completed", END - timedelta(microseconds=1)),
                                    (3, "expired", END), (4, "pending", START - timedelta(microseconds=1))):
        insert(Booking, id=row_id, homestay_id=1, user_id=traveler, check_in=START.date(),
               check_out=(START + timedelta(days=1)).date(), guests=1, total_price=100,
               status=status, created_at=created)
    for row_id, vehicle, user, status in ((1, 1, traveler, "manual_review"), (2, 1, traveler, "confirmed"),
                                          (3, 1, traveler, "confirmed"), (4, 2, traveler, "excluded_provider"),
                                          (5, 1, 9999, "excluded_user"), (6, 9999, traveler, "excluded_vehicle")):
        insert(RentalBooking, id=row_id, vehicle_id=vehicle, user_id=user, pickup_at=START,
               return_at=START + timedelta(days=1), total_price=100, status=status, created_at=START)
    db.commit()


def booking_queries(statements):
    return [sql for sql in statements if "homestay_bookings" in sql or "rental_bookings" in sql]


@pytest.mark.parametrize("role,allowed", [
    ("super_admin", MODULES), ("admin", MODULES), ("operations_admin", MODULES),
    ("finance_admin", ("homestay",)), ("support_admin", ("rental_vehicle",)),
    ("content_moderator", ()), ("traveler", ()), ("provider", ()),
])
def test_booking_volume_role_matrix(client, role_headers, dashboard_sql, role, allowed):
    for module in MODULES:
        dashboard_sql.clear()
        response = client.get(PATH, params=DATES | {"module": module}, headers=role_headers[role])
        if module in allowed:
            assert response.status_code == 200
            assert response.json()["by_status"] == [] and response.json()["total"] == 0
        else:
            assert response.status_code == 403 and booking_queries(dashboard_sql) == []


def test_inactive_and_invalid_auth_never_queries_bookings(client, db_session, dashboard_sql):
    _, inactive = _create_user_and_headers(db_session, email="inactive-report@example.com",
                                          role="operations_admin", is_active=False)
    for headers, expected in ((inactive, 403), ({}, 401), ({"Authorization": "Bearer invalid"}, 401)):
        dashboard_sql.clear()
        assert client.get(PATH, headers=headers, params=DATES | {"module": "homestay"}).status_code == expected
        assert booking_queries(dashboard_sql) == []


@pytest.mark.parametrize("params", [
    DATES, DATES | {"module": ""}, DATES | {"module": "tour_guide"}, {"module": "homestay"},
    {"module": "homestay", "created_from": DATES["created_from"]},
    {"module": "homestay", "created_before": DATES["created_before"]},
    DATES | {"module": "homestay", "created_from": "2026-01-01T00:00:00"},
    DATES | {"module": "homestay", "created_from": "2026-02-30T00:00:00Z"},
    DATES | {"module": "homestay", "created_from": "2026-01-01T00:00:00-00:00"},
    DATES | {"module": "homestay", "created_before": "2026-04-01T00:00:00.000001Z"},
    DATES | {"module": "homestay", "created_before": "2026-01-01T00:00:00Z"},
    DATES | {"module": "homestay", "created_before": "2025-12-31T00:00:00Z"},
])
def test_invalid_booking_windows_do_not_query_source(client, role_headers, dashboard_sql, params):
    dashboard_sql.clear()
    assert client.get(PATH, params=params, headers=role_headers["admin"]).status_code == 422
    assert booking_queries(dashboard_sql) == []


@pytest.mark.parametrize("module,table,expected", [
    ("homestay", "homestay_bookings", [{"status": "completed", "count": 1}, {"status": "confirmed", "count": 1}]),
    ("rental_vehicle", "rental_bookings", [{"status": "confirmed", "count": 2}, {"status": "manual_review", "count": 1}]),
])
def test_90_day_aggregate_is_private_and_read_only(client, role_headers, dashboard_sql, bookings, monkeypatch,
                                                    module, table, expected):
    def reject_outbound(*_args, **_kwargs):
        pytest.fail("Booking report attempted an external connection")

    monkeypatch.setattr(socket.socket, "connect", reject_outbound)
    monkeypatch.setattr(socket.socket, "connect_ex", reject_outbound)
    params = DATES | {"module": module}
    for _ in range(2):
        dashboard_sql.clear()
        response = client.get(PATH, params=params, headers=role_headers["operations_admin"])
        assert response.status_code == 200
        assert response.json() == {"module": module, "created_from": "2026-01-01T00:00:00Z",
                                   "created_before": "2026-04-01T00:00:00Z",
                                   "total": sum(item["count"] for item in expected), "by_status": expected}
        assert all(sql.startswith("select ") for sql in dashboard_sql)
        sql, = booking_queries(dashboard_sql)
        assert f"from {table}" in sql and "group by" in sql
        assert "rental_bookings" not in sql if module == "homestay" else "homestay_bookings" not in sql
        projection = sql.split("from", 1)[0].strip()
        assert projection == f"select {table}.status as status, count({table}.id) as count"
        if module == "rental_vehicle":
            assert all(f"join {name}" in sql for name in ("rental_vehicles", "provider_profiles", "users"))
        else:
            assert "join" not in sql


def test_rental_report_matches_dashboard_and_later_current_status(client, db_session, role_headers, bookings):
    headers = role_headers["support_admin"]
    params = DATES | {"module": "rental_vehicle"}
    before = client.get(PATH, headers=headers, params=params).json()
    dashboard = client.get("/api/v1/admin/dashboard", headers=headers).json()
    assert before["total"] == dashboard["bookings"]["rental_vehicle"] == 3
    db_session.get(RentalBooking, 1).status = "cancelled"
    db_session.commit()
    later = client.get(PATH, headers=headers, params=params).json()
    assert later["total"] == before["total"]
    assert later["by_status"] == [{"status": "cancelled", "count": 1}, {"status": "confirmed", "count": 2}]


@pytest.mark.parametrize("unsafe_status", ["private@example.com", "phone_0771234567", "confirmed "])
def test_sensitive_or_malformed_stored_status_returns_generic_error(client, db_session, role_headers, bookings,
                                                                   caplog, unsafe_status):
    db_session.get(RentalBooking, 1).status = unsafe_status
    db_session.commit()
    result = client.get(PATH, headers=role_headers["admin"], params=DATES | {"module": "rental_vehicle"})
    assert result.status_code == 500 and result.json() == {"detail": "Unable to load booking volume"}
    assert unsafe_status not in result.text + caplog.text


def test_booking_query_failure_returns_generic_error(client, db_session, role_headers, caplog):
    bind = db_session.get_bind()

    def fail(_conn, _cursor, statement, _params, _context, _many):
        if "FROM homestay_bookings" in statement:
            raise OperationalError("private SQL statement", {}, RuntimeError("private record"))

    event.listen(bind, "before_cursor_execute", fail)
    try:
        result = client.get(PATH, headers=role_headers["admin"], params=DATES | {"module": "homestay"})
    finally:
        event.remove(bind, "before_cursor_execute", fail)
    assert result.status_code == 500 and result.json() == {"detail": "Unable to load booking volume"}
    assert "private" not in result.text + caplog.text
