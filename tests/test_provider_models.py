"""
Automated unit tests for Phase 2A Provider Registration Database Foundation.

Covers:
1. Backward compatibility of ProviderProfile with Phase 1 fields.
2. ProviderProfile with all extended fields (personal, business, identity files).
3. ProviderProfile relationships (user, reviewer, user.provider_profile backref).
4. ProviderEmailOtp model creation, fields, and default values.
5. ProviderEmailOtp distinct verification purposes and attempt tracking.
6. ProviderRegistrationAuthorization creation, defaults, token hash uniqueness, and lifecycle.
7. Existing RBAC dependencies compatibility with the extended model.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.dependencies import (
    require_approved_provider,
    require_provider,
)
from app.models.provider_email_otp import ProviderEmailOtp
from app.models.provider_profile import ProviderProfile
from app.models.provider_registration_authorization import (
    ProviderRegistrationAuthorization,
)
from app.models.user import Base, User

# In-memory SQLite engine with sysutcdatetime simulation for SQL Server compatibility
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


@pytest.fixture
def db_session():
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


# ==============================================================================
# 1. ProviderProfile Backward Compatibility & Extended Fields
# ==============================================================================


def test_provider_profile_backward_compatibility(db_session: Session):
    """Test 1: Existing minimal profile creation succeeds; new extended fields default to None."""
    user = User(
        full_name="Legacy Provider",
        email="legacy@ceylongo.com",
        password_hash=None,
        role="provider",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    # Create profile using only initial Phase 1 fields
    profile = ProviderProfile(
        user_id=user.id,
        business_name="Southern Breeze",
        provider_type="homestay",
        phone="0771234567",
        district="Galle",
        approval_status="pending",
    )
    db_session.add(profile)
    db_session.commit()
    db_session.refresh(profile)

    assert profile.id is not None
    assert profile.business_name == "Southern Breeze"
    assert profile.provider_type == "homestay"
    assert profile.phone == "0771234567"
    assert profile.district == "Galle"
    assert profile.approval_status == "pending"

    # All new fields should safely be None
    assert profile.nic_number is None
    assert profile.secondary_phone is None
    assert profile.permanent_province is None
    assert profile.permanent_district is None
    assert profile.permanent_city is None
    assert profile.permanent_landmark is None
    assert profile.permanent_postal_code is None
    assert profile.business_province is None
    assert profile.business_district is None
    assert profile.business_city is None
    assert profile.business_landmark is None
    assert profile.business_postal_code is None
    assert profile.business_registration_number is None
    assert profile.selfie_image_path is None
    assert profile.nic_front_image_path is None
    assert profile.nic_back_image_path is None
    assert profile.reviewed_by is None
    assert profile.reviewed_at is None
    assert profile.rejection_reason is None


def test_provider_profile_with_all_extended_fields(db_session: Session):
    """Test 2: Complete provider profile with personal, business, and document fields persists cleanly."""
    user = User(
        full_name="Sunil Perera",
        email="sunil.perera@ceylongo.com",
        password_hash=None,
        role="provider",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    profile = ProviderProfile(
        user_id=user.id,
        business_name="Sunil's Sigiriya Homestay & Safari",
        provider_type="homestay",
        phone="0771234567",
        secondary_phone="0719876543",
        district="Matale",
        description="Authentic homestay at the foot of Sigiriya Rock Fortress.",
        business_registration_number="PV123456",
        # Personal details
        nic_number="199012345678",
        # Permanent address
        permanent_province="Central Province",
        permanent_district="Matale",
        permanent_city="Dambulla",
        permanent_landmark="Near Golden Temple",
        permanent_postal_code="21100",
        # Business address
        business_province="Central Province",
        business_district="Matale",
        business_city="Sigiriya",
        business_landmark="100m from Lion Rock Entrance",
        business_postal_code="21112",
        # Identity verification file paths (secure internal references)
        selfie_image_path="uploads/providers/1/selfie_abc123.jpg",
        nic_front_image_path="uploads/providers/1/nic_front_def456.jpg",
        nic_back_image_path="uploads/providers/1/nic_back_ghi789.jpg",
        approval_status="pending",
    )
    db_session.add(profile)
    db_session.commit()
    db_session.refresh(profile)

    assert profile.nic_number == "199012345678"
    assert profile.secondary_phone == "0719876543"
    assert profile.permanent_province == "Central Province"
    assert profile.permanent_city == "Dambulla"
    assert profile.business_city == "Sigiriya"
    assert profile.business_registration_number == "PV123456"
    assert profile.selfie_image_path == "uploads/providers/1/selfie_abc123.jpg"
    assert profile.nic_front_image_path == "uploads/providers/1/nic_front_def456.jpg"
    assert profile.nic_back_image_path == "uploads/providers/1/nic_back_ghi789.jpg"


def test_provider_profile_relationships(db_session: Session):
    """Test 3: ProviderProfile relationships with User and Reviewer work bi-directionally."""
    admin = User(
        full_name="Admin Reviewer",
        email="admin@ceylongo.com",
        password_hash="hashed_admin_pwd",
        role="admin",
        is_active=True,
    )
    provider_user = User(
        full_name="Kusal Mendis",
        email="kusal@ceylongo.com",
        password_hash=None,
        role="provider",
        is_active=True,
    )
    db_session.add_all([admin, provider_user])
    db_session.commit()

    now = datetime.now(UTC)
    profile = ProviderProfile(
        user_id=provider_user.id,
        business_name="Kusal Tours",
        provider_type="tour_guide",
        phone="0775556666",
        district="Kandy",
        approval_status="approved",
        reviewed_by=admin.id,
        reviewed_at=now,
    )
    db_session.add(profile)
    db_session.commit()
    db_session.refresh(profile)

    # Relationship tests
    assert profile.user.id == provider_user.id
    assert profile.user.full_name == "Kusal Mendis"
    assert provider_user.provider_profile.id == profile.id
    assert provider_user.provider_profile.business_name == "Kusal Tours"

    assert profile.reviewer is not None
    assert profile.reviewer.id == admin.id
    assert profile.reviewer.full_name == "Admin Reviewer"


# ==============================================================================
# 2. ProviderEmailOtp Model Tests
# ==============================================================================


def test_provider_email_otp_creation_and_defaults(db_session: Session):
    """Test 4: ProviderEmailOtp creates with proper defaults and secure hash storage."""
    expires_at = datetime.now(UTC) + timedelta(minutes=15)
    otp = ProviderEmailOtp(
        email="newprovider@ceylongo.com",
        otp_hash="argon2_hashed_or_sha256_mock_value",
        purpose="registration",
        expires_at=expires_at,
    )
    db_session.add(otp)
    db_session.commit()
    db_session.refresh(otp)

    assert otp.id is not None
    assert otp.email == "newprovider@ceylongo.com"
    assert otp.otp_hash == "argon2_hashed_or_sha256_mock_value"
    assert otp.purpose == "registration"
    assert otp.attempts_count == 0
    assert otp.max_attempts == 5
    assert otp.is_used is False
    assert otp.used_at is None
    assert otp.created_at is not None


def test_provider_email_otp_purposes_and_lifecycle(db_session: Session):
    """Test 5: ProviderEmailOtp tracks distinct purposes, attempt increments, and one-time consumption."""
    purposes = ["registration", "resubmission", "password_setup"]
    for purpose in purposes:
        otp = ProviderEmailOtp(
            email=f"{purpose}@ceylongo.com",
            otp_hash=f"hash_for_{purpose}",
            purpose=purpose,
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
        )
        db_session.add(otp)
    db_session.commit()

    # Query and test consumption lifecycle
    reg_otp = (
        db_session.query(ProviderEmailOtp)
        .filter_by(purpose="registration")
        .first()
    )
    assert reg_otp is not None

    # Simulate 2 failed attempts
    reg_otp.attempts_count += 2
    db_session.commit()
    assert reg_otp.attempts_count == 2

    # Simulate successful verification
    now = datetime.now(UTC)
    reg_otp.is_used = True
    reg_otp.used_at = now
    db_session.commit()

    updated = (
        db_session.query(ProviderEmailOtp)
        .filter_by(purpose="registration")
        .first()
    )
    assert updated.is_used is True
    assert updated.used_at is not None


# ==============================================================================
# 3. ProviderRegistrationAuthorization Model Tests
# ==============================================================================


def test_provider_registration_authorization_creation_and_defaults(
    db_session: Session,
):
    """Test 6: Registration authorization token hash persists with secure defaults."""
    expires_at = datetime.now(UTC) + timedelta(hours=1)
    auth_rec = ProviderRegistrationAuthorization(
        email="verified@ceylongo.com",
        token_hash="a1b2c3d4e5f67890abcdef1234567890abcdef1234567890abcdef1234567890",
        purpose="registration",
        expires_at=expires_at,
    )
    db_session.add(auth_rec)
    db_session.commit()
    db_session.refresh(auth_rec)

    assert auth_rec.id is not None
    assert auth_rec.email == "verified@ceylongo.com"
    assert len(auth_rec.token_hash) == 64
    assert auth_rec.purpose == "registration"
    assert auth_rec.is_consumed is False
    assert auth_rec.consumed_at is None
    assert auth_rec.is_revoked is False
    assert auth_rec.revoked_at is None
    assert auth_rec.created_at is not None


def test_provider_registration_authorization_token_hash_uniqueness(
    db_session: Session,
):
    """Test 7: Token hash must be unique across authorization records."""
    token_hash = "unique_hash_64_characters_example_1234567890abcdef1234567890abcdef"
    rec1 = ProviderRegistrationAuthorization(
        email="provider1@ceylongo.com",
        token_hash=token_hash,
        purpose="registration",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    db_session.add(rec1)
    db_session.commit()

    rec2 = ProviderRegistrationAuthorization(
        email="provider2@ceylongo.com",
        token_hash=token_hash,
        purpose="registration",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    db_session.add(rec2)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_provider_registration_authorization_consumption_and_revocation(
    db_session: Session,
):
    """Test 8: Authorization records can be consumed or revoked independently."""
    # Record to consume
    rec_consume = ProviderRegistrationAuthorization(
        email="consume@ceylongo.com",
        token_hash="hash_to_consume_1234567890abcdef1234567890abcdef1234567890abcdef12",
        purpose="registration",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    # Record to revoke
    rec_revoke = ProviderRegistrationAuthorization(
        email="revoke@ceylongo.com",
        token_hash="hash_to_revoke_1234567890abcdef1234567890abcdef1234567890abcdef123",
        purpose="resubmission",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    db_session.add_all([rec_consume, rec_revoke])
    db_session.commit()

    now = datetime.now(UTC)
    rec_consume.is_consumed = True
    rec_consume.consumed_at = now

    rec_revoke.is_revoked = True
    rec_revoke.revoked_at = now
    db_session.commit()

    c = (
        db_session.query(ProviderRegistrationAuthorization)
        .filter_by(email="consume@ceylongo.com")
        .first()
    )
    assert c.is_consumed is True
    assert c.is_revoked is False

    r = (
        db_session.query(ProviderRegistrationAuthorization)
        .filter_by(email="revoke@ceylongo.com")
        .first()
    )
    assert r.is_revoked is True
    assert r.is_consumed is False


# ==============================================================================
# 4. Existing RBAC Compatibility with Extended Profile
# ==============================================================================


def test_existing_rbac_compatibility_with_extended_profile(db_session: Session):
    """Test 9: Existing require_provider and require_approved_provider work with extended profile."""
    provider_user = User(
        full_name="RBAC Verified Provider",
        email="rbac.provider@ceylongo.com",
        password_hash=None,
        role="provider",
        is_active=True,
    )
    db_session.add(provider_user)
    db_session.commit()

    # require_provider passes
    checked_user = require_provider(current_user=provider_user)
    assert checked_user.id == provider_user.id

    # Create extended profile with approved status
    profile = ProviderProfile(
        user_id=provider_user.id,
        business_name="RBAC Homestay",
        provider_type="homestay",
        phone="0779998888",
        district="Galle",
        nic_number="199512345678",
        permanent_province="Southern Province",
        business_province="Southern Province",
        approval_status="approved",
    )
    db_session.add(profile)
    db_session.commit()

    # require_approved_provider passes and returns profile with extended attributes accessible
    approved_profile = require_approved_provider(
        current_user=provider_user, db=db_session
    )
    assert approved_profile.id == profile.id
    assert approved_profile.nic_number == "199512345678"
    assert approved_profile.permanent_province == "Southern Province"
    assert approved_profile.approval_status == "approved"
