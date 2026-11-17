"""
Automated tests for Module 04 P1.1A — Sponsored Promotions Database Models & Migration.

Covers:
1. test_package_creation_and_defaults: Create valid package and verify defaults.
2. test_package_check_constraints: Validate price >= 0, duration_days > 0, placement_tier enum.
3. test_package_unique_name: Name uniqueness constraint.
4. test_approved_package_seeding_idempotence: Verify 3 approved packages seed idempotently.
5. test_sponsored_promotion_defaults_and_relationships: Default unpaid & pending_approval, foreign keys.
6. test_sponsored_promotion_check_constraints: Invalid status, payment_status, date inverted.
7. test_active_consistency_constraint: campaign_status='active' rejected when unpaid or dates missing.
8. test_sponsored_promotion_cascade_delete: Deleting EcoExperience cascades to campaigns.
9. test_analytics_creation_and_uniqueness: Daily metrics uniqueness and non-negative counts.
10. test_analytics_cascade_delete: Deleting campaign cascades to analytics rows.
11. test_is_currently_active_property: Time-window and payment verification helper logic.
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.eco_experience import EcoExperience
from app.models.eco_promotion import (
    VALID_CAMPAIGN_STATUSES,
    VALID_PAYMENT_STATUSES,
    VALID_PLACEMENT_TIERS,
    EcoPromotionAnalytics,
    EcoPromotionPackage,
    EcoSponsoredPromotion,
)
from app.models.provider_profile import ProviderProfile
from app.models.user import Base, User

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
def seed_provider_and_experience(db_session: Session):
    user = User(
        full_name="Sugath Provider",
        email="sugath_promo@test.com",
        password_hash="hash_pw",
        role="provider",
        is_active=True,
        created_at=datetime.now(UTC),
    )
    admin = User(
        full_name="Admin Officer",
        email="admin_promo@test.com",
        password_hash="hash_admin_pw",
        role="admin",
        is_active=True,
        created_at=datetime.now(UTC),
    )
    db_session.add_all([user, admin])
    db_session.flush()

    profile = ProviderProfile(
        user_id=user.id,
        business_name="Sinharaja Eco Safaris",
        provider_type="eco_tourism",
        approval_status="approved",
        phone="+94771234567",
        district="Ratnapura",
        created_at=datetime.now(UTC),
    )
    db_session.add(profile)
    db_session.flush()

    exp = EcoExperience(
        provider_profile_id=profile.id,
        title="Sinharaja Canopy Rainforest Walk",
        eco_category="Forest & Nature Trails",
        description="Pristine nature experience.",
        itinerary="Day 1 trail walk.",
        duration_hours=Decimal("4.0"),
        district="Ratnapura",
        city="Kalawana",
        meeting_point="Forest Entrance",
        base_price=Decimal("45.00"),
        currency="USD",
        max_group_size=10,
        min_participants=2,
        min_age=8,
        environmental_guidelines="Strict leave no trace.",
        wildlife_safety_summary="Do not feed wild animals.",
        listing_status="approved",
        is_published=True,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db_session.add(exp)
    db_session.commit()

    return {
        "user": user,
        "admin": admin,
        "profile": profile,
        "experience": exp,
    }


# ==============================================================================
# 1. Package Model Tests
# ==============================================================================


def test_package_creation_and_defaults(db_session: Session):
    """Verify package creation, defaults, and attributes."""
    pkg = EcoPromotionPackage(
        name="Custom 7-Day Boost",
        description="Standard search visibility boost.",
        duration_days=7,
        price=Decimal("2500.00"),
        currency="LKR",
        placement_tier="standard",
        is_active=True,
    )
    db_session.add(pkg)
    db_session.commit()

    reloaded = db_session.query(EcoPromotionPackage).filter_by(id=pkg.id).first()
    assert reloaded is not None
    assert reloaded.name == "Custom 7-Day Boost"
    assert reloaded.duration_days == 7
    assert reloaded.price == Decimal("2500.00")
    assert reloaded.currency == "LKR"
    assert reloaded.placement_tier == "standard"
    assert reloaded.is_active is True
    assert reloaded.created_at is not None


@pytest.mark.parametrize(
    "invalid_kwargs",
    [
        {"price": Decimal("-10.00")},             # Negative price
        {"duration_days": 0},                     # Zero duration
        {"duration_days": -5},                    # Negative duration
        {"placement_tier": "unauthorized_tier"},  # Invalid placement tier
    ],
)
def test_package_check_constraints(db_session: Session, invalid_kwargs):
    """Verify database check constraints on EcoPromotionPackage."""
    valid_data = {
        "name": "Invalid Constraint Test Package",
        "duration_days": 7,
        "price": Decimal("2500.00"),
        "currency": "LKR",
        "placement_tier": "standard",
    }
    valid_data.update(invalid_kwargs)
    pkg = EcoPromotionPackage(**valid_data)
    db_session.add(pkg)

    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_package_unique_name(db_session: Session):
    """Verify package name uniqueness constraint."""
    pkg1 = EcoPromotionPackage(
        name="Unique Name Package",
        duration_days=7,
        price=Decimal("2500.00"),
        placement_tier="standard",
    )
    pkg2 = EcoPromotionPackage(
        name="Unique Name Package",
        duration_days=14,
        price=Decimal("4500.00"),
        placement_tier="featured",
    )
    db_session.add(pkg1)
    db_session.commit()

    db_session.add(pkg2)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_approved_package_seeding_idempotence(db_session: Session):
    """Verify the 3 approved business packages can be seeded idempotently without duplicates."""
    approved_packages = [
        ("Starter Spotlight", 7, Decimal("2500.00"), "standard"),
        ("Growth Boost", 14, Decimal("4500.00"), "featured"),
        ("Premier Showcase", 30, Decimal("8500.00"), "spotlight"),
    ]

    # Seed 1
    for name, days, price, tier in approved_packages:
        existing = db_session.query(EcoPromotionPackage).filter_by(name=name).first()
        if not existing:
            db_session.add(
                EcoPromotionPackage(
                    name=name,
                    duration_days=days,
                    price=price,
                    currency="LKR",
                    placement_tier=tier,
                    is_active=True,
                )
            )
    db_session.commit()

    assert db_session.query(EcoPromotionPackage).count() == 3

    # Seed 2 (Idempotent second run)
    for name, days, price, tier in approved_packages:
        existing = db_session.query(EcoPromotionPackage).filter_by(name=name).first()
        if not existing:
            db_session.add(EcoPromotionPackage(name=name, duration_days=days, price=price, placement_tier=tier))
    db_session.commit()

    assert db_session.query(EcoPromotionPackage).count() == 3

    starter = db_session.query(EcoPromotionPackage).filter_by(name="Starter Spotlight").first()
    assert starter.duration_days == 7
    assert starter.price == Decimal("2500.00")
    assert starter.placement_tier == "standard"


# ==============================================================================
# 2. Sponsored Promotion Campaign Model Tests
# ==============================================================================


def test_sponsored_promotion_defaults_and_relationships(
    db_session: Session, seed_provider_and_experience
):
    """Verify campaign creation, defaults (unpaid, pending_approval), and relationships."""
    exp = seed_provider_and_experience["experience"]
    profile = seed_provider_and_experience["profile"]

    pkg = EcoPromotionPackage(
        name="Starter Test",
        duration_days=7,
        price=Decimal("2500.00"),
        placement_tier="standard",
    )
    db_session.add(pkg)
    db_session.commit()

    campaign = EcoSponsoredPromotion(
        eco_experience_id=exp.id,
        provider_profile_id=profile.id,
        package_id=pkg.id,
        package_price_snapshot=pkg.price,
        currency_snapshot=pkg.currency,
        duration_days_snapshot=pkg.duration_days,
        payment_reference="DEP-SLIP-7712",
    )
    db_session.add(campaign)
    db_session.commit()

    reloaded = db_session.query(EcoSponsoredPromotion).filter_by(id=campaign.id).first()
    assert reloaded is not None
    assert reloaded.campaign_status == "pending_approval"
    assert reloaded.payment_status == "unpaid"
    assert reloaded.payment_reference == "DEP-SLIP-7712"
    assert reloaded.package_price_snapshot == Decimal("2500.00")
    assert reloaded.duration_days_snapshot == 7
    assert reloaded.starts_at is None
    assert reloaded.ends_at is None
    assert reloaded.requested_at is not None

    # Relationship verification
    assert reloaded.eco_experience.title == exp.title
    assert reloaded.provider_profile.business_name == profile.business_name
    assert reloaded.package.name == "Starter Test"
    assert reloaded.reviewer is None


@pytest.mark.parametrize(
    "invalid_data",
    [
        {"campaign_status": "fraudulent_status"},   # Invalid status
        {"payment_status": "crypto_transferred"},   # Invalid payment status
        {"package_price_snapshot": Decimal("-5.00")}, # Negative snapshot price
        {"duration_days_snapshot": 0},              # Zero duration snapshot
    ],
)
def test_sponsored_promotion_check_constraints(
    db_session: Session, seed_provider_and_experience, invalid_data
):
    """Verify database check constraints on EcoSponsoredPromotion."""
    exp = seed_provider_and_experience["experience"]
    profile = seed_provider_and_experience["profile"]

    pkg = EcoPromotionPackage(name="Pkg Test", duration_days=7, price=Decimal("2500.00"))
    db_session.add(pkg)
    db_session.commit()

    base_data = {
        "eco_experience_id": exp.id,
        "provider_profile_id": profile.id,
        "package_id": pkg.id,
        "package_price_snapshot": Decimal("2500.00"),
        "currency_snapshot": "LKR",
        "duration_days_snapshot": 7,
    }
    base_data.update(invalid_data)

    campaign = EcoSponsoredPromotion(**base_data)
    db_session.add(campaign)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_sponsored_promotion_inverted_dates_rejected(
    db_session: Session, seed_provider_and_experience
):
    """Verify that ends_at < starts_at is rejected by check constraint."""
    exp = seed_provider_and_experience["experience"]
    profile = seed_provider_and_experience["profile"]
    pkg = EcoPromotionPackage(name="Dates Pkg", duration_days=7, price=Decimal("2500.00"))
    db_session.add(pkg)
    db_session.commit()

    now = datetime.now(UTC)
    campaign = EcoSponsoredPromotion(
        eco_experience_id=exp.id,
        provider_profile_id=profile.id,
        package_id=pkg.id,
        package_price_snapshot=Decimal("2500.00"),
        duration_days_snapshot=7,
        starts_at=now,
        ends_at=now - timedelta(days=2),  # Inverted!
    )
    db_session.add(campaign)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_active_consistency_constraint(
    db_session: Session, seed_provider_and_experience
):
    """
    Enforces business rule at DB level: campaign_status='active' MUST NOT exist
    if payment_status is not 'paid' or if dates are missing.
    """
    exp = seed_provider_and_experience["experience"]
    profile = seed_provider_and_experience["profile"]
    pkg = EcoPromotionPackage(name="Consistency Pkg", duration_days=7, price=Decimal("2500.00"))
    db_session.add(pkg)
    db_session.commit()

    now = datetime.now(UTC)

    # Case 1: active but unpaid -> REJECTED
    c1 = EcoSponsoredPromotion(
        eco_experience_id=exp.id,
        provider_profile_id=profile.id,
        package_id=pkg.id,
        package_price_snapshot=Decimal("2500.00"),
        duration_days_snapshot=7,
        campaign_status="active",
        payment_status="unpaid",
        starts_at=now,
        ends_at=now + timedelta(days=7),
    )
    db_session.add(c1)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    # Case 2: active and paid but missing dates -> REJECTED
    c2 = EcoSponsoredPromotion(
        eco_experience_id=exp.id,
        provider_profile_id=profile.id,
        package_id=pkg.id,
        package_price_snapshot=Decimal("2500.00"),
        duration_days_snapshot=7,
        campaign_status="active",
        payment_status="paid",
        starts_at=None,
        ends_at=None,
    )
    db_session.add(c2)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    # Case 3: active, paid, and valid dates -> ACCEPTED
    c3 = EcoSponsoredPromotion(
        eco_experience_id=exp.id,
        provider_profile_id=profile.id,
        package_id=pkg.id,
        package_price_snapshot=Decimal("2500.00"),
        duration_days_snapshot=7,
        campaign_status="active",
        payment_status="paid",
        starts_at=now,
        ends_at=now + timedelta(days=7),
    )
    db_session.add(c3)
    db_session.commit()
    assert c3.id is not None


def test_sponsored_promotion_cascade_delete(
    db_session: Session, seed_provider_and_experience
):
    """Deleting EcoExperience cascades and cleans up associated campaigns."""
    exp = seed_provider_and_experience["experience"]
    profile = seed_provider_and_experience["profile"]
    pkg = EcoPromotionPackage(name="Cascade Pkg", duration_days=7, price=Decimal("2500.00"))
    db_session.add(pkg)
    db_session.commit()

    campaign = EcoSponsoredPromotion(
        eco_experience_id=exp.id,
        provider_profile_id=profile.id,
        package_id=pkg.id,
        package_price_snapshot=Decimal("2500.00"),
        duration_days_snapshot=7,
    )
    db_session.add(campaign)
    db_session.commit()
    campaign_id = campaign.id

    # Delete parent experience
    db_session.delete(exp)
    db_session.commit()

    assert db_session.query(EcoSponsoredPromotion).filter_by(id=campaign_id).first() is None


# ==============================================================================
# 3. Analytics Model Tests
# ==============================================================================


def test_analytics_creation_and_uniqueness(
    db_session: Session, seed_provider_and_experience
):
    """Verify analytics row creation and unique (promotion_id, metric_date) constraint."""
    exp = seed_provider_and_experience["experience"]
    profile = seed_provider_and_experience["profile"]
    pkg = EcoPromotionPackage(name="Analytics Pkg", duration_days=7, price=Decimal("2500.00"))
    db_session.add(pkg)
    db_session.commit()

    campaign = EcoSponsoredPromotion(
        eco_experience_id=exp.id,
        provider_profile_id=profile.id,
        package_id=pkg.id,
        package_price_snapshot=Decimal("2500.00"),
        duration_days_snapshot=7,
    )
    db_session.add(campaign)
    db_session.commit()

    today = date.today()
    a1 = EcoPromotionAnalytics(
        promotion_id=campaign.id,
        eco_experience_id=exp.id,
        metric_date=today,
        impressions_count=120,
        clicks_count=15,
    )
    db_session.add(a1)
    db_session.commit()

    reloaded = db_session.query(EcoPromotionAnalytics).filter_by(id=a1.id).first()
    assert reloaded.impressions_count == 120
    assert reloaded.clicks_count == 15
    assert reloaded.promotion.id == campaign.id
    assert reloaded.eco_experience.id == exp.id

    # Duplicate metric_date for same promotion -> IntegrityError
    a2 = EcoPromotionAnalytics(
        promotion_id=campaign.id,
        eco_experience_id=exp.id,
        metric_date=today,
        impressions_count=50,
        clicks_count=5,
    )
    db_session.add(a2)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_analytics_negative_counts_rejected(
    db_session: Session, seed_provider_and_experience
):
    """Negative impressions or clicks are rejected by check constraint."""
    exp = seed_provider_and_experience["experience"]
    profile = seed_provider_and_experience["profile"]
    pkg = EcoPromotionPackage(name="Counts Pkg", duration_days=7, price=Decimal("2500.00"))
    db_session.add(pkg)
    db_session.commit()

    campaign = EcoSponsoredPromotion(
        eco_experience_id=exp.id,
        provider_profile_id=profile.id,
        package_id=pkg.id,
        package_price_snapshot=Decimal("2500.00"),
        duration_days_snapshot=7,
    )
    db_session.add(campaign)
    db_session.commit()

    a_neg = EcoPromotionAnalytics(
        promotion_id=campaign.id,
        eco_experience_id=exp.id,
        metric_date=date.today(),
        impressions_count=-1,
        clicks_count=5,
    )
    db_session.add(a_neg)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_analytics_cascade_delete(
    db_session: Session, seed_provider_and_experience
):
    """Deleting a campaign cascades and deletes all its analytics rows."""
    exp = seed_provider_and_experience["experience"]
    profile = seed_provider_and_experience["profile"]
    pkg = EcoPromotionPackage(name="Del Pkg", duration_days=7, price=Decimal("2500.00"))
    db_session.add(pkg)
    db_session.commit()

    campaign = EcoSponsoredPromotion(
        eco_experience_id=exp.id,
        provider_profile_id=profile.id,
        package_id=pkg.id,
        package_price_snapshot=Decimal("2500.00"),
        duration_days_snapshot=7,
    )
    db_session.add(campaign)
    db_session.commit()

    a1 = EcoPromotionAnalytics(
        promotion_id=campaign.id,
        eco_experience_id=exp.id,
        metric_date=date.today(),
        impressions_count=10,
        clicks_count=2,
    )
    db_session.add(a1)
    db_session.commit()
    analytics_id = a1.id

    db_session.delete(campaign)
    db_session.commit()

    assert db_session.query(EcoPromotionAnalytics).filter_by(id=analytics_id).first() is None


# ==============================================================================
# 4. Helper Property Tests
# ==============================================================================


def test_is_currently_active_property():
    """Verify is_currently_active business logic helper."""
    now = datetime.now(UTC)

    # 1. Active & paid within window -> True
    c_active = EcoSponsoredPromotion(
        campaign_status="active",
        payment_status="paid",
        starts_at=now - timedelta(days=1),
        ends_at=now + timedelta(days=6),
    )
    assert c_active.is_currently_active is True

    # 2. Approved & paid within window -> True
    c_approved = EcoSponsoredPromotion(
        campaign_status="approved",
        payment_status="paid",
        starts_at=now - timedelta(days=1),
        ends_at=now + timedelta(days=6),
    )
    assert c_approved.is_currently_active is True

    # 3. Active but unpaid -> False
    c_unpaid = EcoSponsoredPromotion(
        campaign_status="active",
        payment_status="unpaid",
        starts_at=now - timedelta(days=1),
        ends_at=now + timedelta(days=6),
    )
    assert c_unpaid.is_currently_active is False

    # 4. Expired -> False
    c_expired = EcoSponsoredPromotion(
        campaign_status="active",
        payment_status="paid",
        starts_at=now - timedelta(days=10),
        ends_at=now - timedelta(days=3),
    )
    assert c_expired.is_currently_active is False

    # 5. Future start date -> False
    c_future = EcoSponsoredPromotion(
        campaign_status="active",
        payment_status="paid",
        starts_at=now + timedelta(days=2),
        ends_at=now + timedelta(days=9),
    )
    assert c_future.is_currently_active is False
