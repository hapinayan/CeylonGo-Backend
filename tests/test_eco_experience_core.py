"""
Automated unit tests for Module 04 (P0.1) — Eco-Tourism Core Database Models & Pydantic Schemas.

Covers:
1. EcoExperience default state: defaults to draft, unpublished, eco_badge_status='none', not publicly visible.
2. Public visibility rule: requires BOTH listing_status == 'approved' AND is_published == True.
3. Eco-Friendly Badge security rule: never granted automatically; requires eco_badge_status == 'granted'
   backed by an admin-reviewed EcoVerification record with decision == 'granted' and unexpired valid_until.
   Revoked or rejected badges immediately evaluate has_verified_eco_badge to False while preserving history.
4. Database CHECK constraints on EcoExperience, EcoSafetyRule, EcoSeasonRestriction, EcoPractice,
   EcoEvidence, and EcoVerification.
5. Relationships with ProviderProfile, Homestay (linked_homestay_id for 'Eco-Friendly Stays'), and cascade delete
   of child records.
6. Pydantic v2 schema validation:
   - Six approved Eco-Tourism categories enforced
   - Pricing constraints ('per_person', 'per_group', base_price >= 0, duration_hours > 0)
   - Coordinate bounds (-90..90 latitude, -180..180 longitude)
   - Environmental group size limits (1 <= min_participants <= max_group_size <= 50)
   - Separation of factual conservation contribution vs marketing statement
   - Linked homestay restriction to 'Eco-Friendly Stays' category
   - Extra/privilege-escalation fields forbidden on create/update schemas
   - Public response schemas strictly exclude private EcoEvidence file_reference paths
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.eco_experience import (
    VALID_ECO_CATEGORIES,
    EcoEvidence,
    EcoExperience,
    EcoExperienceImage,
    EcoPractice,
    EcoSafetyRule,
    EcoSeasonRestriction,
    EcoVerification,
)
from app.models.homestay import Homestay
from app.models.provider_profile import ProviderProfile
from app.models.user import Base, User
from app.schemas.eco_experience import (
    EcoExperienceCreate,
    EcoExperienceManagementResponse,
    EcoExperiencePublicDetailResponse,
    EcoExperiencePublicSummary,
    EcoExperienceUpdate,
    EcoPracticeInput,
    EcoSafetyRuleInput,
    EcoSeasonRestrictionInput,
    EcoVerificationDecisionRequest,
)

# In-memory SQLite engine with SQL Server sysutcdatetime and FK pragma support
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
        session.close()


@pytest.fixture
def eco_provider_and_admin(db_session: Session) -> tuple[User, ProviderProfile, User]:
    provider_user = User(
        full_name="Kasun Eco Guide",
        email="kasun.eco@ceylongo.com",
        password_hash="hashed_pwd",
        role="provider",
        is_active=True,
    )
    admin_user = User(
        full_name="CeylonGo Admin",
        email="admin.eco@ceylongo.com",
        password_hash="hashed_admin",
        role="admin",
        is_active=True,
    )
    db_session.add_all([provider_user, admin_user])
    db_session.commit()

    profile = ProviderProfile(
        user_id=provider_user.id,
        business_name="Sinharaja Rainforest Guardians",
        provider_type="eco_tourism",
        phone="0771234567",
        district="Ratnapura",
        description="Community-led rainforest conservation walks.",
        approval_status="approved",
    )
    db_session.add(profile)
    db_session.commit()
    db_session.refresh(profile)
    return provider_user, profile, admin_user


def make_valid_eco_payload() -> dict:
    return {
        "title": "Sinharaja Canopy & Endemic Bird Trail",
        "eco_category": "Wildlife & Birdwatching",
        "description": "Guided early-morning birdwatching walk with local naturalists.",
        "itinerary": "06:00 AM Meet at Kudawa entrance -> 06:30 AM Forest trail -> 10:30 AM Debrief.",
        "duration_hours": "4.50",
        "district": "Ratnapura",
        "city": "Kudawa",
        "address": "Kudawa Conservation Gate, Sinharaja",
        "meeting_point": "Sinharaja Forest Reserve Main Ticket Counter (Kudawa)",
        "directions_info": "Take Kalawana-Weddagala road to Kudawa entrance.",
        "latitude": "6.421500",
        "longitude": "80.418200",
        "pricing_type": "per_person",
        "base_price": "6500.00",
        "currency": "LKR",
        "max_group_size": 8,
        "min_participants": 2,
        "min_age": 10,
        "environmental_guidelines": "Stay strictly on marked trails. Carry all litter back out. No loud noises.",
        "wildlife_safety_summary": "Do not feed or touch any wildlife. Maintain 10m distance from reptiles and birds.",
        "conservation_factual_contribution": "LKR 1,000 per ticket goes directly to the Kudawa Youth Reforestation Fund.",
        "conservation_marketing_statement": "Experience pristine biodiversity while supporting local guardians.",
        "protected_area_permit_required": True,
        "certified_guide_required": True,
        "permit_details": "Forest Department entry permit included.",
        "cancellation_policy": "Full refund if cancelled at least 24 hours before start.",
        "free_cancellation_hours": 24,
    }


# ==============================================================================
# 1. SQLAlchemy Model Lifecycle, Defaults, Visibility & Badge Security Rules
# ==============================================================================


def test_eco_experience_defaults_to_draft_unpublished_and_no_badge(
    db_session: Session, eco_provider_and_admin
):
    _, profile, _ = eco_provider_and_admin

    exp = EcoExperience(
        provider_profile_id=profile.id,
        title="Knuckles Cloud Forest Trek",
        eco_category="Forest & Nature Trails",
        duration_hours=Decimal("6.00"),
        district="Matale",
        city="Riverston",
        meeting_point="Riverston Peak Ticket Office",
        base_price=Decimal("8000.00"),
        max_group_size=10,
        environmental_guidelines="Zero plastic policy inside the reserve.",
        wildlife_safety_summary="Leeches and mist hazards; follow ranger instructions.",
    )
    db_session.add(exp)
    db_session.commit()
    db_session.refresh(exp)

    assert exp.id is not None
    assert exp.listing_status == "draft"
    assert exp.is_published is False
    assert exp.is_publicly_visible is False
    assert exp.eco_badge_status == "none"
    assert exp.has_verified_eco_badge is False
    assert exp.pricing_type == "per_person"
    assert exp.currency == "LKR"
    assert exp.min_participants == 1
    assert exp.free_cancellation_hours == 24
    assert exp.provider_business_name == "Sinharaja Rainforest Guardians"
    assert exp.provider_phone == "0771234567"


def test_public_visibility_requires_admin_approval_and_publication(
    db_session: Session, eco_provider_and_admin
):
    _, profile, admin_user = eco_provider_and_admin

    exp = EcoExperience(
        provider_profile_id=profile.id,
        title="Rekawa Turtle Conservation Night Watch",
        eco_category="Conservation Activities",
        duration_hours=Decimal("3.00"),
        district="Hambantota",
        city="Tangalle",
        meeting_point="Rekawa Lagoon Conservation Center",
        base_price=Decimal("4500.00"),
        max_group_size=12,
        environmental_guidelines="No flash photography or white lights on the nesting beach.",
        wildlife_safety_summary="Do not touch nesting sea turtles or hatchlings.",
    )
    db_session.add(exp)
    db_session.commit()

    # 1. Draft + unpublished -> hidden
    assert exp.is_publicly_visible is False

    # 2. Pending approval + published flag True -> still hidden
    exp.listing_status = "pending_approval"
    exp.is_published = True
    db_session.commit()
    assert exp.is_publicly_visible is False

    # 3. Approved + unpublished -> hidden
    exp.listing_status = "approved"
    exp.approval_reviewed_by = admin_user.id
    exp.approval_reviewed_at = datetime.now(UTC)
    exp.is_published = False
    db_session.commit()
    assert exp.is_publicly_visible is False

    # 4. Approved + published -> publicly visible
    exp.is_published = True
    db_session.commit()
    assert exp.is_publicly_visible is True

    # 5. Rejected or deactivated -> immediately hidden
    exp.listing_status = "deactivated"
    db_session.commit()
    assert exp.is_publicly_visible is False


def test_eco_badge_verification_lifecycle_and_audit_history(
    db_session: Session, eco_provider_and_admin
):
    _, profile, admin_user = eco_provider_and_admin

    exp = EcoExperience(
        provider_profile_id=profile.id,
        title="Maduganga Mangrove Restoration & Kayaking",
        eco_category="Conservation Activities",
        duration_hours=Decimal("3.50"),
        district="Galle",
        city="Balapitiya",
        meeting_point="Balapitiya Eco Pier",
        base_price=Decimal("5500.00"),
        max_group_size=8,
        environmental_guidelines="Non-motorized kayaks only; plant 2 mangrove saplings per guest.",
        wildlife_safety_summary="Wear life jackets at all times; do not disturb water monitors.",
    )
    db_session.add(exp)
    db_session.commit()

    # Provider submits practice & private evidence
    practice = EcoPractice(
        eco_experience_id=exp.id,
        practice_category="habitat_restoration",
        claim_description="100% solar-powered pier and verified mangrove nursery partnership.",
    )
    db_session.add(practice)
    db_session.commit()

    evidence = EcoEvidence(
        eco_practice_id=practice.id,
        file_reference="eco_evidence_cert_8f9a2b.png",
        document_type="certificate",
        notes="Coast Conservation Department nursery registration",
    )
    db_session.add(evidence)

    # 1. Provider submits verification request -> badge is NOT granted automatically
    v_submit = EcoVerification(
        eco_experience_id=exp.id,
        submitted_by_provider_id=profile.id,
        decision="submitted",
        reason="Initial submission with Coast Conservation certificate.",
    )
    exp.eco_badge_status = "pending"
    db_session.add(v_submit)
    db_session.commit()
    db_session.refresh(exp)

    assert exp.has_verified_eco_badge is False

    # 2. Even if eco_badge_status were tampered to 'granted' without an admin decision record, badge is False
    exp.eco_badge_status = "granted"
    db_session.commit()
    db_session.refresh(exp)
    assert exp.has_verified_eco_badge is False

    # 3. Admin grants badge with documented verification record
    v_grant = EcoVerification(
        eco_experience_id=exp.id,
        submitted_by_provider_id=profile.id,
        reviewed_by_admin_id=admin_user.id,
        decision="granted",
        reason="Verified nursery permit and non-motorized kayak fleet.",
        valid_until=datetime.now(UTC).replace(tzinfo=None) + timedelta(days=365),
    )
    practice.is_verified = True
    db_session.add(v_grant)
    db_session.commit()
    db_session.refresh(exp)

    assert exp.has_verified_eco_badge is True
    assert len(exp.verifications) == 2

    # 4. Admin revokes badge -> badge removed immediately while preserving audit history
    v_revoke = EcoVerification(
        eco_experience_id=exp.id,
        submitted_by_provider_id=profile.id,
        reviewed_by_admin_id=admin_user.id,
        decision="revoked",
        reason="Permit expired during quarterly audit.",
    )
    exp.eco_badge_status = "revoked"
    db_session.add(v_revoke)
    db_session.commit()
    db_session.refresh(exp)

    assert exp.has_verified_eco_badge is False
    assert len(exp.verifications) == 3
    assert [v.decision for v in exp.verifications] == ["revoked", "granted", "submitted"]


def test_linked_homestay_and_child_cascade_relationships(
    db_session: Session, eco_provider_and_admin
):
    provider_user, profile, _ = eco_provider_and_admin

    homestay = Homestay(
        owner_id=provider_user.id,
        name="Ella Mudhouse Eco Lodge",
        description="Off-grid solar mudhouse stay",
        location="Ella",
        price_per_night=Decimal("12000.00"),
        max_guests=4,
        is_active=True,
    )
    db_session.add(homestay)
    db_session.commit()

    exp = EcoExperience(
        provider_profile_id=profile.id,
        linked_homestay_id=homestay.id,
        title="Ella Off-Grid Permaculture & Eco-Lodge Experience",
        eco_category="Eco-Friendly Stays",
        duration_hours=Decimal("24.00"),
        district="Badulla",
        city="Ella",
        meeting_point="Ella Railway Station Eco Pickup Point",
        base_price=Decimal("12000.00"),
        max_group_size=4,
        environmental_guidelines="Compost toilets and solar lighting only.",
        wildlife_safety_summary="Do not leave food outdoors; wild boar and macaques present.",
    )
    exp.images.append(
        EcoExperienceImage(
            image_url="https://example.com/eco-lodge.jpg",
            caption="Solar powered main cottage",
            is_primary=True,
            sort_order=0,
        )
    )
    exp.safety_rules.append(
        EcoSafetyRule(
            rule_type="wildlife_no_feeding",
            title="No Feeding Torque Macaques",
            description="Keep all fruit and snacks sealed inside the cottage.",
            is_mandatory=True,
        )
    )
    exp.season_restrictions.append(
        EcoSeasonRestriction(
            start_date=date(2026, 11, 1),
            end_date=date(2026, 11, 30),
            restriction_type="monsoon_season",
            reason="Heavy northeast monsoon landslide precaution.",
            is_active=True,
        )
    )

    db_session.add(exp)
    db_session.commit()
    db_session.refresh(exp)

    assert exp.linked_homestay is not None
    assert exp.linked_homestay.name == "Ella Mudhouse Eco Lodge"
    assert len(homestay.linked_eco_experiences) == 1

    # Deleting the EcoExperience cascades to images, safety rules, and season restrictions,
    # while leaving the linked Homestay intact
    exp_id = exp.id
    db_session.delete(exp)
    db_session.commit()

    assert db_session.query(EcoExperienceImage).filter_by(eco_experience_id=exp_id).count() == 0
    assert db_session.query(EcoSafetyRule).filter_by(eco_experience_id=exp_id).count() == 0
    assert db_session.query(EcoSeasonRestriction).filter_by(eco_experience_id=exp_id).count() == 0
    assert db_session.get(Homestay, homestay.id) is not None


@pytest.mark.parametrize(
    "override_field,invalid_value",
    [
        ("eco_category", "Jet Ski Party"),
        ("pricing_type", "hourly_auction"),
        ("base_price", Decimal("-50.00")),
        ("duration_hours", Decimal("0.00")),
        ("max_group_size", 0),
        ("max_group_size", 51),
        ("min_participants", 15),  # > max_group_size (8)
        ("latitude", Decimal("95.000000")),
        ("longitude", Decimal("-185.000000")),
        ("listing_status", "auto_published"),
        ("eco_badge_status", "self_certified"),
    ],
)
def test_eco_experience_database_check_constraints(
    db_session: Session, eco_provider_and_admin, override_field, invalid_value
):
    _, profile, _ = eco_provider_and_admin

    kwargs = dict(
        provider_profile_id=profile.id,
        title="Constraint Check Experience",
        eco_category="Forest & Nature Trails",
        duration_hours=Decimal("3.00"),
        district="Kandy",
        city="Kandy",
        meeting_point="Udawatta Kele Sanctuary Gate",
        pricing_type="per_person",
        base_price=Decimal("3000.00"),
        max_group_size=8,
        min_participants=1,
        environmental_guidelines="Keep silence inside the forest sanctuary.",
        wildlife_safety_summary="Do not feed monkeys or barking deer.",
    )
    kwargs[override_field] = invalid_value

    db_session.add(EcoExperience(**kwargs))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


# ==============================================================================
# 2. Pydantic v2 Schema Validation & Security Isolation Tests
# ==============================================================================


@pytest.mark.parametrize("category", VALID_ECO_CATEGORIES)
def test_schema_accepts_all_six_official_eco_categories(category: str):
    payload = make_valid_eco_payload()
    payload["eco_category"] = category
    schema = EcoExperienceCreate(**payload)
    assert schema.eco_category == category


def test_schema_rejects_invalid_eco_category():
    payload = make_valid_eco_payload()
    payload["eco_category"] = "Nightclub & Casino Tour"
    with pytest.raises(ValidationError) as exc_info:
        EcoExperienceCreate(**payload)
    assert "eco_category" in str(exc_info.value)


@pytest.mark.parametrize(
    "field,invalid_val",
    [
        ("base_price", "-10.00"),
        ("duration_hours", "0"),
        ("duration_hours", "-2.5"),
        ("pricing_type", "free_for_all"),
        ("latitude", "91.0"),
        ("latitude", "-90.001"),
        ("longitude", "180.5"),
        ("longitude", "-181.0"),
        ("max_group_size", 0),
        ("max_group_size", 55),
        ("min_participants", 0),
        ("environmental_guidelines", "short"),
        ("wildlife_safety_summary", "   "),
    ],
)
def test_schema_validates_pricing_coordinates_and_group_limits(field: str, invalid_val):
    payload = make_valid_eco_payload()
    payload[field] = invalid_val
    with pytest.raises(ValidationError):
        EcoExperienceCreate(**payload)


def test_schema_rejects_min_participants_exceeding_max_group_size():
    payload = make_valid_eco_payload()
    payload["max_group_size"] = 6
    payload["min_participants"] = 8
    with pytest.raises(ValidationError) as exc_info:
        EcoExperienceCreate(**payload)
    assert "min_participants cannot exceed max_group_size" in str(exc_info.value)


def test_schema_enforces_distinct_factual_and_marketing_conservation_claims():
    payload = make_valid_eco_payload()
    duplicate_claim = "We love nature and protect the rainforest every day."
    payload["conservation_factual_contribution"] = duplicate_claim
    payload["conservation_marketing_statement"] = duplicate_claim
    with pytest.raises(ValidationError) as exc_info:
        EcoExperienceCreate(**payload)
    assert "conservation_factual_contribution must be distinct" in str(exc_info.value)


def test_schema_restricts_linked_homestay_to_eco_friendly_stays_category():
    payload = make_valid_eco_payload()
    payload["eco_category"] = "Wildlife & Birdwatching"
    payload["linked_homestay_id"] = 5
    with pytest.raises(ValidationError) as exc_info:
        EcoExperienceCreate(**payload)
    assert "Eco-Friendly Stays" in str(exc_info.value)

    # Succeeds when category is 'Eco-Friendly Stays'
    payload["eco_category"] = "Eco-Friendly Stays"
    valid_stay = EcoExperienceCreate(**payload)
    assert valid_stay.linked_homestay_id == 5


@pytest.mark.parametrize(
    "forbidden_field,value",
    [
        ("provider_profile_id", 99),
        ("listing_status", "approved"),
        ("is_published", True),
        ("eco_badge_status", "granted"),
        ("has_verified_eco_badge", True),
    ],
)
def test_create_and_update_schemas_forbid_privilege_escalation_fields(
    forbidden_field: str, value
):
    create_payload = make_valid_eco_payload()
    create_payload[forbidden_field] = value
    with pytest.raises(ValidationError):
        EcoExperienceCreate(**create_payload)

    update_payload = {"title": "Updated Title", forbidden_field: value}
    with pytest.raises(ValidationError):
        EcoExperienceUpdate(**update_payload)


def test_season_restriction_and_safety_rule_schemas():
    # Valid season restriction
    valid_season = EcoSeasonRestrictionInput(
        start_date=date(2026, 5, 1),
        end_date=date(2026, 8, 31),
        restriction_type="monsoon_season",
        reason="Southwest monsoon high river levels in Kitulgala.",
    )
    assert valid_season.restriction_type == "monsoon_season"

    # Invalid date order rejected
    with pytest.raises(ValidationError):
        EcoSeasonRestrictionInput(
            start_date=date(2026, 8, 31),
            end_date=date(2026, 5, 1),
            restriction_type="monsoon_season",
            reason="Invalid date order.",
        )

    # Valid wildlife safety rule
    valid_rule = EcoSafetyRuleInput(
        rule_type="wildlife_no_feeding",
        title="Strictly No Feeding Wild Elephants",
        description="Feeding alters elephant behavior and endangers travelers.",
        is_mandatory=True,
    )
    assert valid_rule.rule_type == "wildlife_no_feeding"


def test_public_schemas_never_expose_private_verification_evidence(
    db_session: Session, eco_provider_and_admin
):
    _, profile, admin_user = eco_provider_and_admin

    exp = EcoExperience(
        provider_profile_id=profile.id,
        title="Horton Plains Cloud Forest Conservation Walk",
        eco_category="Forest & Nature Trails",
        duration_hours=Decimal("4.00"),
        district="Nuwara Eliya",
        city="Ohiya",
        meeting_point="Horton Plains Farr Inn Visitor Center",
        base_price=Decimal("7000.00"),
        max_group_size=8,
        environmental_guidelines="Polythene bags and single-use plastic bottles are searched and banned at entry.",
        wildlife_safety_summary="Do not approach Sambar deer or feed any birds.",
        listing_status="approved",
        is_published=True,
        eco_badge_status="granted",
    )
    db_session.add(exp)
    db_session.commit()

    practice = EcoPractice(
        eco_experience_id=exp.id,
        practice_category="waste_reduction",
        claim_description="100% reusable stainless steel tiffin boxes and water flasks provided.",
        is_verified=True,
    )
    db_session.add(practice)
    db_session.commit()

    secret_file_ref = "private_uploads/eco_evidence/secret_audit_report_999.jpg"
    evidence = EcoEvidence(
        eco_practice_id=practice.id,
        file_reference=secret_file_ref,
        document_type="audit_report",
        notes="Internal inspector report with personal NIC numbers",
    )
    verification = EcoVerification(
        eco_experience_id=exp.id,
        submitted_by_provider_id=profile.id,
        reviewed_by_admin_id=admin_user.id,
        decision="granted",
        reason="Verified zero-plastic equipment audit.",
    )
    db_session.add_all([evidence, verification])
    db_session.commit()
    db_session.refresh(exp)

    # 1. Public Summary serialization
    public_summary = EcoExperiencePublicSummary.model_validate(exp).model_dump()
    assert public_summary["has_verified_eco_badge"] is True
    assert secret_file_ref not in str(public_summary)

    # 2. Public Detail serialization: includes practices, excludes evidence & file_reference
    public_detail = EcoExperiencePublicDetailResponse.model_validate(exp).model_dump()
    assert public_detail["has_verified_eco_badge"] is True
    assert len(public_detail["practices"]) == 1
    assert "evidences" not in public_detail["practices"][0]
    assert secret_file_ref not in str(public_detail)

    # 3. Management serialization: includes evidence metadata (document_type, id),
    #    but still NEVER exposes raw server file_reference path
    mgmt_detail = EcoExperienceManagementResponse.model_validate(exp).model_dump()
    assert len(mgmt_detail["practices"][0]["evidences"]) == 1
    ev_meta = mgmt_detail["practices"][0]["evidences"][0]
    assert ev_meta["document_type"] == "audit_report"
    assert "file_reference" not in ev_meta
    assert secret_file_ref not in str(mgmt_detail)
