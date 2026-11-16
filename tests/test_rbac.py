"""
Automated tests for Role-Based Access Control (RBAC) and security dependencies.

Tests covered:
1. Unauthenticated requests (no token / invalid token).
2. Traveler attempting Provider access.
3. Pending Provider accessing onboarding endpoints.
4. Pending Provider attempting approved-only access.
5. Rejected Provider attempting approved-only access.
6. Approved Provider accessing approved-only functionality.
7. Non-Admin attempting Admin access (Traveler & Provider).
8. Admin accessing Admin-only functionality.
9. Inactive users being rejected.
10. Provider without a profile record attempting approved-only access.
"""

from datetime import UTC, datetime
from typing import Annotated

import pytest
from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.dependencies import (
    require_admin,
    require_approved_provider,
    require_provider,
)
from app.core.security import create_access_token
from app.database.connection import get_db
from app.main import app
from app.models.provider_profile import ProviderProfile
from app.models.user import Base, User

# In-memory SQLite engine for tests
test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


@event.listens_for(test_engine, "connect")
def configure_sqlite_connection(dbapi_connection, connection_record):
    dbapi_connection.create_function(
        "sysutcdatetime",
        0,
        lambda: datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"),
    )


TestingSessionLocal = sessionmaker(
    bind=test_engine, autoflush=False, expire_on_commit=False
)

# Test router to mount RBAC-protected test endpoints
rbac_test_router = APIRouter(prefix="/api/test-rbac", tags=["Test RBAC"])


@rbac_test_router.get("/provider-endpoint")
def provider_only_route(current_user: Annotated[User, Depends(require_provider)]):
    return {
        "status": "success",
        "message": f"Welcome provider {current_user.full_name}",
        "user_id": current_user.id,
    }


@rbac_test_router.get("/approved-provider-endpoint")
def approved_provider_only_route(
    profile: Annotated[ProviderProfile, Depends(require_approved_provider)],
):
    return {
        "status": "success",
        "message": f"Welcome approved provider {profile.business_name}",
        "profile_id": profile.id,
        "approval_status": profile.approval_status,
    }


@rbac_test_router.get("/admin-endpoint")
def admin_only_route(current_admin: Annotated[User, Depends(require_admin)]):
    return {
        "status": "success",
        "message": f"Welcome admin {current_admin.full_name}",
        "user_id": current_admin.id,
    }


# Include test router on app once
if not getattr(app.state, "rbac_test_routes_mounted", False):
    app.include_router(rbac_test_router)
    app.state.rbac_test_routes_mounted = True


@pytest.fixture(autouse=True)
def setup_rbac_test_db():
    Base.metadata.create_all(bind=test_engine)
    yield
    Base.metadata.drop_all(bind=test_engine)


@pytest.fixture
def db_session():
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(db_session):
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    test_client = TestClient(app)
    yield test_client
    app.dependency_overrides.clear()


def make_auth_header(user_id: int) -> dict[str, str]:
    token = create_access_token(data={"sub": str(user_id)})
    return {"Authorization": f"Bearer {token}"}


# ==============================================================================
# 1. Unauthenticated Requests
# ==============================================================================


def test_unauthenticated_request_rejected(client: TestClient):
    """Test 1: Requests without credentials must be rejected with 401 Unauthorized."""
    endpoints = [
        "/api/test-rbac/provider-endpoint",
        "/api/test-rbac/approved-provider-endpoint",
        "/api/test-rbac/admin-endpoint",
    ]
    for url in endpoints:
        response = client.get(url)
        assert response.status_code == 401
        assert "not authenticated" in response.json()["detail"].lower()


def test_invalid_token_rejected(client: TestClient):
    """Test 1b: Requests with malformed or invalid token must return 401."""
    headers = {"Authorization": "Bearer completely-invalid-jwt-token"}
    response = client.get("/api/test-rbac/provider-endpoint", headers=headers)
    assert response.status_code == 401
    assert "could not validate credentials" in response.json()["detail"].lower()


# ==============================================================================
# 2. Traveler Attempting Provider Access
# ==============================================================================


def test_traveler_attempting_provider_access_forbidden(
    client: TestClient, db_session: Session
):
    """Test 2: A traveler attempting provider endpoints receives 403 Forbidden."""
    traveler = User(
        full_name="Traveler User",
        email="traveler@example.com",
        password_hash="argon2_hashed",
        role="traveler",
        is_active=True,
    )
    db_session.add(traveler)
    db_session.commit()

    headers = make_auth_header(traveler.id)

    # Attempt provider onboarding route
    res1 = client.get("/api/test-rbac/provider-endpoint", headers=headers)
    assert res1.status_code == 403
    assert "provider role required" in res1.json()["detail"].lower()

    # Attempt approved-provider route
    res2 = client.get("/api/test-rbac/approved-provider-endpoint", headers=headers)
    assert res2.status_code == 403
    assert "provider role required" in res2.json()["detail"].lower()


# ==============================================================================
# 3. Pending Provider Accessing Onboarding Endpoints
# ==============================================================================


def test_pending_provider_accessing_onboarding_endpoints_succeeds(
    client: TestClient, db_session: Session
):
    """Test 3: Pending providers can access onboarding endpoints protected by require_provider."""
    provider_user = User(
        full_name="Pending Provider",
        email="pending@ceylongo.com",
        password_hash="argon2_hashed",
        role="provider",
        is_active=True,
    )
    db_session.add(provider_user)
    db_session.commit()

    profile = ProviderProfile(
        user_id=provider_user.id,
        business_name="Green Palm Homestay",
        provider_type="homestay",
        phone="0771234567",
        district="Galle",
        description="Scenic coastal homestay",
        approval_status="pending",
    )
    db_session.add(profile)
    db_session.commit()

    headers = make_auth_header(provider_user.id)
    response = client.get("/api/test-rbac/provider-endpoint", headers=headers)

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert "Pending Provider" in data["message"]


# ==============================================================================
# 4. Pending Provider Attempting Approved-Only Access
# ==============================================================================


def test_pending_provider_attempting_approved_access_blocked(
    client: TestClient, db_session: Session
):
    """Test 4: Pending providers must be blocked from approved-only endpoints with 403."""
    provider_user = User(
        full_name="Pending Provider Two",
        email="pending2@ceylongo.com",
        password_hash="argon2_hashed",
        role="provider",
        is_active=True,
    )
    db_session.add(provider_user)
    db_session.commit()

    profile = ProviderProfile(
        user_id=provider_user.id,
        business_name="Surf Point Camp",
        provider_type="activity",
        phone="0779876543",
        district="Matara",
        approval_status="pending",
    )
    db_session.add(profile)
    db_session.commit()

    headers = make_auth_header(provider_user.id)
    response = client.get("/api/test-rbac/approved-provider-endpoint", headers=headers)

    assert response.status_code == 403
    assert "pending approval" in response.json()["detail"].lower()


# ==============================================================================
# 5. Rejected Provider Attempting Approved-Only Access
# ==============================================================================


def test_rejected_provider_attempting_approved_access_blocked(
    client: TestClient, db_session: Session
):
    """Test 5: Rejected providers are blocked from approved-only routes with reason feedback."""
    provider_user = User(
        full_name="Rejected Provider",
        email="rejected@ceylongo.com",
        password_hash="argon2_hashed",
        role="provider",
        is_active=True,
    )
    db_session.add(provider_user)
    db_session.commit()

    profile = ProviderProfile(
        user_id=provider_user.id,
        business_name="Old Villa",
        provider_type="homestay",
        phone="0712345678",
        district="Kandy",
        approval_status="rejected",
        rejection_reason="Business license document expired.",
    )
    db_session.add(profile)
    db_session.commit()

    headers = make_auth_header(provider_user.id)

    # Blocked from approved-only functionality
    res1 = client.get("/api/test-rbac/approved-provider-endpoint", headers=headers)
    assert res1.status_code == 403
    assert "was rejected" in res1.json()["detail"].lower()
    assert "Business license document expired" in res1.json()["detail"]

    # Allowed into provider onboarding route (for profile update & resubmit)
    res2 = client.get("/api/test-rbac/provider-endpoint", headers=headers)
    assert res2.status_code == 200


# ==============================================================================
# 6. Approved Provider Accessing Approved-Only Functionality
# ==============================================================================


def test_approved_provider_accessing_approved_functionality_succeeds(
    client: TestClient, db_session: Session
):
    """Test 6: Approved providers can access both provider onboarding and approved-only routes."""
    provider_user = User(
        full_name="Approved Guide",
        email="guide@ceylongo.com",
        password_hash="argon2_hashed",
        role="provider",
        is_active=True,
    )
    db_session.add(provider_user)
    db_session.commit()

    profile = ProviderProfile(
        user_id=provider_user.id,
        business_name="Sigiriya Heritage Tours",
        provider_type="tour_guide",
        phone="0773334444",
        district="Matale",
        approval_status="approved",
    )
    db_session.add(profile)
    db_session.commit()

    headers = make_auth_header(provider_user.id)

    # Approved-only route
    res1 = client.get("/api/test-rbac/approved-provider-endpoint", headers=headers)
    assert res1.status_code == 200
    data = res1.json()
    assert data["status"] == "success"
    assert "Sigiriya Heritage Tours" in data["message"]
    assert data["approval_status"] == "approved"

    # Base provider route
    res2 = client.get("/api/test-rbac/provider-endpoint", headers=headers)
    assert res2.status_code == 200


# ==============================================================================
# 7. Non-Admin Attempting Admin Access
# ==============================================================================


def test_non_admin_attempting_admin_access_forbidden(
    client: TestClient, db_session: Session
):
    """Test 7: Travelers and Providers attempting admin endpoints receive 403 Forbidden."""
    traveler = User(
        full_name="Traveler One",
        email="trav1@ceylongo.com",
        password_hash="hash",
        role="traveler",
        is_active=True,
    )
    provider = User(
        full_name="Provider One",
        email="prov1@ceylongo.com",
        password_hash="hash",
        role="provider",
        is_active=True,
    )
    db_session.add_all([traveler, provider])
    db_session.commit()

    # Traveler attempts admin
    res_trav = client.get(
        "/api/test-rbac/admin-endpoint", headers=make_auth_header(traveler.id)
    )
    assert res_trav.status_code == 403
    assert "admin role required" in res_trav.json()["detail"].lower()

    # Provider attempts admin
    res_prov = client.get(
        "/api/test-rbac/admin-endpoint", headers=make_auth_header(provider.id)
    )
    assert res_prov.status_code == 403
    assert "admin role required" in res_prov.json()["detail"].lower()


# ==============================================================================
# 8. Admin Accessing Admin-Only Functionality
# ==============================================================================


def test_admin_accessing_admin_functionality_succeeds(
    client: TestClient, db_session: Session
):
    """Test 8: An authenticated admin successfully accesses admin-only endpoints."""
    admin_user = User(
        full_name="System Admin",
        email="admin@ceylongo.com",
        password_hash="hash",
        role="admin",
        is_active=True,
    )
    db_session.add(admin_user)
    db_session.commit()

    headers = make_auth_header(admin_user.id)
    response = client.get("/api/test-rbac/admin-endpoint", headers=headers)

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert "System Admin" in data["message"]
    assert data["user_id"] == admin_user.id


# ==============================================================================
# 9. Inactive Users Being Rejected
# ==============================================================================


def test_inactive_users_rejected_from_all_roles(
    client: TestClient, db_session: Session
):
    """Test 9: Any inactive user is rejected with 403 Forbidden."""
    inactive_provider = User(
        full_name="Banned Provider",
        email="banned.provider@ceylongo.com",
        password_hash="hash",
        role="provider",
        is_active=False,
    )
    inactive_admin = User(
        full_name="Inactive Admin",
        email="inactive.admin@ceylongo.com",
        password_hash="hash",
        role="admin",
        is_active=False,
    )
    db_session.add_all([inactive_provider, inactive_admin])
    db_session.commit()

    # Inactive provider
    res_prov = client.get(
        "/api/test-rbac/provider-endpoint",
        headers=make_auth_header(inactive_provider.id),
    )
    assert res_prov.status_code == 403
    assert "account is inactive" in res_prov.json()["detail"].lower()

    # Inactive admin
    res_admin = client.get(
        "/api/test-rbac/admin-endpoint",
        headers=make_auth_header(inactive_admin.id),
    )
    assert res_admin.status_code == 403
    assert "account is inactive" in res_admin.json()["detail"].lower()


# ==============================================================================
# 10. Provider Without Profile Record
# ==============================================================================


def test_provider_without_profile_returns_404_on_approved_check(
    client: TestClient, db_session: Session
):
    """Test 10: Provider without a profile record returns 404 Not Found on approved provider checks."""
    provider_no_profile = User(
        full_name="Orphan Provider",
        email="orphan@ceylongo.com",
        password_hash="hash",
        role="provider",
        is_active=True,
    )
    db_session.add(provider_no_profile)
    db_session.commit()

    headers = make_auth_header(provider_no_profile.id)
    response = client.get("/api/test-rbac/approved-provider-endpoint", headers=headers)

    assert response.status_code == 404
    assert "provider profile not found" in response.json()["detail"].lower()
