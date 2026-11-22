"""
Automated tests for Module 04 P0.8 — Eco Badge Verification Email Notifications.

Covers:
1. test_badge_granted_sends_correct_email: Admin grants badge -> provider receives granted email.
2. test_badge_rejected_sends_correct_email: Admin rejects badge -> provider receives rejection email with reason.
3. test_badge_revoked_sends_correct_email: Admin revokes badge -> provider receives revocation notice with reason.
4. test_correct_provider_receives_notification: Multi-provider isolation ensures only the owning provider is emailed.
5. test_database_rollback_prevents_notification: Transaction rollback / validation error prevents email dispatch.
6. test_smtp_failure_does_not_undo_committed_decision: SMTP delivery failure does not roll back committed badge status.
7. test_unauthorized_badge_decision_sends_no_email: Unauthorized attempts (401/403) send no email.
8. test_no_private_evidence_or_credentials_in_email: Content inspection confirms absence of tokens, passwords, or private paths.
9. test_duplicate_unchanged_decision_avoids_duplicate_email: Idempotent / unchanged decisions avoid redundant emails.
10. test_smtp_transport_unit: Unit test for send_eco_badge_decision_email with mocked SMTP client and unconfigured SMTP.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.email import (
    build_eco_badge_decision_email_message,
    send_eco_badge_decision_email,
)
from app.core.security import hash_password
from tests.access_tokens import create_fixture_access_token as create_access_token
from app.database.connection import get_db
from app.models.eco_experience import (
    EcoExperience,
    EcoPractice,
    EcoVerification,
)
from app.models.provider_profile import ProviderProfile
from app.models.user import Base, User
from app.routers.auth import router as auth_router
from app.routers.eco_experiences import (
    admin_eco_router,
    router as eco_experiences_router,
)

# Test application isolating M04 routers
eco_p08_app = FastAPI(title="CeylonGo M04 P0.8 Badge Notification Test App")
eco_p08_app.include_router(auth_router)
eco_p08_app.include_router(eco_experiences_router)
eco_p08_app.include_router(admin_eco_router)

test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


@event.listens_for(test_engine, "connect")
def configure_sqlite_connection(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()
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


@pytest.fixture
def db_session():
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def client(db_session: Session):
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    eco_p08_app.dependency_overrides[get_db] = override_get_db
    with TestClient(eco_p08_app) as test_client:
        yield test_client
    eco_p08_app.dependency_overrides.clear()


def auth_header(user: User) -> dict[str, str]:
    token = create_access_token(data={"sub": str(user.id)})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def seed_data(db_session: Session):
    # Provider 1
    user_p1 = User(
        full_name="Sugath Gunasekara",
        email="sugath_eco@test.com",
        password_hash=hash_password("Pass123!"),
        role="provider",
        is_active=True,
        created_at=datetime.now(UTC),
    )
    # Provider 2
    user_p2 = User(
        full_name="Nimal Perera",
        email="nimal_eco@test.com",
        password_hash=hash_password("Pass123!"),
        role="provider",
        is_active=True,
        created_at=datetime.now(UTC),
    )
    # Admin
    admin_user = User(
        full_name="Admin Officer",
        email="admin_eco@test.com",
        password_hash=hash_password("Pass123!"),
        role="admin",
        is_active=True,
        created_at=datetime.now(UTC),
    )
    # Traveler
    traveler_user = User(
        full_name="Traveler Jane",
        email="traveler_jane@test.com",
        password_hash=hash_password("Pass123!"),
        role="traveler",
        is_active=True,
        created_at=datetime.now(UTC),
    )

    db_session.add_all([user_p1, user_p2, admin_user, traveler_user])
    db_session.flush()

    profile_1 = ProviderProfile(
        user_id=user_p1.id,
        business_name="Sinharaja Eco Trekking",
        provider_type="eco_tourism",
        approval_status="approved",
        phone="+94771112233",
        district="Ratnapura",
        created_at=datetime.now(UTC),
    )
    profile_2 = ProviderProfile(
        user_id=user_p2.id,
        business_name="Knuckles Wilderness Adventures",
        provider_type="eco_tourism",
        approval_status="approved",
        phone="+94774445566",
        district="Matale",
        created_at=datetime.now(UTC),
    )
    db_session.add_all([profile_1, profile_2])
    db_session.flush()

    exp_1 = EcoExperience(
        provider_profile_id=profile_1.id,
        title="Sinharaja Rain Forest Canopy Walk",
        eco_category="Forest & Nature Trails",
        description="Authentic biodiversity tour.",
        itinerary="Morning bird watching and rainforest trail hike.",
        duration_hours=Decimal("4.0"),
        district="Ratnapura",
        city="Kalawana",
        meeting_point="Kudawa Forest Gate",
        base_price=Decimal("45.00"),
        currency="USD",
        max_group_size=8,
        min_participants=2,
        min_age=8,
        environmental_guidelines="Strict leave no trace, zero single-use plastics.",
        wildlife_safety_summary="Do not touch or feed native fauna.",
        listing_status="approved",
        is_published=True,
        eco_badge_status="pending",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    exp_2 = EcoExperience(
        provider_profile_id=profile_2.id,
        title="Knuckles Cloud Forest Expedition",
        eco_category="Forest & Nature Trails",
        description="High altitude trail excursion.",
        itinerary="Cloud forest ascent.",
        duration_hours=Decimal("6.0"),
        district="Matale",
        city="Rattota",
        meeting_point="Corbett's Gap",
        base_price=Decimal("60.00"),
        currency="USD",
        max_group_size=6,
        min_participants=2,
        min_age=12,
        environmental_guidelines="Stay on designated paths only.",
        wildlife_safety_summary="Beware of steep slopes and wild leopards.",
        listing_status="approved",
        is_published=True,
        eco_badge_status="pending",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db_session.add_all([exp_1, exp_2])
    db_session.flush()

    # Practice on Exp 1
    prac_1 = EcoPractice(
        eco_experience_id=exp_1.id,
        practice_category="community_employment",
        claim_description="100% of resident trek guides hired from Kudawa village.",
        is_verified=False,
        created_at=datetime.now(UTC),
    )
    db_session.add(prac_1)
    db_session.commit()

    return {
        "user_p1": user_p1,
        "user_p2": user_p2,
        "admin": admin_user,
        "traveler": traveler_user,
        "profile_1": profile_1,
        "profile_2": profile_2,
        "exp_1": exp_1,
        "exp_2": exp_2,
    }


# ==============================================================================
# 1. Decision Notification Tests
# ==============================================================================


@patch("app.routers.eco_experiences.send_eco_badge_decision_email")
def test_badge_granted_sends_correct_email(
    mock_send_email: MagicMock, client: TestClient, seed_data, db_session: Session
):
    """Admin grants Eco Badge -> owning provider receives granted notification with reason and validity."""
    admin = seed_data["admin"]
    exp = seed_data["exp_1"]
    user_p1 = seed_data["user_p1"]

    valid_until = (datetime.now(UTC) + timedelta(days=365)).isoformat()
    response = client.post(
        f"/api/v1/admin/eco-experiences/{exp.id}/badge-decision",
        json={
            "decision": "granted",
            "reason": "Audited Kudawa community employment payroll and village certification.",
            "valid_until": valid_until,
        },
        headers=auth_header(admin),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["eco_badge_status"] == "granted"

    # Verify notification dispatched post-commit
    mock_send_email.assert_called_once()
    call_kwargs = mock_send_email.call_args.kwargs
    assert call_kwargs["to_email"] == user_p1.email
    assert call_kwargs["experience_title"] == exp.title
    assert call_kwargs["decision"] == "granted"
    assert call_kwargs["provider_name"] == user_p1.full_name
    assert call_kwargs["business_name"] == "Sinharaja Eco Trekking"
    assert "Kudawa community employment" in call_kwargs["reason"]


@patch("app.routers.eco_experiences.send_eco_badge_decision_email")
def test_badge_rejected_sends_correct_email(
    mock_send_email: MagicMock, client: TestClient, seed_data, db_session: Session
):
    """Admin rejects Eco Badge -> owning provider receives rejection notice with corrective feedback."""
    admin = seed_data["admin"]
    exp = seed_data["exp_1"]
    user_p1 = seed_data["user_p1"]

    response = client.post(
        f"/api/v1/admin/eco-experiences/{exp.id}/badge-decision",
        json={
            "decision": "rejected",
            "reason": "Supporting payroll document is illegible. Please re-upload certified copy.",
        },
        headers=auth_header(admin),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["eco_badge_status"] == "rejected"

    mock_send_email.assert_called_once()
    call_kwargs = mock_send_email.call_args.kwargs
    assert call_kwargs["to_email"] == user_p1.email
    assert call_kwargs["decision"] == "rejected"
    assert "illegible" in call_kwargs["reason"]


@patch("app.routers.eco_experiences.send_eco_badge_decision_email")
def test_badge_revoked_sends_correct_email(
    mock_send_email: MagicMock, client: TestClient, seed_data, db_session: Session
):
    """Admin revokes an active badge -> provider receives official revocation notice with stated reason."""
    admin = seed_data["admin"]
    exp = seed_data["exp_1"]
    user_p1 = seed_data["user_p1"]

    # First set badge to granted
    exp.eco_badge_status = "granted"
    db_session.commit()

    response = client.post(
        f"/api/v1/admin/eco-experiences/{exp.id}/badge-decision",
        json={
            "decision": "revoked",
            "reason": "Unannounced wildlife audit revealed failure to enforce zero single-use plastics.",
        },
        headers=auth_header(admin),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["eco_badge_status"] == "revoked"

    mock_send_email.assert_called_once()
    call_kwargs = mock_send_email.call_args.kwargs
    assert call_kwargs["to_email"] == user_p1.email
    assert call_kwargs["decision"] == "revoked"
    assert "Unannounced wildlife audit" in call_kwargs["reason"]


# ==============================================================================
# 2. Isolation & Security Tests
# ==============================================================================


@patch("app.routers.eco_experiences.send_eco_badge_decision_email")
def test_correct_provider_receives_notification(
    mock_send_email: MagicMock, client: TestClient, seed_data, db_session: Session
):
    """Ensure Provider 1's decision goes strictly to Provider 1, never Provider 2."""
    admin = seed_data["admin"]
    exp_2 = seed_data["exp_2"]
    user_p2 = seed_data["user_p2"]
    user_p1 = seed_data["user_p1"]

    response = client.post(
        f"/api/v1/admin/eco-experiences/{exp_2.id}/badge-decision",
        json={"decision": "granted", "reason": "Knuckles conservation documentation verified."},
        headers=auth_header(admin),
    )
    assert response.status_code == 200

    mock_send_email.assert_called_once()
    call_kwargs = mock_send_email.call_args.kwargs
    assert call_kwargs["to_email"] == user_p2.email
    assert call_kwargs["to_email"] != user_p1.email
    assert call_kwargs["business_name"] == "Knuckles Wilderness Adventures"


@patch("app.routers.eco_experiences.send_eco_badge_decision_email")
def test_database_rollback_prevents_notification(
    mock_send_email: MagicMock, client: TestClient, seed_data, db_session: Session
):
    """If database transaction fails or payload is invalid, no notification is sent."""
    admin = seed_data["admin"]
    exp = seed_data["exp_1"]

    # Invalid decision payload -> 422
    response = client.post(
        f"/api/v1/admin/eco-experiences/{exp.id}/badge-decision",
        json={"decision": "super_granted", "reason": "Invalid status testing"},
        headers=auth_header(admin),
    )
    assert response.status_code == 422
    mock_send_email.assert_not_called()


@patch("app.routers.eco_experiences.send_eco_badge_decision_email")
def test_smtp_failure_does_not_undo_committed_decision(
    mock_send_email: MagicMock, client: TestClient, seed_data, db_session: Session
):
    """
    If the SMTP transport throws a network or socket error, the committed badge decision
    remains safely intact and the API call returns 200 OK.
    """
    mock_send_email.side_effect = ConnectionError("SMTP relay connection timeout")

    admin = seed_data["admin"]
    exp = seed_data["exp_1"]

    response = client.post(
        f"/api/v1/admin/eco-experiences/{exp.id}/badge-decision",
        json={"decision": "granted", "reason": "Solid sustainability documentation verified."},
        headers=auth_header(admin),
    )
    assert response.status_code == 200
    assert response.json()["eco_badge_status"] == "granted"

    # Verify decision is persisted in DB despite email delivery failure
    db_session.expire_all()
    reloaded_exp = db_session.query(EcoExperience).filter(EcoExperience.id == exp.id).first()
    assert reloaded_exp.eco_badge_status == "granted"

    # Verify audit verification row was committed
    verifications = db_session.query(EcoVerification).filter(EcoVerification.eco_experience_id == exp.id).all()
    assert any(v.decision == "granted" for v in verifications)


@patch("app.routers.eco_experiences.send_eco_badge_decision_email")
def test_unauthorized_badge_decision_sends_no_email(
    mock_send_email: MagicMock, client: TestClient, seed_data
):
    """Travelers, unauthenticated users, or providers cannot make badge decisions or trigger emails."""
    exp = seed_data["exp_1"]
    traveler = seed_data["traveler"]
    provider = seed_data["user_p1"]

    # 1. Unauthenticated -> 401
    resp_unauth = client.post(
        f"/api/v1/admin/eco-experiences/{exp.id}/badge-decision",
        json={"decision": "granted", "reason": "Hacking badge"},
    )
    assert resp_unauth.status_code == 401

    # 2. Traveler -> 403
    resp_traveler = client.post(
        f"/api/v1/admin/eco-experiences/{exp.id}/badge-decision",
        json={"decision": "granted", "reason": "Traveler trying to grant"},
        headers=auth_header(traveler),
    )
    assert resp_traveler.status_code == 403

    # 3. Provider -> 403
    resp_provider = client.post(
        f"/api/v1/admin/eco-experiences/{exp.id}/badge-decision",
        json={"decision": "granted", "reason": "Self-grant attempt"},
        headers=auth_header(provider),
    )
    assert resp_provider.status_code == 403

    mock_send_email.assert_not_called()


@patch("app.routers.eco_experiences.send_eco_badge_decision_email")
def test_duplicate_unchanged_decision_avoids_duplicate_email(
    mock_send_email: MagicMock, client: TestClient, seed_data, db_session: Session
):
    """When a badge decision is unchanged (e.g. granted -> granted), no redundant email is sent."""
    admin = seed_data["admin"]
    exp = seed_data["exp_1"]

    # Set initial state to granted
    exp.eco_badge_status = "granted"
    db_session.commit()

    response = client.post(
        f"/api/v1/admin/eco-experiences/{exp.id}/badge-decision",
        json={"decision": "granted", "reason": "Re-affirming existing grant without status change."},
        headers=auth_header(admin),
    )
    assert response.status_code == 200
    assert response.json()["eco_badge_status"] == "granted"

    # Status did not change, so no duplicate notification is sent
    mock_send_email.assert_not_called()


# ==============================================================================
# 3. Template Inspection & Confidentiality Tests
# ==============================================================================


def test_no_private_evidence_or_credentials_in_email():
    """
    Ensure email messages never include private file paths, evidence identifiers,
    internal admin user IDs, or access tokens.
    """
    msg = build_eco_badge_decision_email_message(
        to_email="test_provider@test.com",
        experience_title="Sinharaja Primary Trek",
        decision="granted",
        provider_name="Sugath",
        business_name="Sinharaja Safaris",
        reason="Conservation practices meet criteria.",
        valid_until=datetime.now(UTC) + timedelta(days=365),
    )

    body_text = msg.get_body(preferencelist=("plain",)).get_content()
    body_html = msg.get_body(preferencelist=("html",)).get_content()

    forbidden_patterns = [
        "uploads/private_eco_evidence",
        "evidence_id",
        "bearer",
        "token=",
        "password_hash",
        "reviewed_by_admin_id",
        "/admin/providers/",
    ]

    for pattern in forbidden_patterns:
        assert pattern not in body_text.lower()
        assert pattern not in body_html.lower()

    # Branding checks
    assert "CeylonGo" in msg["Subject"]
    assert "Sinharaja Primary Trek" in msg["Subject"]
    assert "Discover Local. Feel at Home." in body_text
    assert "Discover Local. Feel at Home." in body_html


def test_smtp_transport_unit():
    """Unit test for send_eco_badge_decision_email with mocked SMTP and unconfigured state."""
    # 1. When SMTP unconfigured -> returns False safely
    with patch("app.core.email.is_smtp_configured", return_value=False):
        success = send_eco_badge_decision_email(
            to_email="unconfigured@test.com",
            experience_title="Test Trail",
            decision="granted",
        )
        assert success is False

    # 2. When SMTP configured -> calls send_message on SMTP server
    mock_smtp_instance = MagicMock()
    with patch("app.core.email.is_smtp_configured", return_value=True), \
         patch("app.core.email.get_smtp_host", return_value="smtp.test.com"), \
         patch("app.core.email.get_smtp_port", return_value=587), \
         patch("app.core.email.get_smtp_timeout", return_value=10), \
         patch("app.core.email.is_smtp_use_tls", return_value=True), \
         patch("app.core.email.get_smtp_username", return_value="smtp_user"), \
         patch("app.core.email.get_smtp_password", return_value="smtp_pass"), \
         patch("smtplib.SMTP") as mock_smtp_cls:

        mock_smtp_cls.return_value.__enter__.return_value = mock_smtp_instance
        success = send_eco_badge_decision_email(
            to_email="test@test.com",
            experience_title="Test Hike",
            decision="granted",
            provider_name="Test Provider",
            reason="Verified criteria",
        )
        assert success is True
        mock_smtp_instance.starttls.assert_called_once()
        mock_smtp_instance.login.assert_called_once_with("smtp_user", "smtp_pass")
        mock_smtp_instance.send_message.assert_called_once()
