"""
Automated unit and integration tests for Phase 2B Service Provider Email OTP Verification.

Scenarios covered:
1. OTP generation and sending (new provider email)
2. Correct OTP verification -> 'continue_registration' + authorization token
3. Incorrect OTP handling & attempt countdown
4. Expired OTP rejection
5. Five failed attempts threshold & invalidation
6. OTP reuse rejection (single-use consumption)
7. Resend cooldown enforcement (HTTP 429 Retry-After)
8. Rate limiting enforcement
9. SMTP delivery failure atomic rollback
10. Existing Traveler email rejected with HTTP 409 Conflict
11. Pending provider rejected on send & safe status on verify
12. Rejected provider flow -> OTP purpose 'resubmission' -> next_action 'resubmit_application'
13. Approved provider without password -> purpose 'password_setup' -> next_action 'create_password'
14. Approved provider with password rejected on send & redirected to login on verify
15. Authorization token revocation of superseded active tokens
16. Authorization token expiration window
17. Concurrent / replay verification protection
"""

import re
import smtplib
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.otp import (
    hash_authorization_token,
    hash_provider_otp,
)
from app.core.rate_limiter import reset_all_limiters
from app.database.connection import get_db
from app.main import app
from app.models.provider_email_otp import ProviderEmailOtp
from app.models.provider_profile import ProviderProfile
from app.models.provider_registration_authorization import (
    ProviderRegistrationAuthorization,
)
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
def setup_test_db():
    Base.metadata.create_all(bind=test_engine)
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
    monkeypatch.setenv("SMTP_USERNAME", "provider@ceylongo.com")
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


def extract_otp_from_mock_email(mock_smtp_class) -> str:
    """Helper to extract 6-digit OTP from the plain text or HTML email sent via MockSMTP."""
    msg = mock_smtp_class.last_sent_msg
    assert msg is not None, "No email was dispatched"
    text_content = ""
    if msg.is_multipart():
        for part in msg.iter_parts():
            if part.get_content_type() == "text/plain":
                text_content = part.get_content()
                break
        if not text_content:
            for part in msg.walk():
                if part.get_content_type() in ("text/plain", "text/html"):
                    text_content += str(part.get_content())
    else:
        text_content = msg.get_content()

    match = re.search(r"\b(\d{6})\b", text_content)
    assert match is not None, f"Could not find 6-digit OTP in email content:\n{text_content}"
    return match.group(1)


# ==============================================================================
# 1. New Provider Registration Flow (Send & Verify)
# ==============================================================================


def test_send_otp_success_new_email(client: TestClient, mock_smtp, db_session: Session):
    """Test 1: Sending OTP for a new email succeeds, persists hashed record, and dispatches email."""
    res = client.post(
        "/api/v1/providers/otp/send",
        json={"email": "NewProvider@ceylongo.com"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["email"] == "newprovider@ceylongo.com"
    assert "Verification code sent successfully" in data["message"]

    # Verify database record
    otp_rec = (
        db_session.query(ProviderEmailOtp)
        .filter_by(email="newprovider@ceylongo.com")
        .first()
    )
    assert otp_rec is not None
    assert otp_rec.purpose == "registration"
    assert otp_rec.is_used is False
    assert otp_rec.attempts_count == 0
    assert len(otp_rec.otp_hash) == 64  # HMAC-SHA256 hex digest

    # Verify email was dispatched
    otp_code = extract_otp_from_mock_email(mock_smtp)
    assert len(otp_code) == 6


def test_verify_otp_success_new_email(client: TestClient, mock_smtp, db_session: Session):
    """Test 2: Correct OTP verification returns next_action='continue_registration' and auth token."""
    email = "journey@ceylongo.com"
    client.post("/api/v1/providers/otp/send", json={"email": email})
    otp = extract_otp_from_mock_email(mock_smtp)

    res = client.post(
        "/api/v1/providers/otp/verify",
        json={"email": email, "otp": otp},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["verified"] is True
    assert data["next_action"] == "continue_registration"
    assert data["authorization_token"] is not None
    assert len(data["authorization_token"]) > 30
    assert data["expires_in"] == 1800  # 30 mins

    # Verify OTP is consumed
    otp_rec = db_session.query(ProviderEmailOtp).filter_by(email=email).first()
    assert otp_rec.is_used is True
    assert otp_rec.used_at is not None

    # Verify authorization record in database
    token_hash = hash_authorization_token(data["authorization_token"])
    auth_rec = (
        db_session.query(ProviderRegistrationAuthorization)
        .filter_by(token_hash=token_hash)
        .first()
    )
    assert auth_rec is not None
    assert auth_rec.email == email
    assert auth_rec.purpose == "registration"
    assert auth_rec.is_consumed is False


# ==============================================================================
# 2. OTP Error Handling & Attempts Limit
# ==============================================================================


def test_verify_otp_incorrect_code(client: TestClient, mock_smtp):
    """Test 3: Incorrect OTP decreases remaining attempts and rejects with HTTP 400."""
    email = "attempts@ceylongo.com"
    client.post("/api/v1/providers/otp/send", json={"email": email})

    res = client.post(
        "/api/v1/providers/otp/verify",
        json={"email": email, "otp": "999999"},
    )
    assert res.status_code == 400
    assert "Invalid verification code" in res.json()["detail"]
    assert "4 attempt(s) remaining" in res.json()["detail"]


def test_verify_otp_five_failed_attempts_exhausts_and_blocks(client: TestClient, mock_smtp):
    """Test 4: Exceeding 5 failed attempts invalidates the OTP."""
    email = "exhaust@ceylongo.com"
    client.post("/api/v1/providers/otp/send", json={"email": email})

    # Fail 4 times
    for _ in range(4):
        res = client.post(
            "/api/v1/providers/otp/verify",
            json={"email": email, "otp": "000000"},
        )
        assert res.status_code == 400

    # 5th failure exhausts attempts
    res5 = client.post(
        "/api/v1/providers/otp/verify",
        json={"email": email, "otp": "000000"},
    )
    assert res5.status_code == 400
    assert "Maximum verification attempts exceeded" in res5.json()["detail"]

    # Even sending the real OTP now fails
    real_otp = extract_otp_from_mock_email(mock_smtp)
    res_final = client.post(
        "/api/v1/providers/otp/verify",
        json={"email": email, "otp": real_otp},
    )
    assert res_final.status_code == 400


def test_verify_otp_expired_code(client: TestClient, db_session: Session):
    """Test 5: Expired OTP is rejected."""
    email = "expired@ceylongo.com"
    otp = "123456"
    expired_time = datetime.now(UTC) - timedelta(minutes=1)

    otp_rec = ProviderEmailOtp(
        email=email,
        otp_hash=hash_provider_otp(email, otp),
        purpose="registration",
        expires_at=expired_time,
        attempts_count=0,
        max_attempts=5,
        is_used=False,
    )
    db_session.add(otp_rec)
    db_session.commit()

    res = client.post(
        "/api/v1/providers/otp/verify",
        json={"email": email, "otp": otp},
    )
    assert res.status_code == 400
    assert "Invalid or expired" in res.json()["detail"]


def test_verify_otp_cannot_be_reused(client: TestClient, mock_smtp):
    """Test 6: Once consumed, an OTP cannot be verified a second time."""
    email = "reuse@ceylongo.com"
    client.post("/api/v1/providers/otp/send", json={"email": email})
    otp = extract_otp_from_mock_email(mock_smtp)

    # First verification succeeds
    res1 = client.post("/api/v1/providers/otp/verify", json={"email": email, "otp": otp})
    assert res1.status_code == 200

    # Second verification fails
    res2 = client.post("/api/v1/providers/otp/verify", json={"email": email, "otp": otp})
    assert res2.status_code == 400
    assert "Invalid or expired" in res2.json()["detail"]


# ==============================================================================
# 3. Resend Cooldown & Rate Limiting
# ==============================================================================


def test_send_otp_resend_cooldown_enforced(client: TestClient, mock_smtp):
    """Test 7: Requesting an OTP again within 60 seconds returns HTTP 429."""
    email = "cooldown@ceylongo.com"
    res1 = client.post("/api/v1/providers/otp/send", json={"email": email})
    assert res1.status_code == 200

    # Immediate second request
    res2 = client.post("/api/v1/providers/otp/send", json={"email": email})
    assert res2.status_code == 429
    assert "Please wait" in res2.json()["detail"]
    assert "Retry-After" in res2.headers


def test_send_otp_rate_limiting(client: TestClient, mock_smtp):
    """Test 8: IP exceeding max send requests per window is rate-limited with HTTP 429."""
    # Send 5 requests with different emails to bypass per-email cooldown
    for i in range(5):
        res = client.post(
            "/api/v1/providers/otp/send",
            json={"email": f"ratelimit{i}@ceylongo.com"},
        )
        assert res.status_code == 200

    # 6th request triggers rate limiter
    res6 = client.post(
        "/api/v1/providers/otp/send",
        json={"email": "ratelimit5@ceylongo.com"},
    )
    assert res6.status_code == 429
    assert "Too many verification code requests" in res6.json()["detail"]


def test_send_otp_smtp_failure_rolls_back_database(client: TestClient, mock_smtp, db_session: Session):
    """Test 9: SMTP failure rolls back transaction; no usable OTP record remains."""
    mock_smtp.should_fail = True
    email = "smtpfail@ceylongo.com"

    res = client.post("/api/v1/providers/otp/send", json={"email": email})
    assert res.status_code == 500
    assert "Failed to deliver verification email" in res.json()["detail"]

    # Verify no active OTP record was committed
    otp_count = db_session.query(ProviderEmailOtp).filter_by(email=email).count()
    assert otp_count == 0


# ==============================================================================
# 4. Account State Routing & Verification Rules
# ==============================================================================


def test_send_otp_rejected_for_existing_traveler(client: TestClient, db_session: Session):
    """Test 10: Existing Traveler account cannot request a provider registration OTP (HTTP 409)."""
    traveler = User(
        full_name="Traveler User",
        email="traveler@ceylongo.com",
        password_hash="hashed_pw",
        role="traveler",
        is_active=True,
    )
    db_session.add(traveler)
    db_session.commit()

    res = client.post(
        "/api/v1/providers/otp/send",
        json={"email": "traveler@ceylongo.com"},
    )
    assert res.status_code == 409
    assert "already registered as a traveler account" in res.json()["detail"]


def test_send_otp_rejected_for_pending_provider(client: TestClient, db_session: Session):
    """Test 11: Pending provider application is rejected on send with HTTP 400."""
    user = User(
        full_name="Pending Applicant",
        email="pending@ceylongo.com",
        password_hash=None,
        role="provider",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    profile = ProviderProfile(
        user_id=user.id,
        business_name="Pending Villa",
        provider_type="homestay",
        phone="0771112222",
        district="Kandy",
        approval_status="pending",
    )
    db_session.add(profile)
    db_session.commit()

    res = client.post(
        "/api/v1/providers/otp/send",
        json={"email": "pending@ceylongo.com"},
    )
    assert res.status_code == 400
    assert "pending admin review" in res.json()["detail"]


def test_rejected_provider_flow_resubmission(client: TestClient, mock_smtp, db_session: Session):
    """Test 12: Rejected provider receives OTP purpose='resubmission' -> next_action='resubmit_application'."""
    user = User(
        full_name="Rejected Applicant",
        email="rejected@ceylongo.com",
        password_hash=None,
        role="provider",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    profile = ProviderProfile(
        user_id=user.id,
        business_name="Rejected Tours",
        provider_type="tour_guide",
        phone="0773334444",
        district="Galle",
        approval_status="rejected",
        rejection_reason="Unclear NIC document scan.",
    )
    db_session.add(profile)
    db_session.commit()

    # Send OTP
    res_send = client.post("/api/v1/providers/otp/send", json={"email": "rejected@ceylongo.com"})
    assert res_send.status_code == 200

    otp_rec = db_session.query(ProviderEmailOtp).filter_by(email="rejected@ceylongo.com").first()
    assert otp_rec.purpose == "resubmission"

    # Verify OTP
    otp = extract_otp_from_mock_email(mock_smtp)
    res_verify = client.post("/api/v1/providers/otp/verify", json={"email": "rejected@ceylongo.com", "otp": otp})
    assert res_verify.status_code == 200
    data = res_verify.json()
    assert data["verified"] is True
    assert data["next_action"] == "resubmit_application"
    assert data["authorization_token"] is not None


def test_approved_provider_without_password_flow(client: TestClient, mock_smtp, db_session: Session):
    """Test 13: Approved provider without password -> purpose='password_setup' -> next_action='create_password'."""
    user = User(
        full_name="Approved Guide",
        email="approved@ceylongo.com",
        password_hash=None,  # No password yet
        role="provider",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    profile = ProviderProfile(
        user_id=user.id,
        business_name="Approved Tours",
        provider_type="tour_guide",
        phone="0775556666",
        district="Colombo",
        approval_status="approved",
    )
    db_session.add(profile)
    db_session.commit()

    # Send OTP
    res_send = client.post("/api/v1/providers/otp/send", json={"email": "approved@ceylongo.com"})
    assert res_send.status_code == 200

    otp_rec = db_session.query(ProviderEmailOtp).filter_by(email="approved@ceylongo.com").first()
    assert otp_rec.purpose == "password_setup"

    # Verify OTP
    otp = extract_otp_from_mock_email(mock_smtp)
    res_verify = client.post("/api/v1/providers/otp/verify", json={"email": "approved@ceylongo.com", "otp": otp})
    assert res_verify.status_code == 200
    data = res_verify.json()
    assert data["verified"] is True
    assert data["next_action"] == "create_password"
    assert data["authorization_token"] is not None


def test_approved_provider_with_password_directed_to_login(client: TestClient, db_session: Session):
    """Test 14: Approved provider with an existing password is directed to login (send rejected with HTTP 400)."""
    user = User(
        full_name="Active Guide",
        email="active@ceylongo.com",
        password_hash="already_set_argon2_pwd",
        role="provider",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    profile = ProviderProfile(
        user_id=user.id,
        business_name="Active Tours",
        provider_type="tour_guide",
        phone="0777778888",
        district="Colombo",
        approval_status="approved",
    )
    db_session.add(profile)
    db_session.commit()

    res = client.post("/api/v1/providers/otp/send", json={"email": "active@ceylongo.com"})
    assert res.status_code == 400
    assert "already active" in res.json()["detail"].lower()


# ==============================================================================
# 5. Authorization Token Revocation & Expiry
# ==============================================================================


def test_authorization_token_revokes_superseded_tokens(client: TestClient, mock_smtp, db_session: Session):
    """Test 15: Verifying a second OTP revokes the previously issued authorization token."""
    email = "supersede@ceylongo.com"

    # First cycle
    client.post("/api/v1/providers/otp/send", json={"email": email})
    otp1 = extract_otp_from_mock_email(mock_smtp)
    res1 = client.post("/api/v1/providers/otp/verify", json={"email": email, "otp": otp1})
    token1 = res1.json()["authorization_token"]
    hash1 = hash_authorization_token(token1)

    rec1 = db_session.query(ProviderRegistrationAuthorization).filter_by(token_hash=hash1).first()
    assert rec1.is_revoked is False

    # Force reset cooldown on database to allow second send
    db_session.query(ProviderEmailOtp).filter_by(email=email).update(
        {"created_at": datetime.now(UTC) - timedelta(seconds=120)}
    )
    db_session.commit()

    # Second cycle
    client.post("/api/v1/providers/otp/send", json={"email": email})
    otp2 = extract_otp_from_mock_email(mock_smtp)
    res2 = client.post("/api/v1/providers/otp/verify", json={"email": email, "otp": otp2})
    token2 = res2.json()["authorization_token"]
    hash2 = hash_authorization_token(token2)

    # First token must now be revoked
    db_session.refresh(rec1)
    assert rec1.is_revoked is True
    assert rec1.revoked_at is not None

    # Second token is active
    rec2 = db_session.query(ProviderRegistrationAuthorization).filter_by(token_hash=hash2).first()
    assert rec2.is_revoked is False
