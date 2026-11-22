import os
import smtplib
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.rate_limiter import reset_all_limiters
from app.core.security import hash_password
from tests.access_tokens import create_fixture_access_token as create_access_token
from app.database.connection import get_db
from app.main import app
from app.models.provider_profile import ProviderProfile
from app.models.user import Base, User

# In-memory SQLite engine
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


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path, monkeypatch):
    Base.metadata.create_all(bind=test_engine)
    upload_dir = str(tmp_path / "admin_test_uploads")
    os.makedirs(upload_dir, exist_ok=True)
    monkeypatch.setenv("PROVIDER_UPLOAD_DIR", upload_dir)
    yield
    Base.metadata.drop_all(bind=test_engine)


@pytest.fixture(autouse=True)
def reset_limiters():
    reset_all_limiters()
    yield
    reset_all_limiters()


@pytest.fixture
def db_session():
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def mock_smtp(monkeypatch):
    class MockSMTP:
        last_sent_msg = None
        should_fail = False

        def __init__(self, host, port, timeout=10):
            self.host = host
            self.port = port
            self.timeout = timeout

        def __enter__(self):
            if MockSMTP.should_fail:
                raise smtplib.SMTPConnectError(421, b"Service not available")
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

        def ehlo(self):
            pass

        def starttls(self):
            pass

        def login(self, user, password):
            pass

        def send_message(self, msg):
            if MockSMTP.should_fail:
                raise smtplib.SMTPException("Delivery failure")
            MockSMTP.last_sent_msg = msg

    MockSMTP.last_sent_msg = None
    MockSMTP.should_fail = False
    monkeypatch.setenv("SMTP_HOST", "smtp.gmail.com")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USERNAME", "admin@ceylongo.com")
    monkeypatch.setenv("SMTP_PASSWORD", "mock-app-password")
    monkeypatch.setattr(smtplib, "SMTP", MockSMTP)
    return MockSMTP


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


def create_user(
    db: Session,
    email: str,
    role: str = "traveler",
    full_name: str = "Test User",
    is_active: bool = True,
    password: str | None = None,
) -> User:
    user = User(
        email=email.strip().lower(),
        full_name=full_name,
        role=role,
        is_active=is_active,
        password_hash=hash_password(password) if password else None,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def create_provider_profile(
    db: Session,
    user: User,
    business_name: str = "Test Villa",
    provider_type: str = "homestay",
    approval_status: str = "pending",
    has_documents: bool = True,
) -> ProviderProfile:
    upload_dir = os.environ.get("PROVIDER_UPLOAD_DIR", "")
    selfie_ref, front_ref, back_ref = None, None, None

    if has_documents and upload_dir:
        # Create dummy image files on disk
        selfie_name = f"selfie_{user.id}.jpg"
        front_name = f"nic_front_{user.id}.jpg"
        back_name = f"nic_back_{user.id}.jpg"

        for name in (selfie_name, front_name, back_name):
            p = os.path.join(upload_dir, name)
            img = Image.new("RGB", (50, 50), color=(100, 150, 200))
            img.save(p, format="JPEG")

        selfie_ref = selfie_name
        front_ref = front_name
        back_ref = back_name

    profile = ProviderProfile(
        user_id=user.id,
        business_name=business_name,
        provider_type=provider_type,
        phone="0771234567",
        district="Kandy",
        nic_number="199012345678",
        approval_status=approval_status,
        selfie_image_path=selfie_ref,
        nic_front_image_path=front_ref,
        nic_back_image_path=back_ref,
    )
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return profile


def auth_headers_for(user: User) -> dict[str, str]:
    token = create_access_token(data={"sub": str(user.id)})
    return {"Authorization": f"Bearer {token}"}


# ==============================================================================
# 1. Access Control Tests
# ==============================================================================


def test_admin_access_success(client: TestClient, db_session: Session):
    admin = create_user(db_session, "admin@ceylongo.com", role="admin")
    res = client.get("/api/v1/admin/providers", headers=auth_headers_for(admin))
    assert res.status_code == 200
    assert "items" in res.json()


def test_traveler_access_denied(client: TestClient, db_session: Session):
    traveler = create_user(db_session, "traveler@ceylongo.com", role="traveler")
    res = client.get("/api/v1/admin/providers", headers=auth_headers_for(traveler))
    assert res.status_code == 403
    assert "admin role required" in res.json()["detail"]


def test_provider_access_denied(client: TestClient, db_session: Session):
    provider = create_user(db_session, "prov_user@ceylongo.com", role="provider")
    res = client.get("/api/v1/admin/providers", headers=auth_headers_for(provider))
    assert res.status_code == 403
    assert "admin role required" in res.json()["detail"]


def test_unauthenticated_access_denied(client: TestClient):
    res = client.get("/api/v1/admin/providers")
    assert res.status_code == 401


# ==============================================================================
# 2. List, Filter, Search & Pagination Tests
# ==============================================================================


def test_list_providers_filtering_and_pagination(client: TestClient, db_session: Session):
    admin = create_user(db_session, "admin_list@ceylongo.com", role="admin")

    u1 = create_user(db_session, "p1@ceylongo.com", role="provider", full_name="Alpha Guide")
    create_provider_profile(db_session, u1, business_name="Alpha Tours", approval_status="pending")

    u2 = create_user(db_session, "p2@ceylongo.com", role="provider", full_name="Beta Homestay")
    create_provider_profile(db_session, u2, business_name="Beta Stay", approval_status="approved")

    u3 = create_user(db_session, "p3@ceylongo.com", role="provider", full_name="Gamma Surf")
    create_provider_profile(db_session, u3, business_name="Gamma Waves", approval_status="rejected")

    # Filter pending only
    res_pending = client.get(
        "/api/v1/admin/providers?status=pending",
        headers=auth_headers_for(admin),
    )
    assert res_pending.status_code == 200
    data_pending = res_pending.json()
    assert data_pending["total"] == 1
    assert data_pending["items"][0]["email"] == "p1@ceylongo.com"
    assert data_pending["items"][0]["approval_status"] == "pending"

    # Search by business name
    res_search = client.get(
        "/api/v1/admin/providers?search=waves",
        headers=auth_headers_for(admin),
    )
    assert res_search.status_code == 200
    data_search = res_search.json()
    assert data_search["total"] == 1
    assert data_search["items"][0]["business_name"] == "Gamma Waves"

    # Pagination: limit 1, page 2
    res_page = client.get(
        "/api/v1/admin/providers?page=2&limit=1",
        headers=auth_headers_for(admin),
    )
    assert res_page.status_code == 200
    data_page = res_page.json()
    assert len(data_page["items"]) == 1
    assert data_page["page"] == 2
    assert data_page["limit"] == 1
    assert data_page["total"] == 3
    assert data_page["total_pages"] == 3


# ==============================================================================
# 3. Provider Application Details & Secure Document Viewing
# ==============================================================================


def test_get_provider_application_details(client: TestClient, db_session: Session):
    admin = create_user(db_session, "admin_detail@ceylongo.com", role="admin")
    user = create_user(db_session, "prov_detail@ceylongo.com", role="provider", full_name="Nimal Detail")
    profile = create_provider_profile(db_session, user, business_name="Detail Eco Resort")

    res = client.get(
        f"/api/v1/admin/providers/{profile.id}",
        headers=auth_headers_for(admin),
    )
    assert res.status_code == 200
    data = res.json()
    assert data["id"] == profile.id
    assert data["full_name"] == "Nimal Detail"
    assert data["email"] == "prov_detail@ceylongo.com"
    assert data["business_name"] == "Detail Eco Resort"
    assert data["approval_status"] == "pending"
    assert data["selfie_uploaded"] is True
    assert data["nic_front_uploaded"] is True
    assert data["nic_back_uploaded"] is True

    # Ensure no raw filesystem path is in the response text
    assert "admin_test_uploads" not in res.text
    assert "C:\\" not in res.text


def test_secure_identity_document_viewing(client: TestClient, db_session: Session):
    admin = create_user(db_session, "admin_doc@ceylongo.com", role="admin")
    user = create_user(db_session, "prov_doc@ceylongo.com", role="provider")
    profile = create_provider_profile(db_session, user)

    # Valid document viewing
    res = client.get(
        f"/api/v1/admin/providers/{profile.id}/documents/selfie",
        headers=auth_headers_for(admin),
    )
    assert res.status_code == 200
    assert res.headers["Content-Type"] in ("image/jpeg", "image/png", "image/webp")
    assert res.headers["Cache-Control"] == "no-store"
    assert res.headers["X-Content-Type-Options"] == "nosniff"
    assert len(res.content) > 0

    # Invalid document type rejected with 400
    res_bad_type = client.get(
        f"/api/v1/admin/providers/{profile.id}/documents/passport",
        headers=auth_headers_for(admin),
    )
    assert res_bad_type.status_code == 400
    assert "Invalid document type" in res_bad_type.json()["detail"]

    # Invalid document type with traversal characters rejected with 400
    res_invalid_traversal = client.get(
        f"/api/v1/admin/providers/{profile.id}/documents/selfie..",
        headers=auth_headers_for(admin),
    )
    assert res_invalid_traversal.status_code == 400
    assert "Invalid document type" in res_invalid_traversal.json()["detail"]

    # Path traversal attack via DB reference prevented
    profile.selfie_image_path = "../../etc/passwd"
    db_session.commit()
    res_traversal = client.get(
        f"/api/v1/admin/providers/{profile.id}/documents/selfie",
        headers=auth_headers_for(admin),
    )
    assert res_traversal.status_code == 404


# ==============================================================================
# 4. Approval Workflow Tests
# ==============================================================================


def test_approve_provider_success(client: TestClient, db_session: Session, mock_smtp):
    admin = create_user(db_session, "admin_app@ceylongo.com", role="admin", full_name="Super Admin")
    user = create_user(db_session, "prov_app@ceylongo.com", role="provider", full_name="Kamal Silva")
    profile = create_provider_profile(db_session, user, business_name="Kamal Homestay")

    res = client.post(
        f"/api/v1/admin/providers/{profile.id}/approve",
        headers=auth_headers_for(admin),
    )
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["approval_status"] == "approved"
    assert data["email_sent"] is True

    # Verify DB update
    db_session.refresh(profile)
    assert profile.approval_status == "approved"
    assert profile.reviewed_by == admin.id
    assert profile.reviewed_at is not None
    assert profile.rejection_reason is None

    # Ensure provider is NOT logged in and password is NOT set
    db_session.refresh(user)
    assert user.password_hash is None
    assert user.is_active is False

    # Verify approval email dispatched
    assert mock_smtp.last_sent_msg is not None
    assert "approved" in str(mock_smtp.last_sent_msg).lower()


def test_approve_provider_duplicate_and_conflict(client: TestClient, db_session: Session):
    admin = create_user(db_session, "admin_dup@ceylongo.com", role="admin")
    user = create_user(db_session, "prov_dup@ceylongo.com", role="provider")
    profile = create_provider_profile(db_session, user, approval_status="approved")

    # Already approved cannot be approved again
    res = client.post(
        f"/api/v1/admin/providers/{profile.id}/approve",
        headers=auth_headers_for(admin),
    )
    assert res.status_code == 400
    assert "already approved" in res.json()["detail"]

    # Rejected application cannot be approved directly
    profile.approval_status = "rejected"
    db_session.commit()
    res_rej = client.post(
        f"/api/v1/admin/providers/{profile.id}/approve",
        headers=auth_headers_for(admin),
    )
    assert res_rej.status_code == 400
    assert "resubmit first" in res_rej.json()["detail"]


def test_approve_provider_missing_documents_blocked(client: TestClient, db_session: Session):
    admin = create_user(db_session, "admin_nodoc@ceylongo.com", role="admin")
    user = create_user(db_session, "prov_nodoc@ceylongo.com", role="provider")
    profile = create_provider_profile(db_session, user, has_documents=False)

    res = client.post(
        f"/api/v1/admin/providers/{profile.id}/approve",
        headers=auth_headers_for(admin),
    )
    assert res.status_code == 400
    assert "identity documents" in res.json()["detail"]


# ==============================================================================
# 5. Rejection Workflow Tests
# ==============================================================================


def test_reject_provider_success(client: TestClient, db_session: Session, mock_smtp):
    admin = create_user(db_session, "admin_rej@ceylongo.com", role="admin")
    user = create_user(db_session, "prov_rej@ceylongo.com", role="provider", full_name="Saman Perera")
    profile = create_provider_profile(db_session, user, business_name="Saman Village")

    reason = "The uploaded NIC images are blurry. Please provide clear scans."
    res = client.post(
        f"/api/v1/admin/providers/{profile.id}/reject",
        json={"reason": reason},
        headers=auth_headers_for(admin),
    )
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["approval_status"] == "rejected"
    assert data["rejection_reason"] == reason
    assert data["email_sent"] is True

    # Verify DB update
    db_session.refresh(profile)
    assert profile.approval_status == "rejected"
    assert profile.rejection_reason == reason
    assert profile.reviewed_by == admin.id
    assert profile.reviewed_at is not None

    db_session.refresh(user)
    assert user.is_active is False

    # Verify rejection email dispatched
    assert mock_smtp.last_sent_msg is not None
    assert reason in str(mock_smtp.last_sent_msg)


def test_reject_provider_missing_reason_rejected(client: TestClient, db_session: Session):
    admin = create_user(db_session, "admin_noreason@ceylongo.com", role="admin")
    user = create_user(db_session, "prov_noreason@ceylongo.com", role="provider")
    profile = create_provider_profile(db_session, user)

    # Empty reason
    res = client.post(
        f"/api/v1/admin/providers/{profile.id}/reject",
        json={"reason": "   "},
        headers=auth_headers_for(admin),
    )
    assert res.status_code == 422


def test_reject_provider_duplicate_and_conflict(client: TestClient, db_session: Session):
    admin = create_user(db_session, "admin_conf@ceylongo.com", role="admin")
    user = create_user(db_session, "prov_conf@ceylongo.com", role="provider")
    profile = create_provider_profile(db_session, user, approval_status="rejected")

    # Already rejected
    res = client.post(
        f"/api/v1/admin/providers/{profile.id}/reject",
        json={"reason": "Re-rejecting again with notes."},
        headers=auth_headers_for(admin),
    )
    assert res.status_code == 400
    assert "already rejected" in res.json()["detail"]

    # Already approved cannot be rejected
    profile.approval_status = "approved"
    db_session.commit()
    res_app = client.post(
        f"/api/v1/admin/providers/{profile.id}/reject",
        json={"reason": "Cannot reject approved."},
        headers=auth_headers_for(admin),
    )
    assert res_app.status_code == 400
    assert "already approved" in res_app.json()["detail"]


def test_email_failure_does_not_rollback_review(client: TestClient, db_session: Session, mock_smtp):
    admin = create_user(db_session, "admin_fail_smtp@ceylongo.com", role="admin")
    user = create_user(db_session, "prov_fail_smtp@ceylongo.com", role="provider")
    profile = create_provider_profile(db_session, user)

    # Force SMTP failure
    mock_smtp.should_fail = True

    res = client.post(
        f"/api/v1/admin/providers/{profile.id}/approve",
        headers=auth_headers_for(admin),
    )
    assert res.status_code == 200
    data = res.json()
    assert data["approval_status"] == "approved"
    assert data["email_sent"] is False  # Truthful delivery status

    # Verify DB review change was NOT rolled back
    db_session.refresh(profile)
    assert profile.approval_status == "approved"
    assert profile.reviewed_by == admin.id


# ==============================================================================
# 6. Authentication Bypass Prevention Tests
# ==============================================================================


def test_pending_provider_login_blocked(client: TestClient, db_session: Session):
    user = create_user(
        db_session,
        "pending_login@ceylongo.com",
        role="provider",
        is_active=False,
        password="ValidPassword123!",
    )
    create_provider_profile(db_session, user, approval_status="pending")

    res = client.post(
        "/api/v1/auth/login",
        json={"email": "pending_login@ceylongo.com", "password": "ValidPassword123!"},
    )
    assert res.status_code == 403
    assert "pending admin review" in res.json()["detail"]


def test_rejected_provider_login_blocked(client: TestClient, db_session: Session):
    user = create_user(
        db_session,
        "rejected_login@ceylongo.com",
        role="provider",
        is_active=False,
        password="ValidPassword123!",
    )
    create_provider_profile(db_session, user, approval_status="rejected")

    res = client.post(
        "/api/v1/auth/login",
        json={"email": "rejected_login@ceylongo.com", "password": "ValidPassword123!"},
    )
    assert res.status_code == 403
    assert "rejected" in res.json()["detail"]


def test_approved_provider_without_password_login_blocked(client: TestClient, db_session: Session):
    user = create_user(
        db_session,
        "approved_nopwd@ceylongo.com",
        role="provider",
        is_active=False,
        password=None,
    )
    create_provider_profile(db_session, user, approval_status="approved")

    res = client.post(
        "/api/v1/auth/login",
        json={"email": "approved_nopwd@ceylongo.com", "password": "AnyPassword123!"},
    )
    assert res.status_code == 403
    assert "Password setup required" in res.json()["detail"]


def test_google_login_cannot_bypass_provider_onboarding(client: TestClient, db_session: Session, monkeypatch):
    # Provider account exists
    user = create_user(db_session, "provider_google@ceylongo.com", role="provider", is_active=False)
    create_provider_profile(db_session, user, approval_status="pending")

    # Mock Google token verification to return this provider's email
    monkeypatch.setattr(
        "app.routers.auth.verify_google_id_token",
        lambda token: {
            "google_sub": "google_provider_sub_123",
            "email": "provider_google@ceylongo.com",
            "full_name": "Provider User",
        },
    )

    res = client.post("/api/v1/auth/google", json={"id_token": "mock_google_token"})
    assert res.status_code == 403
    assert "not supported for provider accounts" in res.json()["detail"]


def test_password_reset_cannot_bypass_provider_onboarding(client: TestClient, db_session: Session):
    user = create_user(
        db_session, "provider_pwd_reset@ceylongo.com", role="provider", is_active=False, password=None
    )
    create_provider_profile(db_session, user, approval_status="pending")

    # Forgot password should not dispatch or reveal
    res = client.post(
        "/api/v1/auth/forgot-password",
        json={"email": "provider_pwd_reset@ceylongo.com"},
    )
    assert res.status_code == 200
    assert "If an account with that email exists" in res.json()["message"]


def test_traveler_authentication_regression_preserved(client: TestClient, db_session: Session):
    # Ensure normal traveler accounts can register and log in normally
    create_user(
        db_session,
        "traveler_regular@ceylongo.com",
        role="traveler",
        is_active=True,
        password="TravelerPassword123!",
    )

    res_login = client.post(
        "/api/v1/auth/login",
        json={"email": "traveler_regular@ceylongo.com", "password": "TravelerPassword123!"},
    )
    assert res_login.status_code == 200
    assert "access_token" in res_login.json()
    assert res_login.json()["user"]["role"] == "traveler"
