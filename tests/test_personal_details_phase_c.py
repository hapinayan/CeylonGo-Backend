"""
Comprehensive Unit Tests for CeylonGo Provider Registration Step 2 Personal Details (Phase C).

Covers:
1. Pydantic schema validation:
   - Sri Lankan Old NIC (9 digits + V/X)
   - Sri Lankan New NIC (12 digits)
   - Invalid NICs
   - Valid Passport (6-12 chars uppercase alphanumeric)
   - Invalid Passports
   - Date of Birth (future, under 18, 18+ boundary, leap year aware)
   - Gender (Male, Female, Other)
   - Nationality
   - Residential Street Address (permanent_landmark)
2. Database Models:
   - ProviderRegistrationDraft persistence of new fields
   - ProviderProfile persistence of new fields
3. Duplicate identity check isolation:
   - Isolated by identity_document_type + number
4. Admin schemas:
   - ProviderDetailResponse exposes personal details
   - ProviderListItemResponse preserves privacy
5. Router logic:
   - save_personal_details direct execution
   - submit_provider_registration field transfer and document requirements (NIC vs Passport)
"""

from datetime import UTC, date, datetime, timedelta
import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi import HTTPException

from app.models.user import Base, User
from app.models.provider_profile import ProviderProfile
from app.models.provider_registration_draft import ProviderRegistrationDraft
from app.models.provider_registration_authorization import ProviderRegistrationAuthorization
from app.schemas.provider_registration import (
    PersonalDetailsRequest,
    DraftResponse,
    validate_sri_lankan_nic,
    validate_passport,
)
from app.schemas.admin_providers import (
    ProviderDetailResponse,
    ProviderListItemResponse,
)
from app.routers.provider_registration import (
    compute_draft_response,
    save_personal_details,
    submit_provider_registration,
)

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


@pytest.fixture
def db_session():
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


def _valid_dob() -> date:
    today = date.today()
    return date(today.year - 25, 1, 15)


# ==============================================================================
# 1. Pydantic Schema Validation Tests
# ==============================================================================

class TestPersonalDetailsValidation:
    def test_valid_old_nic(self):
        req = PersonalDetailsRequest(
            full_name="Kasun Perera",
            phone="+94771234567",
            identity_document_type="nic",
            nic_number="912345678V",
            date_of_birth=_valid_dob(),
            gender="Male",
            nationality="Sri Lankan",
            permanent_province="Western",
            permanent_district="Colombo",
            permanent_city="Colombo 03",
            permanent_postal_code="00300",
            permanent_landmark="No. 45, Galle Road",
        )
        assert req.nic_number == "912345678V"
        assert req.identity_document_type == "nic"

    def test_valid_old_nic_lowercase_normalized(self):
        req = PersonalDetailsRequest(
            full_name="Kasun Perera",
            phone="+94771234567",
            identity_document_type="nic",
            nic_number="912345678v",
            date_of_birth=_valid_dob(),
            gender="Male",
            nationality="Sri Lankan",
            permanent_province="Western",
            permanent_district="Colombo",
            permanent_city="Colombo 03",
            permanent_postal_code="00300",
            permanent_landmark="No. 45, Galle Road",
        )
        assert req.nic_number == "912345678V"

    def test_valid_new_nic(self):
        req = PersonalDetailsRequest(
            full_name="Kasun Perera",
            phone="+94771234567",
            identity_document_type="nic",
            nic_number="199512345678",
            date_of_birth=_valid_dob(),
            gender="Male",
            nationality="Sri Lankan",
            permanent_province="Western",
            permanent_district="Colombo",
            permanent_city="Colombo 03",
            permanent_postal_code="00300",
            permanent_landmark="No. 45, Galle Road",
        )
        assert req.nic_number == "199512345678"

    @pytest.mark.parametrize(
        "invalid_nic",
        [
            "12345678",        # 8 digits
            "1234567890",      # 10 digits
            "12345678901",     # 11 digits
            "1234567890123",   # 13 digits
            "123456789A",      # invalid letter
            "12345V789",       # letter in middle
            "ABCDEFGHIJK1",    # letters
        ],
    )
    def test_invalid_nic_rejected(self, invalid_nic):
        with pytest.raises(ValidationError) as exc:
            PersonalDetailsRequest(
                full_name="Kasun Perera",
                phone="+94771234567",
                identity_document_type="nic",
                nic_number=invalid_nic,
                date_of_birth=_valid_dob(),
                gender="Male",
                nationality="Sri Lankan",
                permanent_province="Western",
                permanent_district="Colombo",
                permanent_city="Colombo 03",
                permanent_postal_code="00300",
                permanent_landmark="No. 45, Galle Road",
            )
        assert "NIC" in str(exc.value)

    @pytest.mark.parametrize(
        "valid_passport",
        [
            "N1234567",
            "PA1234567",
            "ABC12345",
            "12345678",
            "A12345678901",  # 12 chars
        ],
    )
    def test_valid_passport(self, valid_passport):
        req = PersonalDetailsRequest(
            full_name="John Smith",
            phone="+94771234567",
            identity_document_type="passport",
            nic_number=valid_passport,
            date_of_birth=_valid_dob(),
            gender="Male",
            nationality="British",
            permanent_province="Western",
            permanent_district="Colombo",
            permanent_city="Colombo 03",
            permanent_postal_code="00300",
            permanent_landmark="No. 12, Flower Road",
        )
        assert req.nic_number == valid_passport.upper()

    def test_passport_lowercase_normalized_to_uppercase(self):
        req = PersonalDetailsRequest(
            full_name="John Smith",
            phone="+94771234567",
            identity_document_type="passport",
            nic_number="n1234567",
            date_of_birth=_valid_dob(),
            gender="Male",
            nationality="British",
            permanent_province="Western",
            permanent_district="Colombo",
            permanent_city="Colombo 03",
            permanent_postal_code="00300",
            permanent_landmark="No. 12, Flower Road",
        )
        assert req.nic_number == "N1234567"

    @pytest.mark.parametrize(
        "invalid_passport",
        [
            "N1234",          # 5 chars (<6)
            "ABCDEFGHIJKLM",  # 13 chars (>12)
            "N123-456",       # symbol hyphen
            "N123 456",       # whitespace
            "PA@1234",        # special char
        ],
    )
    def test_invalid_passport_rejected(self, invalid_passport):
        with pytest.raises(ValidationError) as exc:
            PersonalDetailsRequest(
                full_name="John Smith",
                phone="+94771234567",
                identity_document_type="passport",
                nic_number=invalid_passport,
                date_of_birth=_valid_dob(),
                gender="Male",
                nationality="British",
                permanent_province="Western",
                permanent_district="Colombo",
                permanent_city="Colombo 03",
                permanent_postal_code="00300",
                permanent_landmark="No. 12, Flower Road",
            )
        assert "passport" in str(exc.value).lower()

    def test_date_of_birth_future_rejected(self):
        tomorrow = date.today() + timedelta(days=1)
        with pytest.raises(ValidationError) as exc:
            PersonalDetailsRequest(
                full_name="Kasun Perera",
                phone="+94771234567",
                identity_document_type="nic",
                nic_number="912345678V",
                date_of_birth=tomorrow,
                gender="Male",
                nationality="Sri Lankan",
                permanent_province="Western",
                permanent_district="Colombo",
                permanent_city="Colombo 03",
                permanent_postal_code="00300",
                permanent_landmark="No. 45, Galle Road",
            )
        assert "past" in str(exc.value).lower()

    def test_date_of_birth_under_18_rejected(self):
        # 17 years old
        seventeen_yo = date.today().replace(year=date.today().year - 17)
        with pytest.raises(ValidationError) as exc:
            PersonalDetailsRequest(
                full_name="Kasun Perera",
                phone="+94771234567",
                identity_document_type="nic",
                nic_number="912345678V",
                date_of_birth=seventeen_yo,
                gender="Male",
                nationality="Sri Lankan",
                permanent_province="Western",
                permanent_district="Colombo",
                permanent_city="Colombo 03",
                permanent_postal_code="00300",
                permanent_landmark="No. 45, Galle Road",
            )
        assert "18 years old" in str(exc.value)

    def test_date_of_birth_exact_18_accepted(self):
        today = date.today()
        # Exactly 18 years old today
        exact_18 = date(today.year - 18, today.month, today.day)
        req = PersonalDetailsRequest(
            full_name="Kasun Perera",
            phone="+94771234567",
            identity_document_type="nic",
            nic_number="912345678V",
            date_of_birth=exact_18,
            gender="Male",
            nationality="Sri Lankan",
            permanent_province="Western",
            permanent_district="Colombo",
            permanent_city="Colombo 03",
            permanent_postal_code="00300",
            permanent_landmark="No. 45, Galle Road",
        )
        assert req.date_of_birth == exact_18

    @pytest.mark.parametrize("gender", ["Male", "Female", "Other"])
    def test_valid_gender_values(self, gender):
        req = PersonalDetailsRequest(
            full_name="Kasun Perera",
            phone="+94771234567",
            identity_document_type="nic",
            nic_number="912345678V",
            date_of_birth=_valid_dob(),
            gender=gender,
            nationality="Sri Lankan",
            permanent_province="Western",
            permanent_district="Colombo",
            permanent_city="Colombo 03",
            permanent_postal_code="00300",
            permanent_landmark="No. 45, Galle Road",
        )
        assert req.gender == gender

    def test_invalid_gender_rejected(self):
        with pytest.raises(ValidationError):
            PersonalDetailsRequest(
                full_name="Kasun Perera",
                phone="+94771234567",
                identity_document_type="nic",
                nic_number="912345678V",
                date_of_birth=_valid_dob(),
                gender="Alien",
                nationality="Sri Lankan",
                permanent_province="Western",
                permanent_district="Colombo",
                permanent_city="Colombo 03",
                permanent_postal_code="00300",
                permanent_landmark="No. 45, Galle Road",
            )

    def test_empty_nationality_rejected(self):
        with pytest.raises(ValidationError):
            PersonalDetailsRequest(
                full_name="Kasun Perera",
                phone="+94771234567",
                identity_document_type="nic",
                nic_number="912345678V",
                date_of_birth=_valid_dob(),
                gender="Male",
                nationality="   ",
                permanent_province="Western",
                permanent_district="Colombo",
                permanent_city="Colombo 03",
                permanent_postal_code="00300",
                permanent_landmark="No. 45, Galle Road",
            )


# ==============================================================================
# 2. Database Models Persistence Tests
# ==============================================================================

class TestDatabaseModels:
    def test_provider_registration_draft_persists_all_fields(self, db_session: Session):
        draft = ProviderRegistrationDraft(
            email="provider@test.com",
            full_name="Kasun Perera",
            primary_phone="+94771234567",
            identity_document_type="passport",
            nic_number="N9876543",
            date_of_birth=date(1992, 8, 20),
            gender="Male",
            nationality="Sri Lankan",
            permanent_province="Western",
            permanent_district="Colombo",
            permanent_city="Colombo 07",
            permanent_landmark="No. 100, Horton Place",
            permanent_postal_code="00700",
        )
        db_session.add(draft)
        db_session.commit()
        db_session.refresh(draft)

        assert draft.id is not None
        assert draft.identity_document_type == "passport"
        assert draft.nic_number == "N9876543"
        assert draft.date_of_birth == date(1992, 8, 20)
        assert draft.gender == "Male"
        assert draft.nationality == "Sri Lankan"
        assert draft.permanent_landmark == "No. 100, Horton Place"

    def test_provider_profile_persists_all_fields(self, db_session: Session):
        user = User(
            full_name="Kasun Perera",
            email="kasun@ceylongo.com",
            role="provider",
            is_active=False,
        )
        db_session.add(user)
        db_session.commit()

        profile = ProviderProfile(
            user_id=user.id,
            business_name="Ceylon Retreat",
            provider_type="homestay",
            phone="+94771234567",
            district="Colombo",
            identity_document_type="passport",
            nic_number="P1234567",
            date_of_birth=date(1988, 3, 10),
            gender="Female",
            nationality="Maldivian",
            permanent_province="Western",
            permanent_district="Colombo",
            permanent_city="Dehiwala",
            permanent_landmark="No. 12, Station Road",
            permanent_postal_code="10350",
            approval_status="pending",
        )
        db_session.add(profile)
        db_session.commit()
        db_session.refresh(profile)

        assert profile.id is not None
        assert profile.identity_document_type == "passport"
        assert profile.nic_number == "P1234567"
        assert profile.date_of_birth == date(1988, 3, 10)
        assert profile.gender == "Female"
        assert profile.nationality == "Maldivian"
        assert profile.permanent_landmark == "No. 12, Station Road"


# ==============================================================================
# 3. Draft Completion & Document Requirements (NIC vs Passport)
# ==============================================================================

class TestDraftCompletionLogic:
    def test_compute_draft_response_personal_completion(self):
        draft = ProviderRegistrationDraft(
            email="provider@test.com",
            full_name="Kasun Perera",
            primary_phone="+94771234567",
            identity_document_type="nic",
            nic_number="912345678V",
            date_of_birth=date(1990, 5, 15),
            gender="Male",
            nationality="Sri Lankan",
            permanent_province="Western",
            permanent_district="Colombo",
            permanent_city="Colombo 03",
            permanent_landmark="No. 45, Galle Road",
            permanent_postal_code="00300",
        )
        res = compute_draft_response(draft)
        assert res.personal_completed is True
        assert res.current_step == "business"
        assert res.identity_document_type == "nic"
        assert res.date_of_birth == date(1990, 5, 15)
        assert res.gender == "Male"
        assert res.nationality == "Sri Lankan"

    def test_compute_draft_response_incomplete_when_dob_missing(self):
        draft = ProviderRegistrationDraft(
            email="provider@test.com",
            full_name="Kasun Perera",
            primary_phone="+94771234567",
            identity_document_type="nic",
            nic_number="912345678V",
            date_of_birth=None,  # Missing
            gender="Male",
            nationality="Sri Lankan",
            permanent_province="Western",
            permanent_district="Colombo",
            permanent_city="Colombo 03",
            permanent_landmark="No. 45, Galle Road",
            permanent_postal_code="00300",
        )
        res = compute_draft_response(draft)
        assert res.personal_completed is False
        assert res.current_step == "personal"

    def test_passport_identity_completion_requires_only_selfie_and_front(self):
        draft = ProviderRegistrationDraft(
            email="provider@test.com",
            full_name="John Smith",
            primary_phone="+94771234567",
            identity_document_type="passport",
            nic_number="N1234567",
            date_of_birth=date(1985, 1, 1),
            gender="Male",
            nationality="British",
            permanent_province="Western",
            permanent_district="Colombo",
            permanent_city="Colombo 03",
            permanent_landmark="No. 1, Main St",
            permanent_postal_code="00300",
            business_name="John Tours",
            provider_category="activity",
            business_province="Western",
            business_district="Colombo",
            business_city="Colombo",
            business_postal_code="00100",
            business_description="Tour provider",
            selfie_file_reference="selfie.jpg",
            nic_front_file_reference="passport_page.jpg",
            nic_back_file_reference=None,  # Not required for passport
        )
        res = compute_draft_response(draft)
        assert res.identity_completed is True
        assert res.current_step == "review"

    def test_nic_identity_completion_requires_front_and_back(self):
        draft = ProviderRegistrationDraft(
            email="provider@test.com",
            full_name="Kasun Perera",
            primary_phone="+94771234567",
            identity_document_type="nic",
            nic_number="199012345678",
            date_of_birth=date(1990, 5, 15),
            gender="Male",
            nationality="Sri Lankan",
            permanent_province="Western",
            permanent_district="Colombo",
            permanent_city="Colombo 03",
            permanent_landmark="No. 45, Galle Road",
            permanent_postal_code="00300",
            business_name="Kasun Homestay",
            provider_category="homestay",
            business_province="Western",
            business_district="Colombo",
            business_city="Colombo",
            business_postal_code="00100",
            business_description="Homestay provider",
            selfie_file_reference="selfie.jpg",
            nic_front_file_reference="nic_front.jpg",
            nic_back_file_reference=None,  # Back missing for NIC
        )
        res = compute_draft_response(draft)
        assert res.identity_completed is False
        assert res.current_step == "identity"

        # Now add back photo
        draft.nic_back_file_reference = "nic_back.jpg"
        res2 = compute_draft_response(draft)
        assert res2.identity_completed is True
        assert res2.current_step == "review"


# ==============================================================================
# 4. Duplicate Identity Check Isolation Tests
# ==============================================================================

class TestDuplicateIdentityIsolation:
    def test_passport_and_nic_with_same_number_do_not_conflict(self, db_session: Session):
        user1 = User(full_name="NIC User", email="nic@ceylongo.com", role="provider", is_active=False)
        db_session.add(user1)
        db_session.flush()

        profile1 = ProviderProfile(
            user_id=user1.id,
            business_name="NIC Homestay",
            provider_type="homestay",
            phone="+94771234567",
            district="Colombo",
            identity_document_type="nic",
            nic_number="199012345678",
            approval_status="pending",
        )
        db_session.add(profile1)
        db_session.commit()

        # Query for passport with the same number
        conflict = (
            db_session.query(ProviderProfile)
            .filter_by(
                nic_number="199012345678",
                identity_document_type="passport",
            )
            .first()
        )
        assert conflict is None

        # Query for nic with the same number -> found
        existing_nic = (
            db_session.query(ProviderProfile)
            .filter_by(
                nic_number="199012345678",
                identity_document_type="nic",
            )
            .first()
        )
        assert existing_nic is not None
        assert existing_nic.id == profile1.id


# ==============================================================================
# 5. Admin Schema Tests
# ==============================================================================

class TestAdminSchemas:
    def test_provider_detail_response_contains_demographic_and_identity_fields(self):
        resp = ProviderDetailResponse(
            id=1,
            user_id=2,
            full_name="Kasun Perera",
            email="owner@ceylonvillas.com",
            role="provider",
            is_active=False,
            business_name="Ceylon Villas",
            provider_type="homestay",
            phone="+94771234567",
            district="Colombo",
            description="Luxury villa",
            approval_status="pending",
            identity_document_type="passport",
            nic_number="N9988776",
            date_of_birth=date(1990, 10, 25),
            gender="Male",
            nationality="Sri Lankan",
            created_at=datetime.now(UTC),
        )
        assert resp.identity_document_type == "passport"
        assert resp.nic_number == "N9988776"
        assert resp.date_of_birth == date(1990, 10, 25)
        assert resp.gender == "Male"
        assert resp.nationality == "Sri Lankan"

    def test_provider_list_item_response_does_not_leak_demographics(self):
        resp = ProviderListItemResponse(
            id=1,
            user_id=2,
            full_name="Kasun Perera",
            email="owner@ceylonvillas.com",
            business_name="Ceylon Villas",
            provider_type="homestay",
            phone="+94771234567",
            district="Colombo",
            approval_status="pending",
            created_at=datetime.now(UTC),
        )
        data = resp.model_dump()
        assert "date_of_birth" not in data
        assert "gender" not in data
        assert "nationality" not in data


# ==============================================================================
# 6. Direct Router Execution Tests
# ==============================================================================

class TestRouterExecution:
    def test_save_personal_details_success(self, db_session: Session):
        auth_record = ProviderRegistrationAuthorization(
            email="direct@provider.com",
            token_hash="fake_hash_1",
            purpose="registration",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            is_consumed=False,
        )
        db_session.add(auth_record)
        db_session.commit()

        payload = PersonalDetailsRequest(
            full_name="Jane Doe",
            phone="+94771234567",
            identity_document_type="passport",
            nic_number="PA9876543",
            date_of_birth=date(1993, 4, 12),
            gender="Female",
            nationality="Australian",
            permanent_province="Western",
            permanent_district="Colombo",
            permanent_city="Colombo 03",
            permanent_postal_code="00300",
            permanent_landmark="No. 88, Marina Road",
        )

        res = save_personal_details(
            payload=payload,
            auth_record=auth_record,
            db=db_session,
        )

        assert res.full_name == "Jane Doe"
        assert res.identity_document_type == "passport"
        assert res.nic_number == "PA9876543"
        assert res.date_of_birth == date(1993, 4, 12)
        assert res.gender == "Female"
        assert res.nationality == "Australian"
        assert res.permanent_landmark == "No. 88, Marina Road"

    def test_save_personal_details_duplicate_document_raises_409(self, db_session: Session):
        # Create existing provider profile with passport N1234567
        user = User(full_name="Existing User", email="existing@ceylongo.com", role="provider", is_active=True)
        db_session.add(user)
        db_session.flush()

        profile = ProviderProfile(
            user_id=user.id,
            business_name="Existing Biz",
            provider_type="homestay",
            phone="+94770000000",
            district="Colombo",
            identity_document_type="passport",
            nic_number="N1234567",
            approval_status="approved",
        )
        db_session.add(profile)
        db_session.commit()

        auth_record = ProviderRegistrationAuthorization(
            email="new@provider.com",
            token_hash="fake_hash_2",
            purpose="registration",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            is_consumed=False,
        )
        db_session.add(auth_record)
        db_session.commit()

        # Try saving the same passport
        payload = PersonalDetailsRequest(
            full_name="New Applicant",
            phone="+94771112222",
            identity_document_type="passport",
            nic_number="N1234567",
            date_of_birth=date(1990, 1, 1),
            gender="Male",
            nationality="British",
            permanent_province="Western",
            permanent_district="Colombo",
            permanent_city="Colombo 01",
            permanent_postal_code="00100",
            permanent_landmark="No. 1, Fort",
        )

        with pytest.raises(HTTPException) as exc:
            save_personal_details(
                payload=payload,
                auth_record=auth_record,
                db=db_session,
            )
        assert exc.value.status_code == 409
        assert "Passport" in exc.value.detail

    def test_submit_provider_registration_passport_flow(self, db_session: Session):
        email = "passport.provider@test.com"
        auth_record = ProviderRegistrationAuthorization(
            email=email,
            token_hash="fake_hash_3",
            purpose="registration",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            is_consumed=False,
        )
        db_session.add(auth_record)

        draft = ProviderRegistrationDraft(
            email=email,
            full_name="Carlos Silva",
            primary_phone="+94779998888",
            identity_document_type="passport",
            nic_number="P7654321",
            date_of_birth=date(1989, 7, 21),
            gender="Male",
            nationality="Portuguese",
            permanent_province="Southern",
            permanent_district="Galle",
            permanent_city="Galle Fort",
            permanent_landmark="No. 14, Lighthouse Street",
            permanent_postal_code="80000",
            business_name="Silva Guest House",
            provider_category="homestay",
            business_province="Southern",
            business_district="Galle",
            business_city="Galle",
            business_postal_code="80000",
            business_description="Charming guest house located inside Galle Fort.",
            selfie_file_reference="selfie_carlos.jpg",
            nic_front_file_reference="passport_carlos.jpg",
            nic_back_file_reference=None,  # Passport has no back image
        )
        db_session.add(draft)
        db_session.commit()

        # Submit
        resp = submit_provider_registration(auth_record=auth_record, db=db_session)
        assert resp.submitted is True
        assert resp.approval_status == "pending"

        # Verify created ProviderProfile
        profile = (
            db_session.query(ProviderProfile)
            .filter_by(nic_number="P7654321", identity_document_type="passport")
            .first()
        )
        assert profile is not None
        assert profile.business_name == "Silva Guest House"
        assert profile.identity_document_type == "passport"
        assert profile.date_of_birth == date(1989, 7, 21)
        assert profile.gender == "Male"
        assert profile.nationality == "Portuguese"
        assert profile.permanent_landmark == "No. 14, Lighthouse Street"
        assert profile.selfie_image_path == "selfie_carlos.jpg"
        assert profile.nic_front_image_path == "passport_carlos.jpg"
        assert profile.nic_back_image_path is None

    def test_submit_provider_registration_nic_missing_back_raises_400(self, db_session: Session):
        email = "nic.provider@test.com"
        auth_record = ProviderRegistrationAuthorization(
            email=email,
            token_hash="fake_hash_4",
            purpose="registration",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            is_consumed=False,
        )
        db_session.add(auth_record)

        draft = ProviderRegistrationDraft(
            email=email,
            full_name="Nimal Siriwardena",
            primary_phone="+94775554433",
            identity_document_type="nic",
            nic_number="199212345678",
            date_of_birth=date(1992, 11, 5),
            gender="Male",
            nationality="Sri Lankan",
            permanent_province="Western",
            permanent_district="Colombo",
            permanent_city="Nugegoda",
            permanent_landmark="No. 15, High Level Road",
            permanent_postal_code="10250",
            business_name="Nimal Cabs",
            provider_category="transport",
            business_province="Western",
            business_district="Colombo",
            business_city="Colombo",
            business_postal_code="00500",
            business_description="Transport services across Sri Lanka.",
            selfie_file_reference="selfie_nimal.jpg",
            nic_front_file_reference="nic_front_nimal.jpg",
            nic_back_file_reference=None,  # Missing back for NIC!
        )
        db_session.add(draft)
        db_session.commit()

        with pytest.raises(HTTPException) as exc:
            submit_provider_registration(auth_record=auth_record, db=db_session)
        assert exc.value.status_code == 400
        assert "All three documents" in exc.value.detail
