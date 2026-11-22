"""
Comprehensive Phase 2 Automated Tests for Local Tour Guide Module 05.

Covers all 10 Phase 2 deliverables:
1. Public tourist guide discovery and listing (strict 4-condition visibility gate)
2. Search by keyword, location, district, city, language, category, experience,
   price (daily/hourly/package/any), currency, verified, rating, group_size, and availability
3. Pagination, sorting (including active sponsored boost), and combined filters
4. Public guide detail with ordered photos, verified badge, languages, service areas,
   experience verification distinction, and approved-only credentials
5. Public tour package listing (filtered) and package detail
6. Public availability calendar with anonymized booked windows (zero traveler PII)
7. Server-authoritative package, hourly, and daily price preview with currency & group-size checks
8. Privacy-aware public contact endpoint (masked for anonymous, direct phone for authenticated)
9. Security checks preventing leakage of private credentials, internal paths, or unapproved guides
10. Validation, authorization, and regression checks
"""

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from tests.access_tokens import create_fixture_access_token as create_access_token
from app.database.connection import get_db
from app.main import app
from app.models.provider_profile import ProviderProfile
from app.models.shared import ListingReference, PromotionCampaign
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

test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


@event.listens_for(test_engine, "connect")
def _configure_sqlite(dbapi_connection, connection_record):
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

PUBLIC_BASE = "/api/v1/tour-guides"


def bearer(user: User, expires_delta: timedelta | None = None) -> dict[str, str]:
    token = create_access_token({"sub": str(user.id)}, expires_delta=expires_delta)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def setup_db():
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
def client(db_session: Session):
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def catalog_data(db_session: Session):
    admin = User(
        full_name="Admin One", email="admin.p2@example.com", role="admin", is_active=True
    )
    traveler = User(
        full_name="Elena Tourist", email="elena.p2@example.com", role="traveler", is_active=True
    )
    inactive_traveler = User(
        full_name="Inactive User", email="inactive.t@example.com", role="traveler", is_active=False
    )

    def make_guide(
        *,
        idx: int,
        full_name: str,
        business_name: str,
        bio: str,
        years: int,
        specialties: str,
        daily_rate: str,
        hourly_rate: str | None,
        currency: str = "LKR",
        base_city: str,
        base_district: str,
        approval_status: str = "approved",
        verification_status: str = "unverified",
        is_active: bool = True,
        is_available: bool = True,
        rating: str = "4.50",
        review_count: int = 10,
        max_group_size: int = 8,
        provider_status: str = "approved",
        user_active: bool = True,
        phone: str = "0771234567",
        secondary_phone: str | None = None,
        license_type: str | None = "national",
    ) -> TourGuide:
        u = User(
            full_name=full_name,
            email=f"guide{idx}.p2@example.com",
            role="provider",
            is_active=user_active,
        )
        db_session.add(u)
        db_session.flush()
        prof = ProviderProfile(
            user_id=u.id,
            business_name=business_name,
            provider_type="tour_guide",
            phone=phone,
            secondary_phone=secondary_phone,
            district=base_district,
            nic_number=f"1990123456{idx:02d}",
            approval_status=provider_status,
        )
        db_session.add(prof)
        db_session.flush()
        g = TourGuide(
            provider_profile_id=prof.id,
            bio=bio,
            years_of_experience=years,
            specialties=specialties,
            license_number=f"SLTDA-2026-{idx:04d}",
            license_type=license_type,
            hourly_rate=Decimal(hourly_rate) if hourly_rate is not None else None,
            daily_rate=Decimal(daily_rate),
            currency=currency,
            base_city=base_city,
            base_district=base_district,
            base_latitude=Decimal("7.290572"),
            base_longitude=Decimal("80.633728"),
            is_available=is_available,
            is_active=is_active,
            approval_status=approval_status,
            verification_status=verification_status,
            rating_average=Decimal(rating),
            review_count=review_count,
            cancellation_policy="Free cancellation up to 24 hours before start.",
            free_cancellation_hours=24,
            max_group_size=max_group_size,
        )
        db_session.add(g)
        db_session.flush()
        return g

    db_session.add_all([admin, traveler, inactive_traveler])
    db_session.flush()

    # 3 publicly visible guides
    g_kandy_verified = make_guide(
        idx=1,
        full_name="Sunil Perera",
        business_name="Sunil Heritage & Culture",
        bio="Licensed national guide specializing in Kandy temples and ancient Sigiriya.",
        years=12,
        specialties="Cultural & Heritage, City & Village",
        daily_rate="15000.00",
        hourly_rate="2500.00",
        base_city="Kandy",
        base_district="Kandy",
        verification_status="verified",
        rating="4.90",
        review_count=42,
        max_group_size=10,
        phone="0771234567",
        secondary_phone="0712223334",
    )
    g_galle_eco = make_guide(
        idx=2,
        full_name="Nimal Silva",
        business_name="Nimal Southern Wildlife",
        bio="Eco naturalist leading Sinharaja rainforest birdwatching and Galle walks.",
        years=6,
        specialties="Wildlife & Eco, Adventure & Nature",
        daily_rate="12000.00",
        hourly_rate=None,
        base_city="Galle",
        base_district="Galle",
        verification_status="unverified",
        rating="4.60",
        review_count=18,
        max_group_size=6,
        phone="0779876543",
    )
    g_jaffna_budget = make_guide(
        idx=3,
        full_name="Karthik Selvam",
        business_name="Jaffna Peninsula Walks",
        bio="Bilingual Tamil and English local storyteller for Jaffna heritage and villages.",
        years=4,
        specialties="Cultural & Heritage, City & Village",
        daily_rate="9000.00",
        hourly_rate="1800.00",
        base_city="Jaffna",
        base_district="Jaffna",
        verification_status="unverified",
        is_available=False,
        rating="4.70",
        review_count=9,
        max_group_size=4,
        phone="0761112233",
    )

    # 6 hidden guides covering every non-public state
    g_draft = make_guide(
        idx=4, full_name="Draft Guide", business_name="Draft Tours",
        bio="Not submitted yet.", years=3, specialties="City & Village",
        daily_rate="8000.00", hourly_rate="1500.00", base_city="Colombo", base_district="Colombo",
        approval_status="draft", is_active=False,
    )
    g_pending = make_guide(
        idx=5, full_name="Pending Guide", business_name="Pending Tours",
        bio="Waiting review.", years=3, specialties="City & Village",
        daily_rate="8000.00", hourly_rate="1500.00", base_city="Colombo", base_district="Colombo",
        approval_status="pending", is_active=False,
    )
    g_rejected = make_guide(
        idx=6, full_name="Rejected Guide", business_name="Rejected Tours",
        bio="Rejected profile.", years=3, specialties="City & Village",
        daily_rate="8000.00", hourly_rate="1500.00", base_city="Colombo", base_district="Colombo",
        approval_status="rejected", is_active=False,
    )
    g_suspended = make_guide(
        idx=7, full_name="Suspended Guide", business_name="Suspended Tours",
        bio="Suspended profile.", years=3, specialties="City & Village",
        daily_rate="8000.00", hourly_rate="1500.00", base_city="Colombo", base_district="Colombo",
        approval_status="suspended", is_active=False,
    )
    g_inactive = make_guide(
        idx=8, full_name="Inactive Guide", business_name="Inactive Tours",
        bio="Deactivated by owner.", years=5, specialties="City & Village",
        daily_rate="8000.00", hourly_rate="1500.00", base_city="Kandy", base_district="Kandy",
        approval_status="approved", is_active=False,
    )
    g_unapproved_provider = make_guide(
        idx=9, full_name="Pending Provider", business_name="Unapproved Provider Tours",
        bio="Provider not approved.", years=5, specialties="City & Village",
        daily_rate="8000.00", hourly_rate="1500.00", base_city="Kandy", base_district="Kandy",
        approval_status="approved", is_active=True, provider_status="pending",
    )
    g_inactive_user = make_guide(
        idx=10, full_name="Disabled User", business_name="Disabled User Tours",
        bio="User deactivated.", years=5, specialties="City & Village",
        daily_rate="8000.00", hourly_rate="1500.00", base_city="Kandy", base_district="Kandy",
        approval_status="approved", is_active=True, user_active=False,
    )

    # Languages
    db_session.add_all([
        TourGuideLanguage(tour_guide_id=g_kandy_verified.id, language_code="en", language_name="English", proficiency_level="fluent", is_primary=True),
        TourGuideLanguage(tour_guide_id=g_kandy_verified.id, language_code="si", language_name="Sinhala", proficiency_level="native", is_primary=False),
        TourGuideLanguage(tour_guide_id=g_galle_eco.id, language_code="en", language_name="English", proficiency_level="fluent", is_primary=True),
        TourGuideLanguage(tour_guide_id=g_galle_eco.id, language_code="de", language_name="German", proficiency_level="conversational", is_primary=False),
        TourGuideLanguage(tour_guide_id=g_jaffna_budget.id, language_code="ta", language_name="Tamil", proficiency_level="native", is_primary=True),
        TourGuideLanguage(tour_guide_id=g_jaffna_budget.id, language_code="en", language_name="English", proficiency_level="fluent", is_primary=False),
    ])

    # Service areas
    db_session.add_all([
        TourGuideServiceArea(tour_guide_id=g_kandy_verified.id, province="Central", district="Kandy", city="Kandy", is_primary=True),
        TourGuideServiceArea(tour_guide_id=g_kandy_verified.id, province="Central", district="Matale", city="Sigiriya", is_primary=False),
        TourGuideServiceArea(tour_guide_id=g_galle_eco.id, province="Southern", district="Galle", city="Galle Fort", is_primary=True),
        TourGuideServiceArea(tour_guide_id=g_galle_eco.id, province="Sabaragamuwa", district="Ratnapura", city="Sinharaja", is_primary=False),
        TourGuideServiceArea(tour_guide_id=g_jaffna_budget.id, province="Northern", district="Jaffna", city="Nallur", is_primary=True),
    ])

    # Images
    db_session.add_all([
        TourGuideImage(tour_guide_id=g_kandy_verified.id, image_url="/api/v1/uploads/tour-guides/kandy_tour.jpg", caption="Sigiriya climb", display_order=2, is_profile=False),
        TourGuideImage(tour_guide_id=g_kandy_verified.id, image_url="/api/v1/uploads/tour-guides/kandy_profile.jpg", caption="Profile photo", display_order=1, is_profile=True),
        TourGuideImage(tour_guide_id=g_galle_eco.id, image_url="/api/v1/uploads/tour-guides/galle_profile.jpg", caption="Rainforest trail", display_order=0, is_profile=True),
    ])

    # Credentials (1 approved, 1 pending, 1 rejected on g_kandy_verified)
    cred_approved = TourGuideCredential(
        tour_guide_id=g_kandy_verified.id,
        credential_type="sltda_license",
        title="SLTDA National Guide License",
        issuing_authority="Sri Lanka Tourism Development Authority",
        certificate_number="SECRET-CERT-999",
        expiry_date=date(2028, 12, 31),
        file_reference="private_internal_sltda_sunil.pdf",
        is_confidential=True,
        verification_status="approved",
    )
    cred_pending = TourGuideCredential(
        tour_guide_id=g_kandy_verified.id,
        credential_type="first_aid",
        title="Unreviewed First Aid Certificate",
        issuing_authority="Red Cross",
        certificate_number="SECRET-FA-111",
        file_reference="private_internal_fa_sunil.pdf",
        is_confidential=True,
        verification_status="pending",
    )
    cred_rejected = TourGuideCredential(
        tour_guide_id=g_kandy_verified.id,
        credential_type="wildlife_permit",
        title="Rejected Wildlife Permit",
        issuing_authority="DWC",
        certificate_number="SECRET-DWC-222",
        file_reference="private_internal_dwc_sunil.pdf",
        is_confidential=True,
        verification_status="rejected",
    )
    db_session.add_all([cred_approved, cred_pending, cred_rejected])
    db_session.flush()
    db_session.add(
        TourGuideVerification(
            tour_guide_id=g_kandy_verified.id,
            credential_id=cred_approved.id,
            reviewed_by=admin.id,
            verification_type="sltda_check",
            status="approved",
            notes="Verified",
        )
    )

    # Packages (active + inactive + draft-guide package)
    pkg_half_per_person = TourGuidePackage(
        tour_guide_id=g_kandy_verified.id,
        title="Kandy Temple Half-Day Walk",
        description="Guided walking tour of Temple of the Tooth and historic centre.",
        itinerary="08:00 Meet -> 08:30 Temple -> 12:00 End",
        duration_hours=Decimal("4.00"),
        pricing_type="per_person",
        price=Decimal("4500.00"),
        currency="LKR",
        max_people=6,
        included_services="Guide commentary, water",
        excluded_services="Temple entry fee",
        meeting_point="Kandy City Centre Clock Tower",
        meeting_latitude=Decimal("7.292500"),
        meeting_longitude=Decimal("80.637000"),
        is_active=True,
    )
    pkg_full_per_group = TourGuidePackage(
        tour_guide_id=g_kandy_verified.id,
        title="Sigiriya & Dambulla Private Full-Day Tour",
        description="Full-day heritage excursion covering Sigiriya Lion Rock and Dambulla Caves.",
        duration_hours=Decimal("9.00"),
        pricing_type="per_group",
        price=Decimal("22000.00"),
        currency="LKR",
        max_people=8,
        meeting_point="Kandy Queen's Hotel Lobby",
        is_active=True,
    )
    pkg_inactive = TourGuidePackage(
        tour_guide_id=g_kandy_verified.id,
        title="Archived Seasonal Tour",
        description="Inactive package that must never be returned publicly.",
        duration_hours=Decimal("5.00"),
        pricing_type="per_person",
        price=Decimal("3000.00"),
        currency="LKR",
        max_people=4,
        meeting_point="Kandy Lake",
        is_active=False,
    )
    pkg_galle = TourGuidePackage(
        tour_guide_id=g_galle_eco.id,
        title="Sinharaja Birdwatching Expedition",
        description="Guided rainforest wildlife expedition.",
        duration_hours=Decimal("7.00"),
        pricing_type="per_person",
        price=Decimal("6000.00"),
        currency="LKR",
        max_people=5,
        meeting_point="Kudawa Conservation Gate",
        is_active=True,
    )
    pkg_on_draft_guide = TourGuidePackage(
        tour_guide_id=g_draft.id,
        title="Draft Guide Hidden Package",
        description="Package on an unapproved draft guide.",
        duration_hours=Decimal("3.00"),
        pricing_type="per_person",
        price=Decimal("2500.00"),
        currency="LKR",
        max_people=4,
        meeting_point="Colombo Fort",
        is_active=True,
    )
    db_session.add_all([
        pkg_half_per_person,
        pkg_full_per_group,
        pkg_inactive,
        pkg_galle,
        pkg_on_draft_guide,
    ])
    db_session.flush()

    # Availability & confirmed booking for calendar/filter tests
    db_session.add_all([
        # g_kandy_verified full-day blocked on 2026-12-10
        TourGuideAvailability(
            tour_guide_id=g_kandy_verified.id,
            available_date=date(2026, 12, 10),
            start_time=None,
            end_time=None,
            is_available=False,
            reason="Private family commitment",
        ),
        # g_kandy_verified morning open + afternoon blocked on 2026-12-11
        TourGuideAvailability(
            tour_guide_id=g_kandy_verified.id,
            available_date=date(2026, 12, 11),
            start_time=time(8, 0),
            end_time=time(12, 0),
            is_available=True,
        ),
        TourGuideAvailability(
            tour_guide_id=g_kandy_verified.id,
            available_date=date(2026, 12, 11),
            start_time=time(13, 0),
            end_time=time(18, 0),
            is_available=False,
            reason="Personal errand",
        ),
        # Confirmed partial-day booking on 2026-12-12 (09:00-13:00) with sensitive traveler info
        TourGuideBooking(
            tour_guide_id=g_kandy_verified.id,
            traveler_id=traveler.id,
            package_id=pkg_half_per_person.id,
            booking_date=date(2026, 12, 12),
            start_datetime=datetime(2026, 12, 12, 9, 0),
            end_datetime=datetime(2026, 12, 12, 13, 0),
            number_of_people=2,
            pickup_location="SECRET HOTEL ROOM 404",
            special_requests="CONFIDENTIAL MEDICAL NOTE",
            total_price=Decimal("9000.00"),
            booking_status="confirmed",
        ),
        # Full-day confirmed booking on 2026-12-13
        TourGuideBooking(
            tour_guide_id=g_kandy_verified.id,
            traveler_id=traveler.id,
            booking_date=date(2026, 12, 13),
            start_datetime=datetime(2026, 12, 13, 0, 0),
            end_datetime=datetime(2026, 12, 13, 23, 59, 59, 999999),
            number_of_people=2,
            total_price=Decimal("15000.00"),
            booking_status="confirmed",
        ),
    ])

    db_session.commit()

    return {
        "admin": admin,
        "traveler": traveler,
        "inactive_traveler": inactive_traveler,
        "g_kandy_verified": g_kandy_verified,
        "g_galle_eco": g_galle_eco,
        "g_jaffna_budget": g_jaffna_budget,
        "hidden_guides": [
            g_draft,
            g_pending,
            g_rejected,
            g_suspended,
            g_inactive,
            g_unapproved_provider,
            g_inactive_user,
        ],
        "pkg_half_per_person": pkg_half_per_person,
        "pkg_full_per_group": pkg_full_per_group,
        "pkg_inactive": pkg_inactive,
        "pkg_galle": pkg_galle,
        "pkg_on_draft_guide": pkg_on_draft_guide,
    }


# =============================================================================
# 1. STRICT PUBLIC VISIBILITY GATE & HIDDEN GUIDE PROTECTION
# =============================================================================

def test_public_discovery_hides_all_unapproved_suspended_and_inactive_guides(
    client, catalog_data
):
    res = client.get(PUBLIC_BASE)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["total"] == 3
    returned_ids = {item["id"] for item in body["items"]}
    assert returned_ids == {
        catalog_data["g_kandy_verified"].id,
        catalog_data["g_galle_eco"].id,
        catalog_data["g_jaffna_budget"].id,
    }

    # Every hidden guide returns 404 across ALL public detail & sub-resource endpoints
    traveler_headers = bearer(catalog_data["traveler"])
    for hidden in catalog_data["hidden_guides"]:
        assert client.get(f"{PUBLIC_BASE}/{hidden.id}").status_code == 404
        assert client.get(f"{PUBLIC_BASE}/{hidden.id}/packages").status_code == 404
        assert client.get(f"{PUBLIC_BASE}/{hidden.id}/availability").status_code == 404
        assert client.get(f"{PUBLIC_BASE}/{hidden.id}/contact").status_code == 404
        assert client.get(
            f"{PUBLIC_BASE}/{hidden.id}/contact", headers=traveler_headers
        ).status_code == 404
        assert client.post(
            f"{PUBLIC_BASE}/{hidden.id}/price-preview",
            json={"rate_type": "daily", "duration_days": 1, "number_of_people": 2},
        ).status_code == 404

    # Inactive package & package on draft guide return 404
    assert client.get(
        f"{PUBLIC_BASE}/packages/{catalog_data['pkg_inactive'].id}"
    ).status_code == 404
    assert client.get(
        f"{PUBLIC_BASE}/packages/{catalog_data['pkg_on_draft_guide'].id}"
    ).status_code == 404


# =============================================================================
# 2. SEARCH, INDIVIDUAL FILTERS & COMBINED FILTERS (TR-02, TR-03, TR-06, TR-07)
# =============================================================================

def test_public_search_and_combined_filters(client, catalog_data):
    kandy_id = catalog_data["g_kandy_verified"].id
    galle_id = catalog_data["g_galle_eco"].id
    jaffna_id = catalog_data["g_jaffna_budget"].id

    # Keyword search matches service area ('Sigiriya')
    r_q = client.get(PUBLIC_BASE, params={"q": "Sigiriya"}).json()
    assert [i["id"] for i in r_q["items"]] == [kandy_id]

    # Location & district match covered service areas ('Ratnapura' / 'Sinharaja')
    r_dist = client.get(PUBLIC_BASE, params={"district": "Matale"}).json()
    assert [i["id"] for i in r_dist["items"]] == [kandy_id]
    r_city = client.get(PUBLIC_BASE, params={"city": "Sinharaja"}).json()
    assert [i["id"] for i in r_city["items"]] == [galle_id]
    r_loc = client.get(PUBLIC_BASE, params={"location": "Nallur"}).json()
    assert [i["id"] for i in r_loc["items"]] == [jaffna_id]

    # Language filter by code and by name
    r_ta = client.get(PUBLIC_BASE, params={"language": "ta"}).json()
    assert [i["id"] for i in r_ta["items"]] == [jaffna_id]
    r_de = client.get(PUBLIC_BASE, params={"language": "German"}).json()
    assert [i["id"] for i in r_de["items"]] == [galle_id]

    # Category filter by slug and canonical name; invalid category -> 422
    r_cat = client.get(PUBLIC_BASE, params={"category": "wildlife_eco"}).json()
    assert [i["id"] for i in r_cat["items"]] == [galle_id]
    r_cat_name = client.get(PUBLIC_BASE, params={"category": "Cultural & Heritage"}).json()
    assert {i["id"] for i in r_cat_name["items"]} == {kandy_id, jaffna_id}
    assert client.get(PUBLIC_BASE, params={"category": "space_tours"}).status_code == 422

    # Experience, verified_only, min_rating, available_only, group_size
    assert [i["id"] for i in client.get(PUBLIC_BASE, params={"min_experience": 10}).json()["items"]] == [kandy_id]
    assert [i["id"] for i in client.get(PUBLIC_BASE, params={"verified_only": "true"}).json()["items"]] == [kandy_id]
    assert [i["id"] for i in client.get(PUBLIC_BASE, params={"min_rating": "4.80"}).json()["items"]] == [kandy_id]
    assert {i["id"] for i in client.get(PUBLIC_BASE, params={"available_only": "true"}).json()["items"]} == {kandy_id, galle_id}
    assert [i["id"] for i in client.get(PUBLIC_BASE, params={"group_size": 8}).json()["items"]] == [kandy_id]

    # Price filters across daily / hourly / package / any
    assert [
        i["id"]
        for i in client.get(
            PUBLIC_BASE, params={"min_price": "10000", "max_price": "13000", "price_basis": "daily"}
        ).json()["items"]
    ] == [galle_id]
    assert {
        i["id"]
        for i in client.get(
            PUBLIC_BASE, params={"max_price": "3000", "price_basis": "hourly"}
        ).json()["items"]
    } == {kandy_id, jaffna_id}
    assert [
        i["id"]
        for i in client.get(
            PUBLIC_BASE, params={"max_price": "5000", "price_basis": "package"}
        ).json()["items"]
    ] == [kandy_id]
    assert client.get(
        PUBLIC_BASE, params={"min_price": "20000", "max_price": "10000"}
    ).status_code == 422

    # Availability date filter excludes full-day blocked (2026-12-10) and full-day booked (2026-12-13)
    r_avail_blocked = client.get(PUBLIC_BASE, params={"available_date": "2026-12-10"}).json()
    assert [i["id"] for i in r_avail_blocked["items"]] == [galle_id]
    r_avail_booked = client.get(PUBLIC_BASE, params={"available_date": "2026-12-13"}).json()
    assert [i["id"] for i in r_avail_booked["items"]] == [galle_id]
    # Partial-day booked (2026-12-12) still includes g_kandy_verified
    r_avail_partial = client.get(PUBLIC_BASE, params={"available_date": "2026-12-12"}).json()
    assert {i["id"] for i in r_avail_partial["items"]} == {kandy_id, galle_id}

    # Combined multi-filter query
    combined = client.get(
        PUBLIC_BASE,
        params={
            "district": "Matale",
            "language": "en",
            "category": "cultural_heritage",
            "min_experience": 5,
            "max_price": "20000",
            "verified_only": "true",
            "available_date": "2026-12-15",
            "group_size": 6,
        },
    ).json()
    assert combined["total"] == 1 and combined["items"][0]["id"] == kandy_id


# =============================================================================
# 3. PAGINATION, SORTING & SPONSORED PRIORITY
# =============================================================================

def test_public_pagination_sorting_and_sponsored_ranking(
    client, db_session, catalog_data
):
    kandy = catalog_data["g_kandy_verified"]
    galle = catalog_data["g_galle_eco"]
    jaffna = catalog_data["g_jaffna_budget"]

    # Price ascending: Jaffna (9000) -> Galle (12000) -> Kandy (15000)
    asc = client.get(PUBLIC_BASE, params={"sort_by": "price_asc"}).json()
    assert [i["id"] for i in asc["items"]] == [jaffna.id, galle.id, kandy.id]

    # Price descending
    desc = client.get(PUBLIC_BASE, params={"sort_by": "price_desc"}).json()
    assert [i["id"] for i in desc["items"]] == [kandy.id, galle.id, jaffna.id]

    # Experience descending: 12 -> 6 -> 4
    exp = client.get(PUBLIC_BASE, params={"sort_by": "experience_desc"}).json()
    assert [i["id"] for i in exp["items"]] == [kandy.id, galle.id, jaffna.id]

    # Rating descending: 4.90 -> 4.70 -> 4.60
    rat = client.get(PUBLIC_BASE, params={"sort_by": "rating_desc"}).json()
    assert [i["id"] for i in rat["items"]] == [kandy.id, jaffna.id, galle.id]

    # Pagination
    p1 = client.get(PUBLIC_BASE, params={"sort_by": "price_asc", "page": 1, "page_size": 2}).json()
    assert len(p1["items"]) == 2
    assert p1["total"] == 3 and p1["total_pages"] == 2 and p1["has_more"] is True
    p2 = client.get(PUBLIC_BASE, params={"sort_by": "price_asc", "page": 2, "page_size": 2}).json()
    assert len(p2["items"]) == 1 and p2["has_more"] is False

    # Active sponsored campaign promotes Galle to #1 in 'recommended' and sets is_sponsored=True
    now_utc = datetime.now(UTC).replace(tzinfo=None)
    galle_ref = ListingReference(
        module_type="tour_guide",
        target_id=galle.id,
        provider_profile_id=galle.provider_profile_id,
        title="Nimal Southern Wildlife",
        district="Galle",
        city="Galle",
        is_active=True,
    )
    db_session.add(galle_ref)
    db_session.flush()
    db_session.add(
        PromotionCampaign(
            provider_profile_id=galle.provider_profile_id,
            listing_reference_id=galle_ref.id,
            campaign_name="Galle Featured Guide",
            target_module="tour_guide",
            budget=Decimal("5000.00"),
            status="active",
            starts_at=now_utc - timedelta(days=1),
            ends_at=now_utc + timedelta(days=7),
        )
    )
    db_session.commit()

    rec = client.get(PUBLIC_BASE, params={"sort_by": "recommended"}).json()
    assert rec["items"][0]["id"] == galle.id
    assert rec["items"][0]["is_sponsored"] is True
    assert rec["items"][1]["id"] == kandy.id
    assert rec["items"][1]["is_sponsored"] is False


# =============================================================================
# 4. PUBLIC GUIDE DETAIL & CREDENTIAL PRIVACY (TR-04..08, TR-20, GS-01, GS-02)
# =============================================================================

def test_public_guide_detail_and_strict_privacy_enforcement(client, catalog_data):
    kandy = catalog_data["g_kandy_verified"]
    galle = catalog_data["g_galle_eco"]

    res = client.get(f"{PUBLIC_BASE}/{kandy.id}")
    assert res.status_code == 200, res.text
    detail = res.json()

    assert detail["is_verified"] is True
    assert detail["experience_is_verified"] is True
    assert detail["experience_label"] == "Verified"
    assert detail["license_type"] == "national"

    # Images ordered with primary profile photo first
    assert len(detail["images"]) == 2
    assert detail["images"][0]["is_profile"] is True

    # Languages & service areas ordered with primary first
    assert detail["languages"][0]["language_code"] == "en"
    assert detail["service_areas"][0]["district"] == "Kandy"

    # Only active packages returned (inactive package excluded)
    assert len(detail["packages"]) == 2
    assert all("Archived" not in p["title"] for p in detail["packages"])

    # ONLY approved credential returned; pending & rejected credentials excluded
    assert len(detail["verified_credentials"]) == 1
    vc = detail["verified_credentials"][0]
    assert vc["title"] == "SLTDA National Guide License"
    assert vc["verified"] is True
    assert "file_reference" not in vc
    assert "certificate_number" not in vc

    # Global string leakage assertion across entire serialized response
    raw_text = res.text
    for forbidden in (
        "private_internal_",
        "SECRET-CERT-999",
        "SECRET-FA-111",
        "SECRET-DWC-222",
        "Unreviewed First Aid",
        "Rejected Wildlife",
        "199012345601",
        "guide1.p2@example.com",
    ):
        assert forbidden not in raw_text

    # Unverified guide shows self-reported experience and hides unverified license_type
    galle_detail = client.get(f"{PUBLIC_BASE}/{galle.id}").json()
    assert galle_detail["is_verified"] is False
    assert galle_detail["experience_is_verified"] is False
    assert galle_detail["experience_label"] == "Self-reported"
    assert galle_detail["license_type"] is None
    assert galle_detail["verified_credentials"] == []


# =============================================================================
# 5. PUBLIC PACKAGE LISTING & PACKAGE DETAIL (TR-08, TR-09, GS-03..05)
# =============================================================================

def test_public_packages_listing_filters_and_detail(client, catalog_data):
    kandy = catalog_data["g_kandy_verified"]
    pkg_half = catalog_data["pkg_half_per_person"]

    all_pkgs = client.get(f"{PUBLIC_BASE}/{kandy.id}/packages").json()
    assert len(all_pkgs) == 2

    half_only = client.get(
        f"{PUBLIC_BASE}/{kandy.id}/packages",
        params={"duration_category": "half_day", "pricing_type": "per_person", "max_price": "5000"},
    ).json()
    assert len(half_only) == 1 and half_only[0]["id"] == pkg_half.id

    group_only = client.get(
        f"{PUBLIC_BASE}/{kandy.id}/packages",
        params={"pricing_type": "per_group", "min_group_size": 7},
    ).json()
    assert len(group_only) == 1 and group_only[0]["duration_category"] == "full_day"

    detail_res = client.get(f"{PUBLIC_BASE}/packages/{pkg_half.id}")
    assert detail_res.status_code == 200
    body = detail_res.json()
    assert body["package"]["meeting_point"] == "Kandy City Centre Clock Tower"
    assert body["guide_id"] == kandy.id
    assert body["guide_is_verified"] is True


# =============================================================================
# 6. PUBLIC AVAILABILITY CALENDAR & ANONYMIZED BOOKINGS (TR-10)
# =============================================================================

def test_public_availability_calendar_statuses_and_pii_protection(client, catalog_data):
    kandy = catalog_data["g_kandy_verified"]
    jaffna = catalog_data["g_jaffna_budget"]

    res = client.get(
        f"{PUBLIC_BASE}/{kandy.id}/availability",
        params={"date_from": "2026-12-10", "date_to": "2026-12-14"},
    )
    assert res.status_code == 200, res.text
    cal = res.json()
    assert cal["guide_is_available"] is True
    by_date = {d["date"]: d for d in cal["days"]}

    assert by_date["2026-12-10"]["status"] == "unavailable"
    assert by_date["2026-12-10"]["is_bookable"] is False
    assert by_date["2026-12-11"]["status"] == "partially_available"
    assert by_date["2026-12-11"]["is_bookable"] is True
    assert len(by_date["2026-12-11"]["open_windows"]) == 1
    assert len(by_date["2026-12-11"]["blocked_windows"]) == 1
    assert by_date["2026-12-12"]["status"] == "partially_available"
    assert len(by_date["2026-12-12"]["booked_windows"]) == 1
    assert by_date["2026-12-13"]["status"] == "booked"
    assert by_date["2026-12-13"]["is_bookable"] is False
    assert by_date["2026-12-14"]["status"] == "available"
    assert by_date["2026-12-14"]["is_bookable"] is True

    # ZERO traveler PII or internal block reason leaked in calendar response
    for secret in ("SECRET HOTEL ROOM 404", "CONFIDENTIAL MEDICAL NOTE", "Elena Tourist", "Private family"):
        assert secret not in res.text

    # Validation checks on date range
    assert client.get(
        f"{PUBLIC_BASE}/{kandy.id}/availability",
        params={"date_from": "2026-12-20", "date_to": "2026-12-10"},
    ).status_code == 422
    assert client.get(
        f"{PUBLIC_BASE}/{kandy.id}/availability",
        params={"date_from": "2026-01-01", "date_to": "2026-06-01"},
    ).status_code == 422

    # Guide with is_available=False marks all days unavailable
    jaffna_cal = client.get(
        f"{PUBLIC_BASE}/{jaffna.id}/availability",
        params={"date_from": "2026-12-14", "date_to": "2026-12-15"},
    ).json()
    assert all(d["status"] == "unavailable" and not d["is_bookable"] for d in jaffna_cal["days"])


# =============================================================================
# 7. PRICE PREVIEW WITH CURRENCY & GROUP-SIZE VALIDATION (TR-09, GS-04, GS-05)
# =============================================================================

def test_public_price_preview_calculations_and_validation(client, catalog_data):
    kandy = catalog_data["g_kandy_verified"]
    galle = catalog_data["g_galle_eco"]
    pkg_half = catalog_data["pkg_half_per_person"]
    pkg_full = catalog_data["pkg_full_per_group"]
    pkg_galle = catalog_data["pkg_galle"]

    # per_person package: 4500 * 3 = 13500 LKR, open slot
    r_pp = client.post(
        f"{PUBLIC_BASE}/{kandy.id}/price-preview",
        json={
            "rate_type": "package",
            "package_id": pkg_half.id,
            "number_of_people": 3,
            "booking_date": "2026-12-14",
            "start_time": "08:00:00",
        },
    )
    assert r_pp.status_code == 200, r_pp.text
    pp = r_pp.json()
    assert Decimal(pp["total_price"]) == Decimal("13500.00")
    assert pp["currency"] == "LKR"
    assert pp["duration_category"] == "half_day"
    assert pp["is_available_for_requested_window"] is True
    assert pp["estimated_end_datetime"].endswith("T12:00:00")

    # per_group package: flat 22000 LKR for 5 travelers, blocked date reports unavailable
    r_pg = client.post(
        f"{PUBLIC_BASE}/{kandy.id}/price-preview",
        json={
            "rate_type": "package",
            "package_id": pkg_full.id,
            "number_of_people": 5,
            "booking_date": "2026-12-10",
            "start_time": "08:00:00",
        },
    )
    assert r_pg.status_code == 200
    assert Decimal(r_pg.json()["total_price"]) == Decimal("22000.00")
    assert r_pg.json()["is_available_for_requested_window"] is False

    # Confirmed booking conflict detected on 2026-12-12 09:00
    r_conflict = client.post(
        f"{PUBLIC_BASE}/{kandy.id}/price-preview",
        json={
            "rate_type": "package",
            "package_id": pkg_half.id,
            "number_of_people": 2,
            "booking_date": "2026-12-12",
            "start_time": "10:00:00",
        },
    )
    assert r_conflict.status_code == 200
    assert r_conflict.json()["is_available_for_requested_window"] is False

    # Group size exceeding package max_people (6) -> 422
    assert client.post(
        f"{PUBLIC_BASE}/{kandy.id}/price-preview",
        json={"rate_type": "package", "package_id": pkg_half.id, "number_of_people": 7},
    ).status_code == 422

    # Cross-guide package rejected -> 404
    assert client.post(
        f"{PUBLIC_BASE}/{kandy.id}/price-preview",
        json={"rate_type": "package", "package_id": pkg_galle.id, "number_of_people": 2},
    ).status_code == 404

    # Hourly rate calculation: 2500 * 3.5 = 8750.00
    r_hr = client.post(
        f"{PUBLIC_BASE}/{kandy.id}/price-preview",
        json={"rate_type": "hourly", "duration_hours": "3.5", "number_of_people": 4},
    )
    assert r_hr.status_code == 200
    assert Decimal(r_hr.json()["total_price"]) == Decimal("8750.00")

    # Guide without hourly_rate returns 400
    assert client.post(
        f"{PUBLIC_BASE}/{galle.id}/price-preview",
        json={"rate_type": "hourly", "duration_hours": "2.0", "number_of_people": 2},
    ).status_code == 400

    # Daily rate calculation: 15000 * 3 = 45000.00
    r_day = client.post(
        f"{PUBLIC_BASE}/{kandy.id}/price-preview",
        json={"rate_type": "daily", "duration_days": 3, "number_of_people": 4},
    )
    assert r_day.status_code == 200
    assert Decimal(r_day.json()["total_price"]) == Decimal("45000.00")

    # Extra client-supplied price field forbidden -> 422
    assert client.post(
        f"{PUBLIC_BASE}/{kandy.id}/price-preview",
        json={
            "rate_type": "daily",
            "duration_days": 1,
            "number_of_people": 2,
            "total_price": "1.00",
        },
    ).status_code == 422


# =============================================================================
# 8. PUBLIC CONTACT PRIVACY & AUTHENTICATION CONTROLS (TR-18)
# =============================================================================

def test_public_contact_privacy_masking_and_token_validation(client, catalog_data):
    kandy = catalog_data["g_kandy_verified"]
    traveler = catalog_data["traveler"]
    inactive_traveler = catalog_data["inactive_traveler"]

    # Anonymous visitor gets masked phone & no secondary phone
    anon = client.get(f"{PUBLIC_BASE}/{kandy.id}/contact")
    assert anon.status_code == 200
    anon_body = anon.json()
    assert anon_body["is_authenticated_view"] is False
    assert anon_body["requires_authentication_for_direct_contact"] is True
    assert anon_body["phone"] == "077****567"
    assert anon_body["secondary_phone"] is None
    assert "0771234567" not in anon.text
    assert "guide1.p2@example.com" not in anon.text

    # Authenticated traveler gets approved direct business phones (still no email/NIC)
    auth_res = client.get(
        f"{PUBLIC_BASE}/{kandy.id}/contact", headers=bearer(traveler)
    )
    assert auth_res.status_code == 200
    auth_body = auth_res.json()
    assert auth_body["is_authenticated_view"] is True
    assert auth_body["requires_authentication_for_direct_contact"] is False
    assert auth_body["phone"] == "0771234567"
    assert auth_body["secondary_phone"] == "0712223334"
    assert auth_body["is_verified"] is True
    assert "guide1.p2@example.com" not in auth_res.text
    assert "199012345601" not in auth_res.text

    # Invalid / expired / inactive tokens rejected strictly
    assert client.get(
        f"{PUBLIC_BASE}/{kandy.id}/contact",
        headers={"Authorization": "Bearer invalid-token"},
    ).status_code == 401
    assert client.get(
        f"{PUBLIC_BASE}/{kandy.id}/contact",
        headers=bearer(traveler, expires_delta=timedelta(minutes=-1)),
    ).status_code == 401
    assert client.get(
        f"{PUBLIC_BASE}/{kandy.id}/contact",
        headers=bearer(inactive_traveler),
    ).status_code == 403
