import io
import os
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.otp import (
    generate_authorization_token,
    hash_authorization_token,
)
from app.core.rate_limiter import reset_all_limiters
from app.database.connection import get_db
from app.main import app
from app.models.provider_profile import ProviderProfile
from app.models.provider_registration_authorization import (
    ProviderRegistrationAuthorization,
)
from app.models.provider_registration_draft import ProviderRegistrationDraft
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
    upload_dir = str(tmp_path / "test_uploads")
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


def issue_test_auth_token(
    db: Session,
    email: str,
    purpose: str = "registration",
    expires_in_minutes: int = 30,
    is_consumed: bool = False,
    is_revoked: bool = False,
) -> str:
    raw_token = generate_authorization_token()
    token_hash = hash_authorization_token(raw_token)
    auth = ProviderRegistrationAuthorization(
        email=email.strip().lower(),
        token_hash=token_hash,
        purpose=purpose,
        expires_at=datetime.now(UTC) + timedelta(minutes=expires_in_minutes),
        is_consumed=is_consumed,
        is_revoked=is_revoked,
    )
    db.add(auth)
    db.commit()
    return raw_token


def create_test_image(fmt: str = "JPEG", size=(100, 100), color=(255, 0, 0)) -> bytes:
    buf = io.BytesIO()
    img = Image.new("RGB", size, color)
    img.save(buf, format=fmt)
    return buf.getvalue()


# ==============================================================================
# 1. Personal Details Validation Tests
# ==============================================================================


def test_save_personal_details_success(client: TestClient, db_session: Session):
    token = issue_test_auth_token(db_session, "newprovider@ceylongo.com")
    payload = {
        "full_name": "Kasun Perera",
        "phone": "0771234567",
        "nic_number": "200012345678",
        "permanent_province": "Central",
        "permanent_district": "Kandy",
        "permanent_city": "Kandy",
        "permanent_landmark": "Near Lake",
        "permanent_postal_code": "20000",
    }
    res = client.put(
        "/api/v1/providers/registration/personal",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["email"] == "newprovider@ceylongo.com"
    assert data["full_name"] == "Kasun Perera"
    assert data["phone"] == "0771234567"
    assert data["nic_number"] == "200012345678"
    assert data["personal_completed"] is True
    assert data["business_completed"] is False
    assert data["current_step"] == "business"

    # Verify DB draft
    draft = db_session.query(ProviderRegistrationDraft).filter_by(email="newprovider@ceylongo.com").first()
    assert draft is not None
    assert draft.full_name == "Kasun Perera"
    assert draft.nic_number == "200012345678"
    assert draft.registration_step == "business"


def test_save_personal_details_validates_phone_formats(client: TestClient, db_session: Session):
    token = issue_test_auth_token(db_session, "phone_val@ceylongo.com")

    # Invalid phone
    res_bad = client.put(
        "/api/v1/providers/registration/personal",
        json={
            "full_name": "Test User",
            "phone": "12345",
            "nic_number": "123456789V",
            "permanent_province": "Western",
            "permanent_district": "Colombo",
            "permanent_city": "Colombo",
            "permanent_postal_code": "00100",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res_bad.status_code == 422

    # Valid with +94
    res_good = client.put(
        "/api/v1/providers/registration/personal",
        json={
            "full_name": "Test User",
            "phone": "+94771234567",
            "nic_number": "123456789V",
            "permanent_province": "Western",
            "permanent_district": "Colombo",
            "permanent_city": "Colombo",
            "permanent_postal_code": "00100",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res_good.status_code == 200
    assert res_good.json()["phone"] == "0771234567"


def test_save_personal_details_validates_nic_formats(client: TestClient, db_session: Session):
    token = issue_test_auth_token(db_session, "nic_val@ceylongo.com")

    # Invalid NIC
    res_bad = client.put(
        "/api/v1/providers/registration/personal",
        json={
            "full_name": "Test User",
            "phone": "0771234567",
            "nic_number": "invalid_nic_123",
            "permanent_province": "Western",
            "permanent_district": "Colombo",
            "permanent_city": "Colombo",
            "permanent_postal_code": "00100",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res_bad.status_code == 422

    # Valid Old NIC with lowercase 'v' -> normalized to 'V'
    res_old_v = client.put(
        "/api/v1/providers/registration/personal",
        json={
            "full_name": "Test User",
            "phone": "0771234567",
            "nic_number": "987654321v",
            "permanent_province": "Western",
            "permanent_district": "Colombo",
            "permanent_city": "Colombo",
            "permanent_postal_code": "00100",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res_old_v.status_code == 200
    assert res_old_v.json()["nic_number"] == "987654321V"


def test_save_personal_details_duplicate_nic_detected(client: TestClient, db_session: Session):
    # Existing provider in provider_profiles with this NIC
    existing_user = User(
        full_name="Existing Provider",
        email="existing@ceylongo.com",
        role="provider",
        is_active=True,
    )
    db_session.add(existing_user)
    db_session.flush()

    existing_profile = ProviderProfile(
        user_id=existing_user.id,
        business_name="Existing Business",
        provider_type="homestay",
        phone="0771112222",
        district="Kandy",
        nic_number="199012345678",
        approval_status="approved",
    )
    db_session.add(existing_profile)
    db_session.commit()

    token = issue_test_auth_token(db_session, "new_applicant@ceylongo.com")
    res = client.put(
        "/api/v1/providers/registration/personal",
        json={
            "full_name": "New Applicant",
            "phone": "0779998888",
            "nic_number": "199012345678",
            "permanent_province": "Central",
            "permanent_district": "Kandy",
            "permanent_city": "Kandy",
            "permanent_postal_code": "20000",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 409
    assert "NIC number is already associated" in res.json()["detail"]


# ==============================================================================
# 2. Business Details Validation Tests
# ==============================================================================


def test_save_business_details_success(client: TestClient, db_session: Session):
    token = issue_test_auth_token(db_session, "biz@ceylongo.com")
    payload = {
        "business_name": "Kandy Village Homestay",
        "provider_type": "homestay",
        "secondary_phone": "0711234567",
        "business_province": "Central",
        "business_district": "Kandy",
        "business_city": "Kandy",
        "business_landmark": "Near Lake",
        "business_postal_code": "20000",
        "business_registration_number": "BR-1234",
        "description": "Authentic mountain homestay experience in Kandy.",
        "same_as_permanent": False,
    }
    res = client.put(
        "/api/v1/providers/registration/business",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["business_name"] == "Kandy Village Homestay"
    assert data["provider_type"] == "homestay"
    assert data["secondary_phone"] == "0711234567"
    assert data["business_completed"] is True
    assert data["current_step"] == "personal"  # Personal not yet filled


def test_save_business_details_invalid_provider_type(client: TestClient, db_session: Session):
    token = issue_test_auth_token(db_session, "invalid_biz@ceylongo.com")
    res = client.put(
        "/api/v1/providers/registration/business",
        json={
            "business_name": "Invalid Type Business",
            "provider_type": "luxury_airline",
            "business_province": "Western",
            "business_district": "Colombo",
            "business_city": "Colombo",
            "business_postal_code": "00100",
            "description": "Luxury travel.",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 422


def test_save_business_details_same_as_permanent(client: TestClient, db_session: Session):
    token = issue_test_auth_token(db_session, "same_addr@ceylongo.com")

    # Step 1: Save personal details
    client.put(
        "/api/v1/providers/registration/personal",
        json={
            "full_name": "Nimal Silva",
            "phone": "0771234567",
            "nic_number": "199512345678",
            "permanent_province": "Southern",
            "permanent_district": "Galle",
            "permanent_city": "Unawatuna",
            "permanent_landmark": "Near Beach",
            "permanent_postal_code": "80600",
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    # Step 2: Save business details with same_as_permanent = True
    res = client.put(
        "/api/v1/providers/registration/business",
        json={
            "business_name": "Unawatuna Surf School",
            "provider_type": "activity",
            "description": "Daily surf training sessions.",
            "same_as_permanent": True,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["business_province"] == "Southern"
    assert data["business_district"] == "Galle"
    assert data["business_city"] == "Unawatuna"
    assert data["business_postal_code"] == "80600"
    assert data["personal_completed"] is True
    assert data["business_completed"] is True
    assert data["current_step"] == "identity"


# ==============================================================================
# 3. Identity Document Upload Tests
# ==============================================================================


def test_upload_identity_documents_success(client: TestClient, db_session: Session):
    token = issue_test_auth_token(db_session, "docs@ceylongo.com")

    selfie_bytes = create_test_image("JPEG")
    nic_front_bytes = create_test_image("PNG")
    nic_back_bytes = create_test_image("WEBP")

    files = {
        "selfie": ("selfie.jpg", selfie_bytes, "image/jpeg"),
        "nic_front": ("front.png", nic_front_bytes, "image/png"),
        "nic_back": ("back.webp", nic_back_bytes, "image/webp"),
    }

    res = client.post(
        "/api/v1/providers/registration/identity",
        files=files,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["selfie_uploaded"] is True
    assert data["nic_front_uploaded"] is True
    assert data["nic_back_uploaded"] is True
    assert data["identity_completed"] is True

    # Ensure no raw filesystem paths are exposed in the JSON response
    response_str = res.text
    assert "test_uploads" not in response_str
    assert "C:\\" not in response_str
    assert "/uploads/" not in response_str


def test_upload_identity_documents_rejects_corrupted_file(client: TestClient, db_session: Session):
    token = issue_test_auth_token(db_session, "corrupt@ceylongo.com")
    fake_image_bytes = b"This is not a real image header."

    res = client.post(
        "/api/v1/providers/registration/identity",
        files={"selfie": ("selfie.jpg", fake_image_bytes, "image/jpeg")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 400
    assert "Invalid or corrupted image content" in res.json()["detail"]


def test_upload_identity_documents_size_limit_exceeded(client: TestClient, db_session: Session):
    token = issue_test_auth_token(db_session, "oversize@ceylongo.com")
    # 5MB + 1 byte
    oversized_bytes = b"0" * (5 * 1024 * 1024 + 1)

    res = client.post(
        "/api/v1/providers/registration/identity",
        files={"selfie": ("selfie.jpg", oversized_bytes, "image/jpeg")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 400
    assert "exceeds maximum allowed size of 5 MB" in res.json()["detail"]


def test_upload_identity_documents_cleans_up_superseded_file(client: TestClient, db_session: Session):
    token = issue_test_auth_token(db_session, "supersede_file@ceylongo.com")
    img1 = create_test_image("JPEG", color=(255, 0, 0))
    img2 = create_test_image("JPEG", color=(0, 255, 0))

    # First upload
    res1 = client.post(
        "/api/v1/providers/registration/identity",
        files={"selfie": ("selfie1.jpg", img1, "image/jpeg")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res1.status_code == 200

    draft = db_session.query(ProviderRegistrationDraft).filter_by(email="supersede_file@ceylongo.com").first()
    old_file_ref = draft.selfie_file_reference
    upload_dir = os.environ["PROVIDER_UPLOAD_DIR"]
    old_file_path = os.path.join(upload_dir, old_file_ref)
    assert os.path.exists(old_file_path)

    # Re-upload selfie
    res2 = client.post(
        "/api/v1/providers/registration/identity",
        files={"selfie": ("selfie2.jpg", img2, "image/jpeg")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res2.status_code == 200

    db_session.refresh(draft)
    new_file_ref = draft.selfie_file_reference
    assert new_file_ref != old_file_ref
    # Old file must have been deleted
    assert not os.path.exists(old_file_path)
    # New file must exist
    assert os.path.exists(os.path.join(upload_dir, new_file_ref))


# ==============================================================================
# 4. Draft Retrieval & Security Tests
# ==============================================================================


def test_get_registration_draft_lifecycle(client: TestClient, db_session: Session):
    token = issue_test_auth_token(db_session, "lifecycle@ceylongo.com")

    # Step 0: Before any draft creation
    res0 = client.get(
        "/api/v1/providers/registration/draft",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res0.status_code == 200
    assert res0.json()["personal_completed"] is False
    assert res0.json()["current_step"] == "personal"

    # Step 1: Add personal details
    client.put(
        "/api/v1/providers/registration/personal",
        json={
            "full_name": "Ruwan Perera",
            "phone": "0771234567",
            "nic_number": "198812345678",
            "permanent_province": "Central",
            "permanent_district": "Matale",
            "permanent_city": "Dambulla",
            "permanent_postal_code": "21100",
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    res1 = client.get(
        "/api/v1/providers/registration/draft",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res1.status_code == 200
    assert res1.json()["personal_completed"] is True
    assert res1.json()["business_completed"] is False
    assert res1.json()["current_step"] == "business"


def test_registration_authorization_security_rejections(client: TestClient, db_session: Session):
    # 1. Missing Authorization header
    res_no_auth = client.get("/api/v1/providers/registration/draft")
    assert res_no_auth.status_code == 401
    assert "token required" in res_no_auth.json()["detail"]

    # 2. Invalid / unknown token
    res_fake = client.get(
        "/api/v1/providers/registration/draft",
        headers={"Authorization": "Bearer not_a_real_token"},
    )
    assert res_fake.status_code == 401

    # 3. Expired token
    expired_token = issue_test_auth_token(
        db_session, "exp@ceylongo.com", expires_in_minutes=-5
    )
    res_exp = client.get(
        "/api/v1/providers/registration/draft",
        headers={"Authorization": f"Bearer {expired_token}"},
    )
    assert res_exp.status_code == 401
    assert "expired" in res_exp.json()["detail"]

    # 4. Revoked token
    revoked_token = issue_test_auth_token(
        db_session, "rev@ceylongo.com", is_revoked=True
    )
    res_rev = client.get(
        "/api/v1/providers/registration/draft",
        headers={"Authorization": f"Bearer {revoked_token}"},
    )
    assert res_rev.status_code == 401
    assert "revoked" in res_rev.json()["detail"]

    # 5. Consumed token
    consumed_token = issue_test_auth_token(
        db_session, "con@ceylongo.com", is_consumed=True
    )
    res_con = client.get(
        "/api/v1/providers/registration/draft",
        headers={"Authorization": f"Bearer {consumed_token}"},
    )
    assert res_con.status_code == 401
    assert "already been used" in res_con.json()["detail"]

    # 6. Wrong token purpose (e.g. password_setup)
    pwd_token = issue_test_auth_token(
        db_session, "pwd@ceylongo.com", purpose="password_setup"
    )
    res_pwd = client.get(
        "/api/v1/providers/registration/draft",
        headers={"Authorization": f"Bearer {pwd_token}"},
    )
    assert res_pwd.status_code == 403
    assert "purpose not allowed" in res_pwd.json()["detail"]


def test_registration_authorization_traveler_blocked(client: TestClient, db_session: Session):
    # Existing traveler
    traveler = User(
        full_name="Traveler User",
        email="traveler@ceylongo.com",
        role="traveler",
        is_active=True,
    )
    db_session.add(traveler)
    db_session.commit()

    token = issue_test_auth_token(db_session, "traveler@ceylongo.com")
    res = client.get(
        "/api/v1/providers/registration/draft",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 403
    assert "registered as a traveler account" in res.json()["detail"]


# ==============================================================================
# 5. Final Submission Tests
# ==============================================================================


def test_final_submit_missing_sections_blocked(client: TestClient, db_session: Session):
    token = issue_test_auth_token(db_session, "incomplete@ceylongo.com")

    # Attempt submit without any draft
    res0 = client.post(
        "/api/v1/providers/registration/submit",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res0.status_code == 400
    assert "No registration draft found" in res0.json()["detail"]

    # Fill only personal details
    client.put(
        "/api/v1/providers/registration/personal",
        json={
            "full_name": "Incomplete User",
            "phone": "0771234567",
            "nic_number": "199112345678",
            "permanent_province": "Central",
            "permanent_district": "Kandy",
            "permanent_city": "Kandy",
            "permanent_postal_code": "20000",
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    # Attempt submit without business and identity
    res1 = client.post(
        "/api/v1/providers/registration/submit",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res1.status_code == 400
    assert "Incomplete business details" in res1.json()["detail"]


def test_final_submit_success_new_provider(client: TestClient, db_session: Session):
    email = "ready_provider@ceylongo.com"
    token = issue_test_auth_token(db_session, email)

    # 1. Personal details
    client.put(
        "/api/v1/providers/registration/personal",
        json={
            "full_name": "Sunil Jayawardena",
            "phone": "0771234567",
            "nic_number": "198512345678",
            "permanent_province": "Central",
            "permanent_district": "Kandy",
            "permanent_city": "Kandy",
            "permanent_landmark": "Temple Road",
            "permanent_postal_code": "20000",
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    # 2. Business details
    client.put(
        "/api/v1/providers/registration/business",
        json={
            "business_name": "Kandy Mountain Retreat",
            "provider_type": "homestay",
            "secondary_phone": "0711234567",
            "business_province": "Central",
            "business_district": "Kandy",
            "business_city": "Kandy",
            "business_landmark": "Near Hills",
            "business_postal_code": "20000",
            "business_registration_number": "BR-9999",
            "description": "Peaceful homestay with scenic mountain views.",
            "same_as_permanent": False,
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    # 3. Identity documents
    client.post(
        "/api/v1/providers/registration/identity",
        files={
            "selfie": ("selfie.jpg", create_test_image("JPEG"), "image/jpeg"),
            "nic_front": ("front.png", create_test_image("PNG"), "image/png"),
            "nic_back": ("back.webp", create_test_image("WEBP"), "image/webp"),
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    # 4. Final Submission
    res_submit = client.post(
        "/api/v1/providers/registration/submit",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res_submit.status_code == 200
    submit_data = res_submit.json()
    assert submit_data["submitted"] is True
    assert submit_data["approval_status"] == "pending"
    assert submit_data["next_action"] == "await_admin_review"

    # Verify User record created
    user = db_session.query(User).filter_by(email=email).first()
    assert user is not None
    assert user.role == "provider"
    assert user.password_hash is None
    assert user.is_active is False  # Must not be active until approved

    # Verify ProviderProfile record created
    profile = db_session.query(ProviderProfile).filter_by(user_id=user.id).first()
    assert profile is not None
    assert profile.business_name == "Kandy Mountain Retreat"
    assert profile.provider_type == "homestay"
    assert profile.phone == "0771234567"
    assert profile.district == "Kandy"
    assert profile.nic_number == "198512345678"
    assert profile.approval_status == "pending"
    assert profile.selfie_image_path is not None
    assert profile.nic_front_image_path is not None
    assert profile.nic_back_image_path is not None

    # Verify authorization token consumed
    auth_rec = (
        db_session.query(ProviderRegistrationAuthorization)
        .filter_by(email=email)
        .first()
    )
    assert auth_rec.is_consumed is True
    assert auth_rec.consumed_at is not None

    # Verify draft is cleaned up
    draft_check = db_session.query(ProviderRegistrationDraft).filter_by(email=email).first()
    assert draft_check is None

    # Duplicate submission with now-consumed token rejected with 401
    res_dup = client.post(
        "/api/v1/providers/registration/submit",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res_dup.status_code == 401


def test_final_submit_resubmission_rejected_provider(client: TestClient, db_session: Session):
    email = "rejected_provider@ceylongo.com"

    # Create existing rejected provider
    user = User(
        full_name="Old Provider Name",
        email=email,
        role="provider",
        is_active=False,
    )
    db_session.add(user)
    db_session.flush()

    profile = ProviderProfile(
        user_id=user.id,
        business_name="Old Business Name",
        provider_type="tour_guide",
        phone="0771112222",
        district="Kandy",
        nic_number="198012345678",
        approval_status="rejected",
        rejection_reason="Unclear NIC back image",
        reviewed_at=datetime.now(UTC),
    )
    db_session.add(profile)
    db_session.commit()

    token = issue_test_auth_token(db_session, email, purpose="resubmission")

    # Complete draft
    client.put(
        "/api/v1/providers/registration/personal",
        json={
            "full_name": "Updated Provider Name",
            "phone": "0779998888",
            "nic_number": "198012345678",
            "permanent_province": "Central",
            "permanent_district": "Kandy",
            "permanent_city": "Kandy",
            "permanent_postal_code": "20000",
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    client.put(
        "/api/v1/providers/registration/business",
        json={
            "business_name": "Updated Guide Service",
            "provider_type": "tour_guide",
            "business_province": "Central",
            "business_district": "Kandy",
            "business_city": "Kandy",
            "business_postal_code": "20000",
            "description": "Updated tour guide description with verified licenses.",
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    client.post(
        "/api/v1/providers/registration/identity",
        files={
            "selfie": ("selfie.jpg", create_test_image("JPEG"), "image/jpeg"),
            "nic_front": ("front.png", create_test_image("PNG"), "image/png"),
            "nic_back": ("back.png", create_test_image("PNG"), "image/png"),
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    res_resubmit = client.post(
        "/api/v1/providers/registration/submit",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res_resubmit.status_code == 200

    # Verify no second user was created
    user_count = db_session.query(User).filter_by(email=email).count()
    assert user_count == 1

    # Verify existing profile was updated and status reset to pending
    db_session.refresh(profile)
    assert profile.business_name == "Updated Guide Service"
    assert profile.phone == "0779998888"
    assert profile.approval_status == "pending"
    assert profile.rejection_reason is None
    assert profile.reviewed_by is None
    assert profile.reviewed_at is None


def test_registration_cross_account_isolation(client: TestClient, db_session: Session):
    token_a = issue_test_auth_token(db_session, "provider_a@ceylongo.com")
    token_b = issue_test_auth_token(db_session, "provider_b@ceylongo.com")

    # Provider A creates a draft
    client.put(
        "/api/v1/providers/registration/personal",
        json={
            "full_name": "Provider A",
            "phone": "0771111111",
            "nic_number": "199011111111",
            "permanent_province": "Western",
            "permanent_district": "Colombo",
            "permanent_city": "Colombo",
            "permanent_postal_code": "00100",
        },
        headers={"Authorization": f"Bearer {token_a}"},
    )

    # Provider B fetches their draft: should see only Provider B's empty draft, never A's
    res_b = client.get(
        "/api/v1/providers/registration/draft",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert res_b.status_code == 200
    assert res_b.json()["email"] == "provider_b@ceylongo.com"
    assert res_b.json()["full_name"] is None
    assert res_b.json()["personal_completed"] is False


def test_registration_authorization_pending_and_active_provider_blocked(client: TestClient, db_session: Session):
    # Pending provider
    pending_user = User(
        full_name="Pending User",
        email="pending_prov@ceylongo.com",
        role="provider",
        is_active=False,
    )
    db_session.add(pending_user)
    db_session.flush()

    pending_profile = ProviderProfile(
        user_id=pending_user.id,
        business_name="Pending Biz",
        provider_type="activity",
        phone="0773333333",
        district="Colombo",
        approval_status="pending",
    )
    db_session.add(pending_profile)

    # Active provider with password
    active_user = User(
        full_name="Active User",
        email="active_prov@ceylongo.com",
        role="provider",
        is_active=True,
        password_hash="argon2_hashed_secret",
    )
    db_session.add(active_user)
    db_session.flush()

    active_profile = ProviderProfile(
        user_id=active_user.id,
        business_name="Active Biz",
        provider_type="homestay",
        phone="0774444444",
        district="Kandy",
        approval_status="approved",
    )
    db_session.add(active_profile)
    db_session.commit()

    # Pending token
    token_pending = issue_test_auth_token(db_session, "pending_prov@ceylongo.com")
    res_pending = client.get(
        "/api/v1/providers/registration/draft",
        headers={"Authorization": f"Bearer {token_pending}"},
    )
    assert res_pending.status_code == 400
    assert "pending admin review" in res_pending.json()["detail"]

    # Active token
    token_active = issue_test_auth_token(db_session, "active_prov@ceylongo.com")
    res_active = client.get(
        "/api/v1/providers/registration/draft",
        headers={"Authorization": f"Bearer {token_active}"},
    )
    assert res_active.status_code == 400
    assert "Account is already active" in res_active.json()["detail"]


def test_rejected_provider_wrong_purpose_blocked(client: TestClient, db_session: Session):
    user = User(
        full_name="Rejected User",
        email="rejected_wrong_purpose@ceylongo.com",
        role="provider",
        is_active=False,
    )
    db_session.add(user)
    db_session.flush()

    profile = ProviderProfile(
        user_id=user.id,
        business_name="Rejected Biz",
        provider_type="homestay",
        phone="0775555555",
        district="Galle",
        approval_status="rejected",
    )
    db_session.add(profile)
    db_session.commit()

    # Token issued with purpose "registration" instead of "resubmission"
    wrong_token = issue_test_auth_token(
        db_session, "rejected_wrong_purpose@ceylongo.com", purpose="registration"
    )
    res = client.get(
        "/api/v1/providers/registration/draft",
        headers={"Authorization": f"Bearer {wrong_token}"},
    )
    assert res.status_code == 400
    assert "purpose for resubmission" in res.json()["detail"]

