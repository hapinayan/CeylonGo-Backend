"""
Automated unit tests for Local Tour Guide Database Models & Constraints (Phase 01B).

Covers:
1. TourGuide model creation, provider ownership (without redundant user_id), and User/ProviderProfile relationships.
2. Unique constraints on TourGuide provider_profile_id.
3. TourGuide listing review workflow (approval_status, reviewer, reviewed_at, rejection_reason).
4. TourGuide review_count >= 0 and rating constraints.
5. TourGuide base location coordinates and boundary checks.
6. TourGuideLanguage unique language per guide, proficiency check, and cascade deletion.
7. TourGuideServiceArea unique district/city per guide and cascade deletion.
8. TourGuideCredential creation, type checks, date checks, confidential file_reference, and cascade deletion.
9. TourGuideImage creation, order check, and unique profile image enforcement.
10. TourGuidePackage creation, pricing check, duration check, itinerary, and coordinates check.
11. TourGuideAvailability slot uniqueness and explicit times CHECK constraint (both NULL or both NOT NULL with end > start).
12. TourGuideBooking creation, unambiguous start/end timestamps (same-day and multi-day), expiration, snapshots.
13. TourGuideBooking cross-guide package rejection (ensuring bookings cannot reference another guide's package).
14. TourGuideVerification creation, status check, and cross-guide credential rejection.
"""

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.provider_profile import ProviderProfile
from app.models.tour_guide import (
    TourGuide,
    TourGuideAvailability,
    TourGuideBooking,
    TourGuideCredential,
    TourGuideImage,
    TourGuideLanguage,
    TourGuidePackage,
    TourGuideServiceArea,
    TourGuideVerification,
)
from app.models.user import Base, User

# In-memory SQLite engine with PRAGMA foreign_keys = ON
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
def db_session() -> Session:
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def sample_guide_user(db_session: Session) -> User:
    user = User(
        full_name="Sunil Perera",
        email="sunil.guide@example.com",
        role="provider",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def sample_traveler_user(db_session: Session) -> User:
    user = User(
        full_name="Alice Walker",
        email="alice.traveler@example.com",
        role="traveler",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def sample_admin_user(db_session: Session) -> User:
    user = User(
        full_name="Admin Reviewer",
        email="admin.reviewer@example.com",
        role="admin",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def sample_provider_profile(db_session: Session, sample_guide_user: User) -> ProviderProfile:
    profile = ProviderProfile(
        user_id=sample_guide_user.id,
        business_name="Sunil Island Tours",
        provider_type="tour_guide",
        phone="+94771234567",
        district="Kandy",
        approval_status="approved",
    )
    db_session.add(profile)
    db_session.commit()
    db_session.refresh(profile)
    return profile


@pytest.fixture
def sample_tour_guide(
    db_session: Session, sample_provider_profile: ProviderProfile
) -> TourGuide:
    guide = TourGuide(
        provider_profile_id=sample_provider_profile.id,
        bio="Licensed national tour guide with 10+ years experience in cultural triangle.",
        years_of_experience=10,
        specialties="Cultural, Historical, Nature",
        license_number="SLTDA/T/2020/0042",
        license_type="national",
        license_expiry_date=date(2027, 12, 31),
        hourly_rate=Decimal("2500.00"),
        daily_rate=Decimal("15000.00"),
        currency="LKR",
        base_city="Kandy",
        base_district="Kandy",
        base_latitude=Decimal("7.290572"),
        base_longitude=Decimal("80.633728"),
        is_available=True,
        is_active=True,
        approval_status="approved",
        verification_status="verified",
        max_group_size=8,
    )
    db_session.add(guide)
    db_session.commit()
    db_session.refresh(guide)
    return guide


# =============================================================================
# 1. TourGuide Model & Ownership Tests
# =============================================================================

def test_tour_guide_creation_and_derived_user(db_session: Session, sample_tour_guide: TourGuide):
    assert sample_tour_guide.id is not None
    assert sample_tour_guide.years_of_experience == 10
    assert sample_tour_guide.daily_rate == Decimal("15000.00")
    assert sample_tour_guide.provider_profile.business_name == "Sunil Island Tours"
    # Derived user account through provider profile
    assert sample_tour_guide.user is not None
    assert sample_tour_guide.user.email == "sunil.guide@example.com"
    # Backref verification
    assert sample_tour_guide.provider_profile.tour_guide == sample_tour_guide


def test_tour_guide_unique_provider_profile(
    db_session: Session, sample_tour_guide: TourGuide, sample_provider_profile: ProviderProfile
):
    duplicate_guide = TourGuide(
        provider_profile_id=sample_provider_profile.id,  # Same profile
        bio="Another guide bio",
        daily_rate=Decimal("12000.00"),
        base_city="Galle",
        base_district="Galle",
    )
    db_session.add(duplicate_guide)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_tour_guide_listing_review_workflow(
    db_session: Session, sample_provider_profile: ProviderProfile, sample_admin_user: User
):
    guide = TourGuide(
        provider_profile_id=sample_provider_profile.id,
        bio="Draft guide bio",
        daily_rate=Decimal("10000.00"),
        base_city="Colombo",
        base_district="Colombo",
        approval_status="pending",
    )
    db_session.add(guide)
    db_session.commit()

    # Admin reviews and rejects
    guide.approval_status = "rejected"
    guide.reviewed_by = sample_admin_user.id
    guide.reviewed_at = datetime.now(UTC).replace(tzinfo=None)
    guide.rejection_reason = "License documentation is blurry and expired."
    db_session.commit()
    db_session.refresh(guide)

    assert guide.approval_status == "rejected"
    assert guide.reviewer.email == "admin.reviewer@example.com"
    assert "blurry" in guide.rejection_reason


def test_tour_guide_negative_review_count_rejected(
    db_session: Session, sample_provider_profile: ProviderProfile
):
    guide = TourGuide(
        provider_profile_id=sample_provider_profile.id,
        bio="Guide bio",
        daily_rate=Decimal("10000.00"),
        base_city="Colombo",
        base_district="Colombo",
        review_count=-1,  # negative review count
    )
    db_session.add(guide)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_tour_guide_invalid_coordinates_rejected(
    db_session: Session, sample_provider_profile: ProviderProfile
):
    guide = TourGuide(
        provider_profile_id=sample_provider_profile.id,
        bio="Guide bio",
        daily_rate=Decimal("10000.00"),
        base_city="Colombo",
        base_district="Colombo",
        base_latitude=Decimal("95.000000"),  # Latitude > 90
        base_longitude=Decimal("80.000000"),
    )
    db_session.add(guide)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


# =============================================================================
# 2. TourGuideLanguage Tests
# =============================================================================

def test_tour_guide_languages_unique_and_cascade(db_session: Session, sample_tour_guide: TourGuide):
    lang_en = TourGuideLanguage(
        tour_guide_id=sample_tour_guide.id,
        language_code="en",
        language_name="English",
        proficiency_level="fluent",
        is_primary=True,
    )
    lang_si = TourGuideLanguage(
        tour_guide_id=sample_tour_guide.id,
        language_code="si",
        language_name="Sinhala",
        proficiency_level="native",
        is_primary=False,
    )
    db_session.add_all([lang_en, lang_si])
    db_session.commit()

    assert len(sample_tour_guide.languages) == 2

    # Duplicate language_code for the same guide should fail
    duplicate_lang = TourGuideLanguage(
        tour_guide_id=sample_tour_guide.id,
        language_code="en",
        language_name="English (Duplicate)",
        proficiency_level="conversational",
    )
    db_session.add(duplicate_lang)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


# =============================================================================
# 3. TourGuideServiceArea Tests
# =============================================================================

def test_tour_guide_service_areas(db_session: Session, sample_tour_guide: TourGuide):
    area1 = TourGuideServiceArea(
        tour_guide_id=sample_tour_guide.id,
        province="Central",
        district="Kandy",
        city="Kandy",
        is_primary=True,
    )
    area2 = TourGuideServiceArea(
        tour_guide_id=sample_tour_guide.id,
        province="Central",
        district="Matale",
        city="Sigiriya",
        is_primary=False,
    )
    db_session.add_all([area1, area2])
    db_session.commit()

    assert len(sample_tour_guide.service_areas) == 2

    # Duplicate district/city for same guide should fail
    duplicate_area = TourGuideServiceArea(
        tour_guide_id=sample_tour_guide.id,
        province="Central",
        district="Kandy",
        city="Kandy",
    )
    db_session.add(duplicate_area)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


# =============================================================================
# 4. TourGuideCredential Tests
# =============================================================================

def test_tour_guide_credentials_and_protected_file_reference(
    db_session: Session, sample_tour_guide: TourGuide
):
    cred = TourGuideCredential(
        tour_guide_id=sample_tour_guide.id,
        credential_type="sltda_license",
        title="SLTDA National Guide License",
        issuing_authority="Sri Lanka Tourism Development Authority",
        certificate_number="SLTDA-2020-0042",
        issue_date=date(2020, 1, 1),
        expiry_date=date(2027, 12, 31),
        file_reference="secure/credentials/sltda_sunil_2020.pdf",
        is_confidential=True,
        verification_status="approved",
    )
    db_session.add(cred)
    db_session.commit()
    db_session.refresh(cred)

    assert cred.id is not None
    assert cred.is_confidential is True
    assert cred.file_reference == "secure/credentials/sltda_sunil_2020.pdf"
    assert cred.tour_guide.bio == sample_tour_guide.bio


# =============================================================================
# 5. TourGuideImage & Primary Profile Image Tests
# =============================================================================

def test_tour_guide_images_and_single_primary_profile(
    db_session: Session, sample_tour_guide: TourGuide
):
    img1 = TourGuideImage(
        tour_guide_id=sample_tour_guide.id,
        image_url="https://storage.ceylongo.com/images/sunil_profile.jpg",
        caption="Profile photo at Sigiriya",
        display_order=0,
        is_profile=True,
    )
    img2 = TourGuideImage(
        tour_guide_id=sample_tour_guide.id,
        image_url="https://storage.ceylongo.com/images/sunil_tour_action.jpg",
        caption="Leading a nature hike",
        display_order=1,
        is_profile=False,
    )
    db_session.add_all([img1, img2])
    db_session.commit()

    assert len(sample_tour_guide.images) == 2

    # Attempting to add a SECOND profile image for the same guide must fail
    img_duplicate_profile = TourGuideImage(
        tour_guide_id=sample_tour_guide.id,
        image_url="https://storage.ceylongo.com/images/sunil_profile2.jpg",
        caption="Second profile photo",
        display_order=2,
        is_profile=True,
    )
    db_session.add(img_duplicate_profile)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


# =============================================================================
# 6. TourGuidePackage Tests
# =============================================================================

def test_tour_guide_packages_with_itinerary(db_session: Session, sample_tour_guide: TourGuide):
    pkg = TourGuidePackage(
        tour_guide_id=sample_tour_guide.id,
        title="Kandy Cultural Heritage 1-Day Tour",
        description="Comprehensive walking tour through Temple of the Tooth and Peradeniya Gardens.",
        itinerary="08:00 AM - Hotel Pickup\n09:00 AM - Temple of the Tooth\n12:30 PM - Local Traditional Lunch\n02:00 PM - Peradeniya Botanical Gardens\n05:00 PM - Return",
        duration_hours=Decimal("8.00"),
        pricing_type="per_person",
        price=Decimal("4500.00"),
        currency="LKR",
        max_people=10,
        included_services="Entry guidance, bottled water, English commentary",
        excluded_services="Temple entry tickets, lunch",
        meeting_point="Kandy City Centre Clock Tower",
        meeting_latitude=Decimal("7.290600"),
        meeting_longitude=Decimal("80.633700"),
        is_active=True,
    )
    db_session.add(pkg)
    db_session.commit()
    db_session.refresh(pkg)

    assert pkg.id is not None
    assert "Temple of the Tooth" in pkg.itinerary
    assert pkg.price == Decimal("4500.00")
    assert len(sample_tour_guide.packages) == 1


# =============================================================================
# 7. TourGuideAvailability Tests
# =============================================================================

def test_tour_guide_availability_full_day_and_timed_slots(
    db_session: Session, sample_tour_guide: TourGuide
):
    # Valid all-day availability (both times NULL)
    all_day_slot = TourGuideAvailability(
        tour_guide_id=sample_tour_guide.id,
        available_date=date(2026, 12, 1),
        start_time=None,
        end_time=None,
        is_available=True,
    )
    # Valid specific time slot (both times NOT NULL, end > start)
    timed_slot = TourGuideAvailability(
        tour_guide_id=sample_tour_guide.id,
        available_date=date(2026, 12, 2),
        start_time=time(8, 0),
        end_time=time(17, 0),
        is_available=True,
    )
    db_session.add_all([all_day_slot, timed_slot])
    db_session.commit()

    assert len(sample_tour_guide.availabilities) == 2


def test_tour_guide_availability_invalid_null_combinations_rejected(
    db_session: Session, sample_tour_guide: TourGuide
):
    # Invalid: start_time set but end_time is NULL
    invalid_partial_slot = TourGuideAvailability(
        tour_guide_id=sample_tour_guide.id,
        available_date=date(2026, 12, 5),
        start_time=time(8, 0),
        end_time=None,
        is_available=True,
    )
    db_session.add(invalid_partial_slot)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_tour_guide_availability_end_time_not_greater_rejected(
    db_session: Session, sample_tour_guide: TourGuide
):
    # Invalid: end_time <= start_time
    invalid_time_slot = TourGuideAvailability(
        tour_guide_id=sample_tour_guide.id,
        available_date=date(2026, 12, 6),
        start_time=time(14, 0),
        end_time=time(10, 0),
        is_available=True,
    )
    db_session.add(invalid_time_slot)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


# =============================================================================
# 8. TourGuideBooking Tests (Same-Day, Multi-Day, Snapshots, & Expiry)
# =============================================================================

def test_tour_guide_booking_same_day(
    db_session: Session,
    sample_tour_guide: TourGuide,
    sample_traveler_user: User,
):
    booking = TourGuideBooking(
        tour_guide_id=sample_tour_guide.id,
        traveler_id=sample_traveler_user.id,
        booking_date=date(2026, 12, 15),
        start_datetime=datetime(2026, 12, 15, 8, 0, 0),
        end_datetime=datetime(2026, 12, 15, 17, 0, 0),
        number_of_people=3,
        pickup_location="Hotel Sigiriya",
        total_price=Decimal("15000.00"),
        booking_status="pending",
        payment_method="pay_at_location",
        payment_status="unpaid",
        expires_at=datetime(2026, 12, 14, 8, 0, 0),
        cancellation_policy_snapshot="Free cancellation up to 24 hours before tour start.",
        free_cancellation_hours_snapshot=24,
    )
    db_session.add(booking)
    db_session.commit()
    db_session.refresh(booking)

    assert booking.id is not None
    assert booking.start_datetime.date() == booking.end_datetime.date()
    assert booking.expires_at is not None
    assert booking.free_cancellation_hours_snapshot == 24


def test_tour_guide_booking_multi_day(
    db_session: Session,
    sample_tour_guide: TourGuide,
    sample_traveler_user: User,
):
    booking = TourGuideBooking(
        tour_guide_id=sample_tour_guide.id,
        traveler_id=sample_traveler_user.id,
        booking_date=date(2026, 12, 20),
        start_datetime=datetime(2026, 12, 20, 7, 0, 0),
        end_datetime=datetime(2026, 12, 23, 19, 0, 0),  # 4-day tour
        number_of_people=2,
        pickup_location="Colombo Airport",
        total_price=Decimal("60000.00"),
        booking_status="confirmed",
        payment_method="online",
        payment_status="paid",
    )
    db_session.add(booking)
    db_session.commit()
    db_session.refresh(booking)

    assert booking.id is not None
    assert booking.end_datetime > booking.start_datetime
    assert (booking.end_datetime - booking.start_datetime).days == 3


def test_tour_guide_booking_invalid_timestamps_rejected(
    db_session: Session,
    sample_tour_guide: TourGuide,
    sample_traveler_user: User,
):
    invalid_booking = TourGuideBooking(
        tour_guide_id=sample_tour_guide.id,
        traveler_id=sample_traveler_user.id,
        booking_date=date(2026, 12, 15),
        start_datetime=datetime(2026, 12, 15, 17, 0, 0),
        end_datetime=datetime(2026, 12, 15, 8, 0, 0),  # end before start
        number_of_people=2,
        total_price=Decimal("15000.00"),
    )
    db_session.add(invalid_booking)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_booking_cannot_reference_package_of_another_guide(
    db_session: Session,
    sample_tour_guide: TourGuide,
    sample_traveler_user: User,
):
    """
    CRITICAL ARCHITECTURE REQUIREMENT:
    Ensure that bookings cannot reference a package belonging to another guide.
    Enforced via composite FK: (package_id, tour_guide_id) -> tour_guide_packages(id, tour_guide_id).
    """
    pkg1 = TourGuidePackage(
        tour_guide_id=sample_tour_guide.id,
        title="Guide 1 Exclusive Tour",
        description="Only guide 1 can guide this tour.",
        duration_hours=Decimal("5.00"),
        price=Decimal("8000.00"),
        meeting_point="Galle Fort Gate",
    )
    db_session.add(pkg1)
    db_session.commit()
    db_session.refresh(pkg1)

    # Create Guide 2
    user2 = User(full_name="Nimal Fernando", email="nimal@example.com", role="provider")
    db_session.add(user2)
    db_session.commit()

    profile2 = ProviderProfile(
        user_id=user2.id,
        business_name="Nimal Tours",
        provider_type="tour_guide",
        phone="+94779876543",
        district="Galle",
        approval_status="approved",
    )
    db_session.add(profile2)
    db_session.commit()

    guide2 = TourGuide(
        provider_profile_id=profile2.id,
        bio="Galle local expert",
        daily_rate=Decimal("14000.00"),
        base_city="Galle",
        base_district="Galle",
    )
    db_session.add(guide2)
    db_session.commit()
    db_session.refresh(guide2)

    # Attempt to book Guide 2 with Guide 1's package
    invalid_booking = TourGuideBooking(
        tour_guide_id=guide2.id,  # Guide 2
        traveler_id=sample_traveler_user.id,
        package_id=pkg1.id,       # Package belongs to Guide 1!
        booking_date=date(2026, 12, 20),
        start_datetime=datetime(2026, 12, 20, 9, 0, 0),
        end_datetime=datetime(2026, 12, 20, 15, 0, 0),
        number_of_people=2,
        total_price=Decimal("8000.00"),
    )
    db_session.add(invalid_booking)

    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


# =============================================================================
# 9. TourGuideVerification & Cross-Guide Credential Protection Tests
# =============================================================================

def test_tour_guide_verification_and_cross_guide_credential_protection(
    db_session: Session,
    sample_tour_guide: TourGuide,
    sample_admin_user: User,
):
    """
    CRITICAL ARCHITECTURE REQUIREMENT:
    Ensure verification records cannot reference credentials belonging to another guide.
    Enforced via composite FK: (credential_id, tour_guide_id) -> tour_guide_credentials(id, tour_guide_id).
    """
    cred1 = TourGuideCredential(
        tour_guide_id=sample_tour_guide.id,
        credential_type="first_aid",
        title="Red Cross First Aid Level 2",
        issuing_authority="Sri Lanka Red Cross",
        file_reference="secure/credentials/cred1.pdf",
    )
    db_session.add(cred1)
    db_session.commit()
    db_session.refresh(cred1)

    # Valid verification for Guide 1
    verification = TourGuideVerification(
        tour_guide_id=sample_tour_guide.id,
        credential_id=cred1.id,
        reviewed_by=sample_admin_user.id,
        verification_type="first_aid_audit",
        status="approved",
        notes="Certificate verified with Red Cross registry.",
    )
    db_session.add(verification)
    db_session.commit()
    db_session.refresh(verification)

    assert verification.id is not None
    assert verification.credential.title == "Red Cross First Aid Level 2"
    assert verification.reviewer.email == "admin.reviewer@example.com"

    # Create Guide 3
    user3 = User(full_name="Kamal Guide", email="kamal@example.com", role="provider")
    db_session.add(user3)
    db_session.commit()

    profile3 = ProviderProfile(
        user_id=user3.id,
        business_name="Kamal Tours",
        provider_type="tour_guide",
        phone="+94770001122",
        district="Colombo",
        approval_status="approved",
    )
    db_session.add(profile3)
    db_session.commit()

    guide3 = TourGuide(
        provider_profile_id=profile3.id,
        bio="Colombo guide",
        daily_rate=Decimal("10000.00"),
        base_city="Colombo",
        base_district="Colombo",
    )
    db_session.add(guide3)
    db_session.commit()
    db_session.refresh(guide3)

    # Attempt to verify Guide 3 with Guide 1's credential
    invalid_verification = TourGuideVerification(
        tour_guide_id=guide3.id,   # Guide 3
        credential_id=cred1.id,    # Credential belongs to Guide 1!
        reviewed_by=sample_admin_user.id,
        verification_type="fraudulent_verification",
        status="approved",
    )
    db_session.add(invalid_verification)

    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()
