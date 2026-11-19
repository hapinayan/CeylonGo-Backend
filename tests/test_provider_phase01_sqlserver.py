"""Opt-in SQL Server checks against the approved, isolated integration database."""

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select, text, update
from sqlalchemy.orm import Session

from app.core.database_guard import verify_safe_integration_database
from app.core.otp import hash_provider_otp
from app.core.provider_onboarding import lock_onboarding
from app.database.connection import DATABASE_URL
from app.models.activity import Activity
from app.models.activity_slot import ActivitySlot
from app.models.provider_email_otp import ProviderEmailOtp
from app.models.provider_profile import ProviderProfile
from app.models.provider_registration_authorization import (
    ProviderRegistrationAuthorization,
)
from app.models.provider_registration_draft import ProviderRegistrationDraft
from app.models.shared import AdminAuditLog
from app.models.user import User
from app.routers.activity_bookings import create_activity_booking
from app.routers.activity_slots import (
    list_provider_activity_slots,
    list_public_activity_slots,
)
from app.routers.admin_providers import (
    approve_provider_application,
    reject_provider_application,
)
from app.routers.provider_auth import setup_provider_password
from app.routers.provider_otp import verify_provider_otp_endpoint
from app.routers.provider_registration import submit_provider_registration
from app.schemas.activity_booking import ActivityBookingCreate
from app.schemas.admin_providers import RejectProviderRequest
from app.schemas.provider_auth import ProviderPasswordSetupRequest
from app.schemas.provider_otp import VerifyOtpRequest

TARGET = os.getenv("CEYLONGO_PROVIDER_TEST_DB", "CeylonGoDB_IntegrationTest")
pytestmark = pytest.mark.skipif(
    os.getenv("PROVIDER_PHASE01_SQLSERVER") != "1",
    reason="Enable explicitly for the isolated SQL Server test database",
)


@pytest.fixture
def engine():
    verify_safe_integration_database(TARGET, {"CeylonGoDB_IntegrationTest", "CeylonGoDB_MergeTest"})
    db_engine = create_engine(
        DATABASE_URL.set(database=TARGET),
        connect_args={"timeout": 5},
        hide_parameters=True,
    )
    try:
        yield db_engine
    finally:
        db_engine.dispose()


def verify_target(connection):
    assert connection.scalar(text("SELECT DB_NAME()")) == TARGET
    connection.exec_driver_sql("SET LOCK_TIMEOUT 6000")


def make_admin(db):
    admin = User(
        email=f"sql-admin-{uuid4().hex}@example.test",
        full_name="Isolated SQL Server administrator",
        role="admin",
        is_active=True,
    )
    db.add(admin)
    db.flush()
    return admin


@pytest.fixture
def transaction(engine):
    with engine.connect() as connection:
        outer = connection.begin()
        verify_target(connection)
        try:
            with Session(
                bind=connection,
                join_transaction_mode="create_savepoint",
                expire_on_commit=False,
            ) as db:
                yield db
        finally:
            assert outer.is_active, "The enclosing fixture transaction was lost"
            outer.rollback()


def test_activity_public_slots_booking_and_ownership_on_sqlserver(transaction):
    db = transaction
    suffix = uuid4().hex
    owner = User(
        full_name="Phase 01 Activity Owner",
        email=f"sql-a-{suffix}@example.test",
        role="provider",
        is_active=True,
    )
    buyer = User(
        full_name="Phase 01 Traveler",
        email=f"sql-t-{suffix}@example.test",
        role="traveler",
        is_active=True,
    )
    db.add_all([owner, buyer])
    db.flush()
    profile = ProviderProfile(
        user_id=owner.id,
        business_name="Phase 01 Activity",
        provider_type="activity",
        approval_status="approved",
        district="Colombo",
        phone="0771234567",
    )
    db.add(profile)
    db.flush()
    activity = Activity(
        provider_profile_id=profile.id,
        title=f"Phase 01 Activity {suffix}",
        description="Isolated predicate verification",
        category="cultural",
        district="Colombo",
        city="Fort",
        meeting_point="Public entrance",
        duration_minutes=60,
        pricing_type="per_person",
        price=100,
        currency="LKR",
        approval_status="approved",
        is_active=True,
    )
    db.add(activity)
    db.flush()
    start = datetime.now(UTC).replace(tzinfo=None) + timedelta(days=1)
    active = ActivitySlot(
        activity_id=activity.id,
        starts_at=start,
        ends_at=start + timedelta(hours=1),
        capacity=8,
        is_active=True,
    )
    inactive = ActivitySlot(
        activity_id=activity.id,
        starts_at=start + timedelta(hours=2),
        ends_at=start + timedelta(hours=3),
        capacity=8,
        is_active=False,
    )
    db.add_all([active, inactive])
    db.commit()
    assert [row.id for row in list_public_activity_slots(activity.id, db)] == [
        active.id
    ]
    assert len(list_provider_activity_slots(activity.id, profile, db)) == 2
    with pytest.raises(HTTPException) as foreign_owner:
        list_provider_activity_slots(activity.id, ProviderProfile(id=0), db)
    assert foreign_owner.value.status_code in (403, 404)
    booking = create_activity_booking(
        ActivityBookingCreate(
            activity_slot_id=active.id,
            participants=2,
            payment_method="pay_at_location",
        ),
        buyer,
        db,
    )
    assert booking.participants == 2
    db.refresh(active)
    assert active.reserved_count == 2
    assert active.eco_experience_id is None
    db.execute(update(User).where(User.id == owner.id).values(is_active=False))
    db.commit()
    with pytest.raises(HTTPException) as suspended:
        list_public_activity_slots(activity.id, db)
    assert suspended.value.status_code == 404


def test_otp_sqlserver_consumption_and_replay(transaction):
    db = transaction
    email = f"sql-otp-{uuid4().hex}@example.com"
    otp = "456728"
    challenge = ProviderEmailOtp(
        email=email,
        otp_hash=hash_provider_otp(email, otp),
        purpose="registration",
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )
    db.add(challenge)
    db.commit()
    response = verify_provider_otp_endpoint(VerifyOtpRequest(email=email, otp=otp), db)
    assert response.verified and response.authorization_token
    with pytest.raises(HTTPException) as replay:
        verify_provider_otp_endpoint(VerifyOtpRequest(email=email, otp=otp), db)
    assert replay.value.status_code == 400
    assert (
        db.scalar(
            select(ProviderEmailOtp.is_used).where(
                ProviderEmailOtp.id == challenge.id,
            )
        )
        is True
    )


def test_registration_duplicate_review_and_activation_sqlserver(
    transaction, monkeypatch
):
    from datetime import date

    db = transaction
    monkeypatch.setattr(
        "app.routers.admin_providers.send_provider_approval_email", lambda **_: True
    )
    suffix = uuid4().hex
    email = f"sql-register-{suffix}@example.com"
    identity = f"{int(suffix[:12], 16) % 10**12:012d}"
    admin = make_admin(db)

    def draft_with_grant(address):
        draft = ProviderRegistrationDraft(
            email=address,
            full_name="Phase 01 Provider",
            primary_phone="0771234567",
            identity_document_type="nic",
            nic_number=identity,
            date_of_birth=date(1990, 5, 1),
            gender="male",
            nationality="Sri Lankan",
            permanent_province="Western",
            permanent_district="Colombo",
            permanent_city="Colombo",
            permanent_landmark="Test road",
            permanent_postal_code="00100",
            business_name="Phase 01 Fixture",
            provider_category="activity",
            business_province="Western",
            business_district="Colombo",
            business_city="Colombo",
            business_postal_code="00100",
            business_description="Isolated test provider",
            selfie_file_reference="fixture-selfie.jpg",
            nic_front_file_reference="fixture-front.jpg",
            nic_back_file_reference="fixture-back.jpg",
            registration_step="review",
        )
        grant = ProviderRegistrationAuthorization(
            email=address,
            purpose="registration",
            token_hash=uuid4().hex + uuid4().hex,
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
        )
        db.add_all([draft, grant])
        db.commit()
        return grant

    grant = draft_with_grant(email)
    assert submit_provider_registration(grant, db).submitted
    with pytest.raises(HTTPException) as replay:
        submit_provider_registration(grant, db)
    assert replay.value.status_code == 401
    duplicate = draft_with_grant(f"sql-duplicate-{suffix}@example.com")
    with pytest.raises(HTTPException) as duplicate_doc:
        submit_provider_registration(duplicate, db)
    assert duplicate_doc.value.status_code == 409
    db.rollback()

    user = db.scalar(select(User).where(User.email == email))
    assert user is not None and not user.is_active
    profile = db.scalar(
        select(ProviderProfile).where(ProviderProfile.user_id == user.id)
    )
    assert profile.approval_status == "pending"
    setup = ProviderRegistrationAuthorization(
        email=email,
        purpose="password_setup",
        token_hash=uuid4().hex + uuid4().hex,
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )
    db.add(setup)
    db.commit()
    password = ProviderPasswordSetupRequest(
        password="SafeSqlPass123!", confirm_password="SafeSqlPass123!"
    )
    with pytest.raises(HTTPException) as unapproved:
        setup_provider_password(password, (setup, user, profile), db)
    assert unapproved.value.status_code == 400
    assert (
        approve_provider_application(profile.id, admin, db).approval_status
        == "approved"
    )
    with pytest.raises(HTTPException) as conflicting:
        reject_provider_application(
            profile.id, RejectProviderRequest(reason="Conflicting review"), admin, db
        )
    assert conflicting.value.status_code == 400
    assert (
        db.scalar(
            select(AdminAuditLog.action).where(
                AdminAuditLog.entity_name == "provider_profiles",
                AdminAuditLog.entity_id == profile.id,
            )
        )
        == "provider_approved"
    )
    assert setup_provider_password(
        password, (setup, user, profile), db
    ).account_activated
    db.refresh(user)
    assert user.is_active and user.password_hash
    with pytest.raises(HTTPException) as activation_replay:
        setup_provider_password(password, (setup, user, profile), db)
    assert activation_replay.value.status_code == 401


def test_onboarding_lock_conflicts_and_releases_on_sqlserver(engine):
    email = f"sql-lock-{uuid4().hex}@example.test"
    with engine.connect() as first, engine.connect() as second:
        verify_target(first)
        verify_target(second)
        first.commit()
        second.commit()
        with Session(bind=first) as writer, Session(bind=second) as contender:
            lock_onboarding(writer, email)
            with pytest.raises(HTTPException) as busy:
                lock_onboarding(contender, email)
            assert busy.value.status_code == 409
            writer.rollback()
            lock_onboarding(contender, email)
            contender.rollback()
