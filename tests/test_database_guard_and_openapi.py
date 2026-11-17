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
    DatabaseSafetyError,
    APPROVED_INTEGRATION_DATABASES,
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
