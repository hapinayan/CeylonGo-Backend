"""
Tests for Database Identity Guard and OpenAPI Schema Integrity
=============================================================
Verifies fail-closed database safety enforcement and API route/operation uniqueness.
"""

from decimal import Decimal
import pytest
from pydantic import ValidationError

from app.core.database_guard import (
    verify_safe_integration_database,
    verify_safe_integration_server,
    verify_safe_integration_target,
    verify_connected_server_identity,
    DatabaseSafetyError,
    APPROVED_INTEGRATION_DATABASES,
    APPROVED_INTEGRATION_SERVERS,
    PROTECTED_DATABASES,
    DISALLOWED_INTEGRATION_SERVERS,
)
from app.main import app
from app.schemas.homestay import HomestayCreateRequest, HomestayUpdateRequest, HomestayImageInput


# ==============================================================================
# Database Identity Guard Tests
# ==============================================================================

def test_guard_rejects_empty_or_whitespace_database_names():
    for empty_val in [None, "", "   ", "\t\n"]:
        with pytest.raises(DatabaseSafetyError) as exc_info:
            verify_safe_integration_database(empty_val)
        assert "target database name is unspecified or empty" in str(exc_info.value).lower()


def test_guard_strictly_rejects_live_ceylongodb():
    for live_variant in ["CeylonGoDB", "ceylongodb", "CEYLONGODB", "  CeylonGoDB  "]:
        with pytest.raises(DatabaseSafetyError) as exc_info:
            verify_safe_integration_database(live_variant)
        assert "live database safety violation" in str(exc_info.value).lower()


def test_guard_rejects_unapproved_databases():
    unapproved = ["master", "tempdb", "model", "msdb", "ProductionDB", "TestDB", "CeylonGoDB_Dev", "CeylonGoDB_Rehearsal"]
    for db_name in unapproved:
        with pytest.raises(DatabaseSafetyError) as exc_info:
            verify_safe_integration_database(db_name)
        assert "unapproved integration database" in str(exc_info.value).lower()


def test_guard_permits_approved_integration_database():
    valid_variants = [
        "CeylonGoDB_IntegrationTest",
        "ceylongodb_integrationtest",
        "CEYLONGODB_INTEGRATIONTEST",
        "  CeylonGoDB_IntegrationTest  ",
    ]
    for db_name in valid_variants:
        assert verify_safe_integration_database(db_name) is True


def test_guard_custom_allowlist_preserves_live_db_rejection():
    # Even if someone accidentally adds CeylonGoDB to a custom allowlist, it MUST still fail closed
    with pytest.raises(DatabaseSafetyError) as exc_info:
        verify_safe_integration_database("CeylonGoDB", approved_databases={"CeylonGoDB", "OtherDB"})
    assert "live database safety violation" in str(exc_info.value).lower()

    # Valid custom allowlist works for other explicit databases
    assert verify_safe_integration_database("Isolated_Rehearsal", approved_databases={"Isolated_Rehearsal"}) is True


def test_guard_rejects_empty_or_whitespace_server_names():
    for empty_val in [None, "", "   ", "\t\n"]:
        with pytest.raises(DatabaseSafetyError) as exc_info:
            verify_safe_integration_server(empty_val)
        assert "target server instance is unspecified or empty" in str(exc_info.value).lower()


def test_guard_rejects_disallowed_and_unapproved_servers():
    disallowed = [
        r".\SQLEXPRESS",
        r"DESKTOP-JL1BGSI\SQLEXPRESS",
        r"localhost\SQLEXPRESS",
        r"127.0.0.1\SQLEXPRESS",
        r"localhost",
        r"127.0.0.1",
        r".",
        r"PROD_SQL_SERVER",
        r"staging.database.windows.net",
    ]
    for srv in disallowed:
        with pytest.raises(DatabaseSafetyError) as exc_info:
            verify_safe_integration_server(srv)
        assert any(k in str(exc_info.value).lower() for k in ["disallowed integration server", "unapproved integration server"])


def test_guard_permits_approved_integration_servers():
    approved = [
        r"DESKTOP-JL1BGSI\SQLEXPRESS01",
        r"desktop-jl1bgsi\sqlexpress01",
        r"  DESKTOP-JL1BGSI\SQLEXPRESS01  ",
        r".\SQLEXPRESS01",
        r".\sqlexpress01",
        r"./sqlexpress01",
        r"localhost\SQLEXPRESS01",
        r"localhost/sqlexpress01",
        r"127.0.0.1\SQLEXPRESS01",
    ]
    for srv in approved:
        assert verify_safe_integration_server(srv) is True


def test_guard_rejects_protected_databases_including_logintest_and_system():
    for protected in ["CeylonGoDB", "CeylonGoDB_LoginTest", "ceylongodb_logintest", "master", "tempdb", "model", "msdb"]:
        with pytest.raises(DatabaseSafetyError) as exc_info:
            verify_safe_integration_database(protected)
        assert any(k in str(exc_info.value).lower() for k in ["live database safety violation", "protected database safety violation", "unapproved integration database"])


def test_guard_verify_safe_integration_target_with_opt_in():
    # Valid target with valid opt-in
    assert verify_safe_integration_target(
        r"DESKTOP-JL1BGSI\SQLEXPRESS01",
        "ceylongodb_integrationtest",
        opt_in="1",
    ) is True

    with pytest.raises(DatabaseSafetyError, match="INTEGRATION OPT-IN REQUIRED"):
        verify_safe_integration_target(r".\SQLEXPRESS01", "CeylonGoDB_IntegrationTest")

    # Disabled opt-in
    with pytest.raises(DatabaseSafetyError) as exc_info:
        verify_safe_integration_target(
            r"DESKTOP-JL1BGSI\SQLEXPRESS01",
            "ceylongodb_integrationtest",
            opt_in="0",
        )
    assert "integration opt-in required" in str(exc_info.value).lower()

    # Disallowed server with valid db
    with pytest.raises(DatabaseSafetyError):
        verify_safe_integration_target(
            r".\SQLEXPRESS",
            "ceylongodb_integrationtest",
            opt_in="1",
        )

    # Valid server with protected db
    with pytest.raises(DatabaseSafetyError):
        verify_safe_integration_target(
            r"DESKTOP-JL1BGSI\SQLEXPRESS01",
            "CeylonGoDB",
            opt_in="1",
        )


def test_verify_connected_server_identity_with_mock():
    from unittest.mock import MagicMock

    # Valid identity
    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_engine.connect.return_value.__enter__.return_value = mock_conn
    mock_conn.execute.return_value.fetchone.return_value = (
        "ceylongodb_integrationtest",
        "DESKTOP-JL1BGSI\\SQLEXPRESS01",
        "DESKTOP-JL1BGSI",
        "SQLEXPRESS01",
    )
    ident = verify_connected_server_identity(mock_engine)
    assert ident["current_db"] == "ceylongodb_integrationtest"
    assert ident["instance_name"] == "SQLEXPRESS01"

    mock_conn.execute.return_value.fetchone.return_value = None
    with pytest.raises(DatabaseSafetyError, match="identity is unavailable"):
        verify_connected_server_identity(mock_engine)

    # Connected to CeylonGoDB -> raises
    mock_conn.execute.return_value.fetchone.return_value = (
        "CeylonGoDB",
        "DESKTOP-JL1BGSI\\SQLEXPRESS01",
        "DESKTOP-JL1BGSI",
        "SQLEXPRESS01",
    )
    with pytest.raises(DatabaseSafetyError) as exc_info:
        verify_connected_server_identity(mock_engine)
    assert "live identity safety violation" in str(exc_info.value).lower()

    # Connected to SQLEXPRESS (not SQLEXPRESS01) -> raises
    mock_conn.execute.return_value.fetchone.return_value = (
        "ceylongodb_integrationtest",
        "DESKTOP-JL1BGSI\\SQLEXPRESS",
        "DESKTOP-JL1BGSI",
        "SQLEXPRESS",
    )
    with pytest.raises(DatabaseSafetyError) as exc_info:
        verify_connected_server_identity(mock_engine)
    assert "live identity safety violation" in str(exc_info.value).lower()


@pytest.mark.parametrize(
    "alias", [r"localhost\SQLEXPRESS", r".\SQLEXPRESS01"]
)
def test_guard_rejects_live_database_on_both_instances(alias):
    with pytest.raises(DatabaseSafetyError, match="LIVE DATABASE SAFETY VIOLATION"):
        verify_safe_integration_target(alias, "CeylonGoDB", opt_in="1")


def test_guard_authorizes_only_the_correct_artisan_alias_and_database():
    alias = r"localhost\SQLEXPRESS"
    assert verify_safe_integration_target(alias, "CeylonGoDB_IntegrationTest", "1")
    with pytest.raises(DatabaseSafetyError):
        verify_safe_integration_target(alias, "CeylonGoDB_Artisan_Test", "1")
    with pytest.raises(DatabaseSafetyError):
        verify_safe_integration_target(r"127.0.0.1\SQLEXPRESS", "CeylonGoDB_IntegrationTest", "1")


@pytest.mark.parametrize(
    ("database", "server", "machine", "instance"),
    [
        ("CeylonGoDB", r"VISHNUGA-KAJARASH\SQLEXPRESS", "VISHNUGA-KAJARASH", "SQLEXPRESS"),
        ("CeylonGoDB", r"DESKTOP-JL1BGSI\SQLEXPRESS01", "DESKTOP-JL1BGSI", "SQLEXPRESS01"),
        ("OtherTest", r"VISHNUGA-KAJARASH\SQLEXPRESS", "VISHNUGA-KAJARASH", "SQLEXPRESS"),
        ("CeylonGoDB_IntegrationTest", r"OTHER\SQLEXPRESS", "OTHER", "SQLEXPRESS"),
        ("CeylonGoDB_IntegrationTest", r"VISHNUGA-KAJARASH\SQLEXPRESS", "OTHER", "SQLEXPRESS"),
        ("CeylonGoDB_IntegrationTest", r"VISHNUGA-KAJARASH\SQLEXPRESS", "VISHNUGA-KAJARASH", "SQLEXPRESS01"),
    ],
)
def test_failed_artisan_identity_prevents_migration_mutation(
    monkeypatch, tmp_path, database, server, machine, instance,
):
    from unittest.mock import MagicMock

    from scripts import migrate

    # Raw ODBC connection limit the fake to methods on that interface.
    connection = MagicMock(spec=["cursor", "commit", "rollback", "close"])
    cursor = connection.cursor.return_value
    row = (database, server, machine, instance)
    cursor.fetchone.return_value = row
    cursor.execute.return_value.fetchone.return_value = row
    monkeypatch.setattr(migrate, "get_connection", lambda config: connection)
    lock = MagicMock()
    monkeypatch.setattr(migrate, "migration_lock", lock)
    cfg = migrate.DatabaseConfig(
        server=r"localhost\SQLEXPRESS",
        database="CeylonGoDB_IntegrationTest",
        integration_opt_in="1",
    )
    with pytest.raises((DatabaseSafetyError, migrate.TargetDatabaseMismatchError)):
        migrate.cmd_upgrade(cfg, tmp_path, target_version="038", confirmed=True)
    lock.assert_not_called()
    connection.commit.assert_not_called()
    connection.close.assert_called_once()
    assert all(call.args[0].lstrip().startswith("SELECT ") for call in cursor.execute.call_args_list)


def test_guard_accepts_exact_connected_artisan_identity():
    from unittest.mock import MagicMock

    connection = MagicMock(spec=["cursor"])
    connection.cursor.return_value.execute.return_value.fetchone.return_value = (
        "CeylonGoDB_IntegrationTest",
        r"VISHNUGA-KAJARASH\SQLEXPRESS",
        "VISHNUGA-KAJARASH",
        "SQLEXPRESS",
    )
    identity = verify_connected_server_identity(
        connection, server_alias=r"localhost\SQLEXPRESS"
    )
    assert identity["server_name"] == r"VISHNUGA-KAJARASH\SQLEXPRESS"


# ==============================================================================
# OpenAPI Schema & Route Integrity Tests
# ==============================================================================

def test_openapi_operation_ids_are_strictly_unique():
    schema = app.openapi()
    paths = schema.get("paths", {})
    assert len(paths) > 0

    operation_ids = []
    seen_ops = {}
    for path, methods in paths.items():
        for method, details in methods.items():
            if method.lower() in {"get", "post", "put", "delete", "patch", "options", "head"}:
                op_id = details.get("operationId")
                assert op_id, f"Missing operationId for {method.upper()} {path}"
                if op_id in seen_ops:
                    pytest.fail(f"Duplicate operationId '{op_id}' found in {method.upper()} {path} (already in {seen_ops[op_id]})")
                seen_ops[op_id] = f"{method.upper()} {path}"
                operation_ids.append(op_id)

    assert len(operation_ids) == len(set(operation_ids)), "Duplicate operation IDs detected across endpoints"

    # Robust API contract coverage: verify all essential production endpoints exist
    required_core_paths = [
        "/api/v1/health",
        "/api/v1/auth/login",
        "/api/v1/auth/register",
        "/api/v1/auth/me",
        "/api/v1/homestays",
        "/api/v1/homestays/mine",
        "/api/v1/homestays/{homestay_id}",
        "/api/v1/homestays/{homestay_id}/availability",
        "/api/v1/homestays/{homestay_id}/price-preview",
        "/api/v1/bookings",
        "/api/v1/bookings/me",
        "/api/v1/homestay-promotions",
        "/api/v1/homestay-promotions/mine",
        "/api/v1/homestay-promotions/{promotion_id}",
        "/api/v1/artisan-listings",
        "/api/v1/showcase-items",
        "/api/v1/promotions",
        "/api/v1/admin/providers",
        "/api/v1/providers/registration/submit",
    ]
    for required_path in required_core_paths:
        assert required_path in paths, f"Missing required API endpoint: {required_path}"

    assert len(paths) >= 48, f"Expected at least 48 production API paths, found {len(paths)}"


def extract_all_route_endpoints(app_instance):
    """
    Recursively extracts all (method, path) endpoints registered across FastAPI app.routes,
    traversing Starlette Route, APIRoute, APIRouter, and FastAPI 0.160+ _IncludedRouter.
    """
    endpoints = []

    def _recurse(routes, parent_prefix=""):
        for route in routes:
            if hasattr(route, "effective_candidates"):
                inc_prefix = getattr(getattr(route, "include_context", None), "prefix", "")
                candidates = route.effective_candidates()
                _recurse(candidates, inc_prefix or parent_prefix)
            elif hasattr(route, "original_router") and hasattr(route.original_router, "routes"):
                router_prefix = getattr(route.original_router, "prefix", "")
                _recurse(route.original_router.routes, router_prefix or parent_prefix)
            elif hasattr(route, "routes"):
                router_prefix = getattr(route, "prefix", "")
                _recurse(route.routes, router_prefix or parent_prefix)
            elif hasattr(route, "methods") and hasattr(route, "path"):
                path = route.path
                if parent_prefix and not path.startswith(parent_prefix):
                    path = parent_prefix.rstrip("/") + "/" + path.lstrip("/")
                for method in route.methods:
                    if method not in {"HEAD", "OPTIONS"}:
                        endpoints.append((method, path))

    _recurse(app_instance.routes)
    return endpoints


def test_no_duplicate_router_registration():
    # Direct route table inspection: assert that each (method, path) combination is registered exactly once
    registered_endpoints = extract_all_route_endpoints(app)
    assert len(registered_endpoints) >= 60, f"Expected at least 60 total endpoints, found {len(registered_endpoints)}"

    seen_pairs = set()
    duplicates = []
    for pair in registered_endpoints:
        if pair in seen_pairs:
            duplicates.append(pair)
        seen_pairs.add(pair)

    assert not duplicates, f"Found duplicate route registrations in app.routes: {duplicates}"

    # Verify key endpoints are registered with expected HTTP methods
    assert ("GET", "/api/v1/homestays/{homestay_id}/availability") in seen_pairs
    assert ("GET", "/api/v1/homestays/{homestay_id}/price-preview") in seen_pairs
    assert ("POST", "/api/v1/bookings") in seen_pairs
    assert ("GET", "/api/v1/bookings/me") in seen_pairs
    assert ("GET", "/api/v1/homestay-promotions") in seen_pairs
    assert ("POST", "/api/v1/homestay-promotions") in seen_pairs
    assert ("GET", "/api/v1/homestay-promotions/mine") in seen_pairs
    assert ("PUT", "/api/v1/homestay-promotions/{promotion_id}") in seen_pairs
    assert ("DELETE", "/api/v1/homestay-promotions/{promotion_id}") in seen_pairs

    # Confirm that duplicate detection would genuinely fail if a router were registered twice
    from fastapi import FastAPI, APIRouter
    sim_app = FastAPI()
    sim_router = APIRouter(prefix="/simulation")
    @sim_router.get("/dup-check")
    def _sim_endpoint():
        return {"status": "ok"}
    sim_app.include_router(sim_router)
    sim_app.include_router(sim_router)

    sim_endpoints = extract_all_route_endpoints(sim_app)
    sim_dups = [x for i, x in enumerate(sim_endpoints) if x in sim_endpoints[:i]]
    assert len(sim_dups) == 1 and sim_dups[0] == ("GET", "/simulation/dup-check"), "Duplicate router simulation did not detect duplicate"


# ==============================================================================
# Duplicate Image URL & Validation Tests
# ==============================================================================

def test_duplicate_image_urls_rejected_in_schema():
    # Valid baseline payload matching HomestayCreateRequest schema exactly (no forbidden extra fields)
    valid_payload = {
        "name": "Seaside Villa",
        "description": "A coastal retreat",
        "location": "Mirissa Beach Road",
        "price_per_night": Decimal("5000.00"),
        "max_guests": 4,
        "amenities": ["WiFi"],
        "images": [
            HomestayImageInput(image_url="https://example.com/img1.jpg", is_primary=True),
            HomestayImageInput(image_url="https://example.com/img2.jpg", is_primary=False),
        ]
    }
    baseline = HomestayCreateRequest(**valid_payload)
    assert baseline.name == "Seaside Villa"
    assert len(baseline.images) == 2

    # Introduce duplicate image URLs into payload
    duplicate_payload = valid_payload.copy()
    duplicate_payload["images"] = [
        HomestayImageInput(image_url="https://example.com/img1.jpg", is_primary=True),
        HomestayImageInput(image_url="https://example.com/img1.jpg", is_primary=False),
    ]
    with pytest.raises(ValidationError) as exc_info:
        HomestayCreateRequest(**duplicate_payload)
    assert "duplicate image urls are not allowed" in str(exc_info.value).lower()


def test_empty_image_url_rejected_in_schema():
    # Valid baseline payload
    valid_payload = {
        "name": "Seaside Villa",
        "description": "A coastal retreat",
        "location": "Mirissa Beach Road",
        "price_per_night": Decimal("5000.00"),
        "max_guests": 4,
        "amenities": ["WiFi"],
    }
    with pytest.raises(ValidationError) as exc_info:
        HomestayCreateRequest(
            **valid_payload,
            images=[
                HomestayImageInput(image_url="   ", is_primary=True),
            ]
        )
    assert "image url cannot be empty" in str(exc_info.value).lower()
