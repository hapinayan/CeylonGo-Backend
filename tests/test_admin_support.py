from datetime import datetime, timedelta
import socket

import pytest
from sqlalchemy import create_engine, event, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.models.provider_profile import ProviderProfile
from app.models.rental import RentalIncident
from app.models.rental_booking import RentalBooking
from app.models.rental_vehicle import RentalVehicle
from app.models.user import Base, User
from app.routers.admin_support import list_incident_triage
from tests.test_admin_dashboard import dashboard_sql
from tests.test_admin_rbac_matrix import _create_user_and_headers, client, db_session, role_headers


PATH = "/api/v1/admin/support/incidents"
MODULE = {"module": "rental_vehicle"}
BASE_DATE = datetime(2026, 1, 1)
STATUSES = ("reported", "under_investigation", "insurance_claimed", "customer_billed", "resolved", "dismissed")
TYPES = ("scratch", "dent", "accident", "mechanical_failure", "theft", "late_return", "traffic_fine")


def seed_incidents(db, user_id):
    profile = ProviderProfile(user_id=user_id, business_name="Private provider", provider_type="rental",
                              district="Kandy", phone="0771234567")
    db.add(profile)
    db.flush()
    vehicle = RentalVehicle(provider_id=profile.id, vehicle_type="bike", vehicle_name="Sensitive vehicle", price_per_day=100)
    db.add(vehicle)
    db.flush()
    booking = RentalBooking(vehicle_id=vehicle.id, user_id=user_id, pickup_at=BASE_DATE,
                            return_at=BASE_DATE + timedelta(days=1), total_price=100)
    db.add(booking)
    db.flush()
    for index, incident_type in enumerate(TYPES):
        date = BASE_DATE + timedelta(days=90 if index == 6 else 0)
        db.add(RentalIncident(
            booking_id=booking.id, incident_type=incident_type, incident_datetime=date,
            created_at=date, updated_at=date, resolution_status=STATUSES[index % len(STATUSES)],
            description="private@example.com", estimated_damage_cost=99, actual_repair_cost=50,
            police_report_reference="private-police-reference",
        ))
    db.commit()


@pytest.fixture
def incidents(db_session, role_headers):
    seed_incidents(db_session, db_session.query(User.id).filter_by(role="provider").scalar())


def incident_queries(statements):
    return [sql for sql in statements if "rental_incidents" in sql]


@pytest.mark.parametrize("role", ["super_admin", "admin", "operations_admin", "support_admin", "finance_admin",
                                     "content_moderator", "traveler", "provider"])
def test_triage_role_matrix(client, role_headers, dashboard_sql, role):
    dashboard_sql.clear()
    response = client.get(PATH, params=MODULE, headers=role_headers[role])
    if role in {"super_admin", "admin", "operations_admin", "support_admin"}:
        assert response.status_code == 200
        assert response.json() == {"module": "rental_vehicle", "items": [], "total": 0,
                                   "page": 1, "page_size": 20, "total_pages": 0}
    else:
        assert response.status_code == 403
        assert incident_queries(dashboard_sql) == []


def test_inactive_missing_and_invalid_auth_never_queries_incidents(client, db_session, dashboard_sql):
    _, inactive = _create_user_and_headers(db_session, email="inactive-support@example.com",
                                          role="support_admin", is_active=False)
    dashboard_sql.clear()
    assert client.get(PATH, params=MODULE, headers=inactive).status_code == 403
    assert client.get(PATH, params=MODULE).status_code == 401
    assert client.get(PATH, params=MODULE, headers={"Authorization": "Bearer invalid"}).status_code == 401
    assert incident_queries(dashboard_sql) == []


@pytest.mark.parametrize("params", [
    {}, {"module": ""}, {"module": "homestay"}, MODULE | {"incident_type": "other"},
    MODULE | {"resolution_status": "pending"}, MODULE | {"page": 0}, MODULE | {"page": 1_000_001},
    MODULE | {"page_size": 0}, MODULE | {"page_size": 101}, MODULE | {"page": "1.5"},
    MODULE | {"created_from": "2026-01-01"}, MODULE | {"created_from": "2026-01-01T00:00:00"},
    MODULE | {"created_from": "1767225600"}, MODULE | {"created_from": "2026-02-30T00:00:00Z"},
    MODULE | {"created_from": "2026-01-01T00:00:00-00:00"}, MODULE | {"created_from": "2026-01-01T00:00:00+01:99"},
    MODULE | {"created_from": "9999-12-31T00:00:00Z"},
    MODULE | {"created_from": "2026-01-01T00:00:00Z", "created_before": "2026-04-01T00:00:00.000001Z"},
    MODULE | {"created_from": "2026-01-02T00:00:00Z", "created_before": "2026-01-01T00:00:00Z"},
    MODULE | {"created_from": "2026-01-01T00:00:00Z", "created_before": "2026-01-01T05:30:00+05:30"},
])
def test_invalid_filters_fail_before_incident_query(client, role_headers, dashboard_sql, params):
    dashboard_sql.clear()
    assert client.get(PATH, params=params, headers=role_headers["support_admin"]).status_code == 422
    assert incident_queries(dashboard_sql) == []


def test_native_status_and_type_filters(client, role_headers, incidents):
    headers = role_headers["super_admin"]
    for status in STATUSES:
        response = client.get(PATH, params=MODULE | {"resolution_status": status}, headers=headers)
        assert response.status_code == 200
        assert {item["resolution_status"] for item in response.json()["items"]} == {status}
    for index, incident_type in enumerate(TYPES):
        response = client.get(PATH, params=MODULE | {"incident_type": incident_type,
                              "resolution_status": STATUSES[index % len(STATUSES)]}, headers=headers)
        assert response.status_code == 200
        assert [item["incident_type"] for item in response.json()["items"]] == [incident_type]


@pytest.mark.parametrize("filters", [
    {"created_from": "2026-01-01T05:30:00+05:30", "created_before": "2026-04-01T00:00:00Z"},
    {"created_from": "2026-01-01T00:00:00Z"}, {"created_before": "2026-04-01T00:00:00Z"},
])
def test_utc_boundary_and_one_sided_windows(client, role_headers, incidents, filters):
    result = client.get(PATH, params=MODULE | filters, headers=role_headers["operations_admin"])
    assert result.status_code == 200
    assert result.json()["total"] == 2
    assert [item["id"] for item in result.json()["items"]] == [1, 2]
    assert all(item["created_at"] == BASE_DATE.isoformat() for item in result.json()["items"])


def test_default_lane_pagination_projection_and_repeat_reads(client, role_headers, incidents, dashboard_sql, monkeypatch):
    def reject_outbound_connection(*_args, **_kwargs):
        pytest.fail("An incident triage read attempted an external connection")

    monkeypatch.setattr(socket.socket, "connect", reject_outbound_connection)
    monkeypatch.setattr(socket.socket, "connect_ex", reject_outbound_connection)
    headers = role_headers["admin"]
    dashboard_sql.clear()
    params = MODULE | {"page_size": 1}
    first = client.get(PATH, params=params, headers=headers).json()
    assert first == {"module": "rental_vehicle", "items": [{"id": 1, "booking_id": 1, "incident_type": "scratch",
                     "resolution_status": "reported", "created_at": BASE_DATE.isoformat(),
                     "updated_at": BASE_DATE.isoformat()}], "total": 3, "page": 1, "page_size": 1, "total_pages": 3}
    assert client.get(PATH, params=params, headers=headers).json() == first
    assert [client.get(PATH, params=params | {"page": page}, headers=headers).json()["items"][0]["id"]
            for page in (2, 3)] == [2, 7]
    last = client.get(PATH, params=params | {"page": 1_000_000}, headers=headers).json()
    assert last["items"] == [] and last["total"] == last["total_pages"] == 3
    wide = client.get(PATH, params=MODULE | {"page_size": 100}, headers=headers).json()
    assert wide["page_size"] == 100 and wide["total_pages"] == 1
    assert {item["resolution_status"] for item in wide["items"]} == {"reported", "under_investigation"}
    assert all(sql.startswith("select ") for sql in dashboard_sql)
    queries = incident_queries(dashboard_sql)
    assert len(queries) == 12
    for count, page in zip(queries[::2], queries[1::2], strict=True):
        assert count.split("where")[1].strip() == page.split("where")[1].split("order by")[0].strip()
        assert all(field not in page for field in ("description", "police", "cost", "email", "phone", "payment"))
        assert "rental_bookings" in page and "rental_vehicles" in page


def test_existing_incident_and_finance_contracts(client, role_headers, incidents):
    support, finance = role_headers["support_admin"], role_headers["finance_admin"]
    rental = "/api/v1/admin/rentals/incidents"
    assert client.get(rental, headers=support).json()["total"] == 7
    assert client.get(f"{rental}/1", headers=support).json()["description"] == "private@example.com"
    assert client.get(rental, headers=finance).status_code == 403
    refunds = "/api/v1/payments/admin/refunds"
    assert client.get(refunds, headers=finance).status_code == 200
    assert client.get(refunds, headers=support).status_code == 200  # Existing shared refund read.
    processing = f"{refunds}/99999/process"
    assert client.post(processing, headers=support, json={"status": "rejected"}).status_code == 403
    assert client.post(processing, headers=finance, json={"status": "rejected"}).status_code == 404


def test_database_failure_is_generic(client, db_session, role_headers, incidents, caplog):
    bind = db_session.get_bind()

    def fail(_conn, _cursor, statement, _parameters, _context, _many):
        if "FROM rental_incidents" in statement:
            raise OperationalError("private SQL statement", {}, RuntimeError("private record"))

    event.listen(bind, "before_cursor_execute", fail)
    try:
        response = client.get(PATH, params=MODULE, headers=role_headers["admin"])
    finally:
        event.remove(bind, "before_cursor_execute", fail)
    assert response.status_code == 500 and response.json() == {"detail": "Unable to load incident triage"}
    assert "private" not in response.text + caplog.text


def test_count_and_page_can_see_different_commits(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'triage.sqlite').as_posix()}")

    @event.listens_for(engine, "connect")
    def configure(connection, _record):
        connection.create_function("sysutcdatetime", 0, lambda: "2026-01-01 00:00:00")

    try:
        Base.metadata.create_all(engine)
        with Session(engine) as db:
            db.add(User(id=1, full_name="Provider", email="owner@example.com", role="provider"))
            db.flush()
            seed_incidents(db, 1)

        @event.listens_for(engine, "before_cursor_execute")
        def change_after_count(_conn, _cursor, statement, _parameters, _context, _many):
            if "FROM rental_incidents" in statement and "ORDER BY" in statement:
                with engine.begin() as writer:
                    writer.execute(update(RentalIncident).where(RentalIncident.id == 1).values(resolution_status="resolved"))

        with Session(engine) as reader:
            result = list_incident_triage(reader, "rental_vehicle", page=1, page_size=20)
        assert result.total == 3 and [item.id for item in result.items] == [2, 7]
    finally:
        engine.dispose()
