"""Offline checks for the Academic MVP manifest and fail-closed seeder."""

from collections import Counter
from datetime import date, datetime, timedelta
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.models import mvp
from scripts import seed_mvp
from scripts.mvp_seed_data import MOCK_INTEGRATIONS, PREFIX, digest

ANCHOR = date(2030, 6, 15)
DEMO_COUNTS = {
    "users": 10,
    "provider_profiles": 7,
    "listings": 7,
    "listing_media": 5,
    "inventory_units": 5,
    "provider_documents": 14,
    "availability_slots": 15,
    "bookings": 5,
    "booking_allocations": 5,
    "booking_status_history": 10,
    "booking_payments": 3,
    "community_posts": 2,
    "community_comments": 2,
    "community_post_likes": 1,
    "admin_audit_logs": 13,
}


@pytest.fixture
def engine():
    db = create_engine(
        "sqlite:///:memory:", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(db, "connect")
    def enable_foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    mvp.MVPBase.metadata.create_all(db)
    yield db
    db.dispose()


@pytest.fixture
def auth_artifacts(engine):
    now = datetime(2030, 6, 15)
    with Session(engine) as db, db.begin():
        user = mvp.MVPUser(email="auth-fixture@example.invalid", full_name="Auth Fixture",
                           hashed_password="fixture", role="traveler")
        db.add(user)
        db.flush()
        db.add_all([
            mvp.MVPPasswordReset(
                user_id=user.id, token_hash=digest("temporary-reset"), created_at=now,
                expires_at=now + timedelta(hours=1),
            ),
            mvp.MVPProviderEmailChallenge(
                email="verification@example.invalid", challenge_hash=digest("temporary-challenge"),
                created_at=now, expires_at=now + timedelta(minutes=5),
            ),
            mvp.MVPSchemaVersion(
                version="fixture", artifact_name="fixture.sql", sha256="0" * 64,
                applied_at=now, applied_by="isolated-test",
            ),
        ])


def seed(engine, password_factory=lambda role: f"hash-for-{role}", anchor=ANCHOR):
    with Session(engine) as db, db.begin():
        return seed_mvp.write_dataset(db, anchor, password_factory)


def counts(engine):
    with engine.connect() as db:
        return {
            table.name: db.scalar(select(func.count()).select_from(table))
            for table in mvp.MVPBase.metadata.sorted_tables
        }


def test_demo_coverage_and_dependency_consistency(engine):
    assert seed(engine) == {"inserted": sum(DEMO_COUNTS.values()), "verified": 0}
    assert counts(engine) == dict(
        DEMO_COUNTS, password_resets=0, provider_email_challenges=0, mvp_schema_versions=0,
    )
    assert set(MOCK_INTEGRATIONS) == {"TR-06", "TR-08", "TR-09"}
    with Session(engine) as db:
        public = db.scalars(select(mvp.MVPListing).filter_by(is_active=True)).all()
        assert {row.category for row in public} == {"homestay", "bike", "bicycle", "adventure", "guide"}
        assert all(row.approval_status == "approved" and row.latitude and row.longitude for row in public)
        for unit in db.scalars(select(mvp.MVPInventoryUnit)):
            assert unit.provider_id == db.get(mvp.MVPListing, unit.listing_id).provider_id
        for document in db.scalars(select(mvp.MVPProviderDocument)):
            if document.kind in {"revenue_license", "insurance"}:
                assert db.get(mvp.MVPInventoryUnit, document.unit_id).provider_id == document.provider_id
            else:
                assert document.unit_id is None
        for booking in db.scalars(select(mvp.MVPBooking)):
            listing = db.get(mvp.MVPListing, booking.listing_id)
            allocation = db.scalars(select(mvp.MVPBookingAllocation).filter_by(booking_id=booking.id)).one()
            slot = db.get(mvp.MVPAvailabilitySlot, allocation.slot_id)
            assert listing.id == allocation.listing_id == slot.listing_id
            assert slot.starts_at == booking.starts_at < booking.ends_at == slot.ends_at
            assert booking.quantity == allocation.quantity <= slot.capacity
            assert booking.total_amount == booking.quantity * listing.price
            assert bool(allocation.released_at) == (booking.status in {"cancelled", "rejected"})
            history = db.scalars(select(mvp.MVPBookingStatusHistory).filter_by(
                booking_id=booking.id,
            ).order_by(mvp.MVPBookingStatusHistory.occurred_at)).all()
            assert history[0].from_status is None and history[0].to_status == "pending"
            assert all(old.to_status == new.from_status for old, new in zip(history, history[1:]))
            assert history[-1].to_status == booking.status
        payments = db.scalars(select(mvp.MVPBookingPayment)).all()
        assert Counter((row.method, row.status) for row in payments) == {
            ("simulated", "demo_failed"): 1, ("simulated", "demo_succeeded"): 1,
            ("at_location", "unpaid"): 1,
        }
        for payment in payments:
            booking = db.get(mvp.MVPBooking, payment.booking_id)
            assert payment.display_amount == booking.total_amount
            assert bool(payment.resolved_at) == (payment.method == "simulated")
        for audit in db.scalars(select(mvp.MVPAdminAuditLog)):
            assert db.get(mvp.MVPUser, audit.actor_id).role == "admin"
            assert sum(value is not None for value in (
                audit.subject_user_id, audit.subject_provider_id,
                audit.subject_listing_id, audit.subject_post_id,
            )) == 1


def test_idempotency_preserves_credentials_and_test_artifacts(engine, auth_artifacts):
    seed(engine)
    with Session(engine) as db, db.begin():
        user = db.scalars(select(mvp.MVPUser).filter_by(role="admin")).one()
        user.hashed_password = "rotated-hash"
        user.auth_version = 2
        user.credential_version = 1
        user.updated_at = datetime(2030, 6, 16)
    before = counts(engine)
    password_factory = Mock(side_effect=AssertionError("Reruns must not hash passwords"))
    assert seed(engine, password_factory) == {"inserted": 0, "verified": sum(DEMO_COUNTS.values())}
    password_factory.assert_not_called()
    assert counts(engine) == before
    assert before["password_resets"] == before["provider_email_challenges"] == 1
    with Session(engine) as db:
        user = db.scalars(select(mvp.MVPUser).filter_by(role="admin")).one()
        assert (user.hashed_password, user.auth_version, user.credential_version) == ("rotated-hash", 2, 1)
        assert user.updated_at == datetime(2030, 6, 16)
        assert db.get(mvp.MVPSchemaVersion, "fixture").sha256 == "0" * 64


def test_case_insensitive_account_collision_preserves_existing_row(engine):
    email = f"TRAVELER-ONE@{PREFIX.upper()}.INVALID"
    with Session(engine) as db, db.begin():
        db.add(mvp.MVPUser(email=email, full_name="Existing User", hashed_password="existing",
                           role="admin"))
    before = counts(engine)
    with pytest.raises(seed_mvp.SeedError, match="collision"):
        seed(engine)
    assert counts(engine) == before
    with Session(engine) as db:
        user = db.scalars(select(mvp.MVPUser)).one()
        assert (user.email, user.full_name, user.hashed_password, user.role) == (
            email, "Existing User", "existing", "admin",
        )


def test_anchor_drift_aborts_without_changes(engine):
    seed(engine)
    before = counts(engine)
    with pytest.raises(seed_mvp.SeedError, match="drift"):
        seed(engine, anchor=ANCHOR + timedelta(days=1))
    assert counts(engine) == before


def test_seeded_data_through_mvp_routes(engine):
    from fastapi.testclient import TestClient

    from app.core.security import hash_password
    from app.database.mvp_session import get_mvp_db
    from app.main_mvp import create_mvp_app

    password = "offline-fixture-password"
    hashed = hash_password(password)
    seed(engine, lambda role: hashed)
    app = create_mvp_app()

    def get_isolated_db():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_mvp_db] = get_isolated_db
    with TestClient(app) as client:
        login = client.post("/api/v1/auth/login", json={
            "email": f"traveler-one@{PREFIX}.invalid", "password": password,
        })
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        response = client.get("/api/v1/bookings/my", headers=headers)
        assert response.status_code == 200
        assert {booking["status"] for booking in response.json()} == {
            "pending", "completed", "confirmed",
        }
        response = client.get("/api/v1/listings")
        assert response.status_code == 200
        assert len(response.json()) == 5
        response = client.post("/api/v1/itinerary/generate", json=MOCK_INTEGRATIONS["TR-06"])
        assert response.status_code == 200
        assert len(response.json()["days"]) == 2


def test_late_database_failure_rolls_back_every_insert(engine):
    with engine.begin() as db:
        db.exec_driver_sql("""
            CREATE TRIGGER fail_audit BEFORE INSERT ON admin_audit_logs
            BEGIN SELECT RAISE(ABORT, 'injected failure'); END
        """)
    with pytest.raises(IntegrityError, match="injected failure"):
        seed(engine)
    assert not any(counts(engine).values())


@pytest.mark.parametrize("table, mutation", [
    (mvp.MVPListing, {"price": -1}),
    (mvp.MVPInventoryUnit, {"capacity": 0}),
    (mvp.MVPBookingPayment, {"method": "at_location", "status": "demo_succeeded"}),
    (mvp.MVPCommunityPostLike, {"user_id": 999999}),
])
def test_orm_constraints_reject_invalid_rows(engine, table, mutation):
    seed(engine)
    with pytest.raises(IntegrityError):
        with Session(engine) as db, db.begin():
            row = db.scalars(select(table)).first()
            for key, value in mutation.items():
                setattr(row, key, value)


@pytest.mark.parametrize("server,database", [
    ("localhost\\CEYLONGOSEEDTEST", "CeylonGoDB"),
    ("localhost\\CEYLONGOSEEDTEST", "CeylonGoDB_Production"),
    ("localhost\\CEYLONGOSEEDTEST", "CeylonGoDB_MVP_Rehearsal"),
    ("remote.example.invalid", seed_mvp.APPROVED_TARGET[1]),
    ("", ""),
])
def test_unapproved_targets_fail_before_engine_creation(monkeypatch, server, database):
    factory = Mock()
    monkeypatch.setattr(seed_mvp, "create_engine", factory)
    with pytest.raises(seed_mvp.SeedError, match="allowlist"):
        seed_mvp.run_sql_server(server, database, None, None, None, ANCHOR, Mock())
    factory.assert_not_called()


def test_missing_credentials_fail_before_engine_creation(monkeypatch):
    factory = Mock()
    monkeypatch.setattr(seed_mvp, "create_engine", factory)
    with pytest.raises(seed_mvp.SeedError, match="credentials"):
        seed_mvp.run_sql_server(*seed_mvp.APPROVED_TARGET, None, None, None, ANCHOR, Mock())
    factory.assert_not_called()


def test_wrong_connected_identity_stops_before_lock():
    session = Mock()
    session.execute.return_value.one.return_value = (
        "CeylonGoDB", "seed_user", "seed_user", "test-instance", 0, 0,
    )
    with pytest.raises(seed_mvp.SeedError, match="verification"):
        seed_mvp.verify_sql_server(
            session, seed_mvp.APPROVED_TARGET[1], "seed_user", "test-instance",
        )
    session.execute.assert_called_once()


def test_cli_suppresses_driver_diagnostics(monkeypatch, capsys):
    monkeypatch.setattr(seed_mvp, "run_sql_server", Mock(side_effect=RuntimeError("secret")))
    server, database = seed_mvp.APPROVED_TARGET
    assert seed_mvp.main([
        "--server", server, "--database", database, "--instance-identity", "local",
        "--anchor", ANCHOR.isoformat(),
    ]) == 1
    assert "secret" not in capsys.readouterr().err


@pytest.fixture
def sql_preflight(monkeypatch):
    """Model catalog responses without importing a SQL Server driver."""
    from types import SimpleNamespace
    import re
    from hashlib import sha256

    server, database = seed_mvp.APPROVED_TARGET
    login, instance = "seed_user", "test-instance"
    ddl = seed_mvp.DDL.read_text(encoding="utf-8")
    responses = {
        "identity": Mock(one=Mock(return_value=(database, login, login, instance, 0, 0, 0, 0, 0, 1))),
        "lock": Mock(scalar_one=Mock(return_value=0)),
        "objects": Mock(scalars=Mock(return_value=set(re.findall(
            r"\b(?:CONSTRAINT|INDEX)\s+(\w+)", ddl, re.I,
        )))),
        "constraints": Mock(scalars=Mock(return_value=[])),
        "triggers": Mock(first=Mock(return_value=None)),
        "ledger": Mock(all=Mock(return_value=[
            ("1.0.0", seed_mvp.DDL.name, sha256(ddl.encode("utf-8")).hexdigest()),
        ])),
    }
    catalog = Mock()
    catalog.get_table_names.return_value = list(mvp.MVPBase.metadata.tables)
    catalog.get_columns.side_effect = lambda name, schema: [
        {"name": column.name} for column in mvp.MVPBase.metadata.tables[name].columns
    ]
    monkeypatch.setattr(seed_mvp, "inspect", Mock(return_value=catalog))
    session = Mock()

    def execute(statement):
        sql = str(statement)
        for fragment, key in (
            ("ORIGINAL_LOGIN", "identity"), ("sp_getapplock", "lock"),
            ("sys.objects", "objects"), ("sys.foreign_keys", "constraints"),
            ("sys.triggers", "triggers"), ("dbo.mvp_schema_versions", "ledger"),
        ):
            if fragment in sql:
                return responses[key]
        raise AssertionError(f"Unexpected SQL: {sql}")

    session.execute.side_effect = execute
    return SimpleNamespace(
        responses=responses, catalog=catalog, session=session,
        verify=lambda: seed_mvp.verify_sql_server(session, database, login, instance),
    )


def test_baseline_sql_server_preflight(sql_preflight):
    sql_preflight.verify()


@pytest.mark.parametrize("index,value", [(4, 1), (5, 1), (6, 1), (7, 1), (8, 1), (9, 0)])
def test_privileged_or_metadata_blind_principal_stops_before_lock(sql_preflight, index, value):
    identity = sql_preflight.responses["identity"].one
    row = list(identity.return_value)
    row[index] = value
    identity.return_value = tuple(row)
    with pytest.raises(seed_mvp.SeedError, match="principal"):
        sql_preflight.verify()
    sql_preflight.session.execute.assert_called_once()


def test_application_lock_failure_stops_before_catalog_reads(sql_preflight):
    sql_preflight.responses["lock"].scalar_one.return_value = -1
    with pytest.raises(seed_mvp.SeedError, match="seed lock"):
        sql_preflight.verify()
    sql_preflight.catalog.get_table_names.assert_not_called()


@pytest.mark.parametrize("drift,message", [
    ("table", "table set"),
    ("column", "column set"),
    ("object", "missing expected schema"),
    ("constraint", "disabled or untrusted"),
    ("trigger", "enabled DML trigger"),
    ("checksum", "ledger"),
    ("future-version", "ledger"),
])
def test_sql_server_schema_drift_is_rejected(sql_preflight, drift, message):
    responses = sql_preflight.responses
    if drift == "table":
        sql_preflight.catalog.get_table_names.return_value.append("legacy_table")
    elif drift == "column":
        sql_preflight.catalog.get_columns.side_effect = None
        sql_preflight.catalog.get_columns.return_value = [{"name": "extra_column"}]
    elif drift == "object":
        responses["objects"].scalars.return_value.remove("uq_booking_payments_succeeded")
    elif drift == "constraint":
        responses["constraints"].scalars.return_value = ["ck_bookings_status"]
    elif drift == "trigger":
        responses["triggers"].first.return_value = ("unexpected_trigger",)
    elif drift == "checksum":
        responses["ledger"].all.return_value[0] = ("1.0.0", seed_mvp.DDL.name, "0" * 64)
    else:
        responses["ledger"].all.return_value.append(("1.0.1", "future.sql", "1" * 64))
    with pytest.raises(seed_mvp.SeedError, match=message):
        sql_preflight.verify()


def test_escaped_local_server_is_canonicalized_before_engine_creation(monkeypatch):
    factory = Mock(side_effect=RuntimeError("stop before engine creation"))
    monkeypatch.setattr(seed_mvp, "create_engine", factory)
    server, database = seed_mvp.APPROVED_TARGET
    with pytest.raises(RuntimeError, match="stop before engine"):
        seed_mvp.run_sql_server(server.replace("\\", "\\\\"), database, "seed_user",
                                "sql-password", "test-instance", ANCHOR, Mock())
    assert factory.call_args.args[0].host == server


@pytest.mark.parametrize("password", [None, "elevenchars"])
def test_cli_rejects_missing_or_short_demo_password(monkeypatch, capsys, password):
    if password is None:
        monkeypatch.delenv("MVP_SEED_ADMIN_PASSWORD", raising=False)
    else:
        monkeypatch.setenv("MVP_SEED_ADMIN_PASSWORD", password)

    def run_without_sql(server, database, login, db_password, instance, anchor, password_factory):
        password_factory("admin")
        pytest.fail("An invalid demo password was accepted")

    monkeypatch.setattr(seed_mvp, "run_sql_server", run_without_sql)
    server, database = seed_mvp.APPROVED_TARGET
    assert seed_mvp.main([
        "--server", server, "--database", database, "--instance-identity", "local",
        "--anchor", ANCHOR.isoformat(),
    ]) == 1
    error = capsys.readouterr().err
    assert "MVP_SEED_ADMIN_PASSWORD" in error
    if password:
        assert password not in error
