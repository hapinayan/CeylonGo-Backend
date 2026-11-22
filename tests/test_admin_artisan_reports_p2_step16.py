"""
Automated tests for Phase P2 Step 16: Admin Basic Reports & Safe CSV Exports.
Verifies:
1. Admin Reports Summary endpoint (/api/v1/admin/artisan/reports/summary) aggregates counts for
   businesses, products, reservations, and promotions without data leakage or fabricated revenue.
2. Safe CSV Export (/api/v1/admin/artisan/reports/export/{type}) generates valid CSV data.
3. Spreadsheet formula injection prevention (prepending apostrophe for cells starting with '=', '+', '-', '@', tab).
4. Strict RBAC: Non-admin users (travelers, providers, unauthenticated) receive HTTP 401/403.
5. No unnecessary personal data disclosure in reservation or business reports.
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
import io
import csv
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import hash_password
from tests.access_tokens import create_fixture_access_token as create_access_token
from app.database.connection import get_db
from app.main import app
from app.models.artisan import ArtisanReservation
from app.models.artisan_listing import ArtisanListing
from app.models.promotion import Promotion
from app.models.provider_profile import ProviderProfile
from app.models.showcase_item import ShowcaseItem
from app.models.user import Base, User
from app.routers.admin_artisan import safe_csv_cell

# In-memory SQLite engine for tests
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


@pytest.fixture
def admin_headers(db_session):
    admin = User(
        full_name="Admin Reports",
        email="admin_reports@ceylongo.com",
        password_hash="mock_hash",
        role="admin",
        is_active=True,
    )
    db_session.add(admin)
    db_session.commit()
    db_session.refresh(admin)
    token = create_access_token(data={"sub": str(admin.id), "role": "admin"})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def traveler_headers(db_session):
    traveler = User(
        full_name="Traveler Reports",
        email="traveler_reports@ceylongo.com",
        password_hash="mock_hash",
        role="traveler",
        is_active=True,
    )
    db_session.add(traveler)
    db_session.commit()
    db_session.refresh(traveler)
    token = create_access_token(data={"sub": str(traveler.id), "role": "traveler"})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def sample_report_data(db_session):
    provider_user = User(
        full_name="Provider Rep",
        email="provider_rep@ceylongo.com",
        password_hash="mock_hash",
        role="service_provider",
        is_active=True,
    )
    db_session.add(provider_user)
    db_session.commit()

    profile = ProviderProfile(
        user_id=provider_user.id,
        business_name="Ceylon Handicrafts Ltd",
        provider_type="artisan",
        phone="+94771234567",
        district="Kandy",
        approval_status="approved",
    )
    db_session.add(profile)
    db_session.commit()

    # Listing 1: Approved, Verified, Featured (contains formula injection attempt)
    listing1 = ArtisanListing(
        provider_profile_id=profile.id,
        title="=cmd|' /C calc'!A0 Woodcarvings",
        craft_category="woodcarving",
        district="Kandy",
        city="Kandy City",
        latitude=Decimal("7.2906"),
        longitude=Decimal("80.6337"),
        phone="+94771234567",
        email="kandy@crafts.lk",
        approval_status="approved",
        is_active=True,
        is_verified=True,
        is_featured=True,
    )
    # Listing 2: Pending
    listing2 = ArtisanListing(
        provider_profile_id=profile.id,
        title="Galle Batik Workshop",
        craft_category="batik",
        district="Galle",
        city="Galle Fort",
        latitude=Decimal("6.0535"),
        longitude=Decimal("80.2210"),
        phone="+94772345678",
        email="galle@batik.lk",
        approval_status="pending",
        is_active=True,
        is_verified=False,
        is_featured=False,
    )
    db_session.add_all([listing1, listing2])
    db_session.commit()

    # Product 1: Approved & Active
    prod1 = ShowcaseItem(
        artisan_listing_id=listing1.id,
        title="Handcarved Elephant",
        item_type="woodcarving",
        price=Decimal("4500.00"),
        stock_quantity=10,
        is_available=True,
        is_active=True,
        approval_status="approved",
    )
    # Product 2: Out of stock
    prod2 = ShowcaseItem(
        artisan_listing_id=listing1.id,
        title="Ebony Mask",
        item_type="masks",
        price=Decimal("7500.00"),
        stock_quantity=0,
        is_available=False,
        is_active=True,
        approval_status="approved",
    )
    db_session.add_all([prod1, prod2])
    db_session.commit()

    # Traveler for reservation
    traveler = User(
        full_name="Res Traveler",
        email="res_traveler@ceylongo.com",
        password_hash="mock_hash",
        role="traveler",
        is_active=True,
    )
    db_session.add(traveler)
    db_session.commit()

    # Reservation 1: Completed (collected LKR 9000.00)
    res1 = ArtisanReservation(
        showcase_item_id=prod1.id,
        artisan_listing_id=listing1.id,
        traveler_id=traveler.id,
        quantity=2,
        total_price=Decimal("9000.00"),
        currency="LKR",
        reservation_status="completed",
        pickup_date=date.today(),
    )
    # Reservation 2: Pending
    res2 = ArtisanReservation(
        showcase_item_id=prod1.id,
        artisan_listing_id=listing1.id,
        traveler_id=traveler.id,
        quantity=1,
        total_price=Decimal("4500.00"),
        currency="LKR",
        reservation_status="pending",
        pickup_date=date.today() + timedelta(days=3),
    )
    db_session.add_all([res1, res2])
    db_session.commit()

    # Promotion
    promo = Promotion(
        artisan_listing_id=listing1.id,
        title="New Year Special",
        promo_code="NY2026",
        discount_percentage=Decimal("15.00"),
        start_date=date.today() - timedelta(days=5),
        end_date=date.today() + timedelta(days=10),
        is_active=True,
    )
    db_session.add(promo)
    db_session.commit()

    return {
        "listing1": listing1,
        "listing2": listing2,
        "prod1": prod1,
        "res1": res1,
        "promo": promo,
    }


def test_safe_csv_cell_formula_injection_prevention():
    """Verify formula injection triggers (=, +, -, @, tab, newline) are escaped with single quote."""
    assert safe_csv_cell("=1+1") == "'=1+1"
    assert safe_csv_cell("+cmd|calc") == "'+cmd|calc"
    assert safe_csv_cell("-2+3") == "'-2+3"
    assert safe_csv_cell("@SUM(A1:A5)") == "'@SUM(A1:A5)"
    assert safe_csv_cell("\tTabbed") == "'\tTabbed"
    assert safe_csv_cell("\rNewline") == "'\rNewline"
    # Normal values remain untouched
    assert safe_csv_cell("Normal Text") == "Normal Text"
    assert safe_csv_cell(123) == "123"
    assert safe_csv_cell(None) == ""


def test_admin_reports_summary(client, admin_headers, sample_report_data):
    """Verify admin reports summary returns correct aggregate KPIs."""
    response = client.get("/api/v1/admin/artisan/reports/summary", headers=admin_headers)
    assert response.status_code == 200
    data = response.json()

    # Businesses: 2 total (1 approved, 1 pending, 1 verified, 1 featured)
    assert data["businesses"]["total_businesses"] == 2
    assert data["businesses"]["approved_count"] == 1
    assert data["businesses"]["pending_count"] == 1
    assert data["businesses"]["verified_count"] == 1
    assert data["businesses"]["featured_count"] == 1

    # Products: 2 total (1 active with stock, 1 out of stock)
    assert data["products"]["total_products"] == 2
    assert data["products"]["active_count"] == 2
    assert data["products"]["out_of_stock_count"] == 1

    # Reservations: 2 total (1 completed, 1 pending, total collected = 9000.00)
    assert data["reservations"]["total_reservations"] == 2
    assert data["reservations"]["completed_count"] == 1
    assert data["reservations"]["pending_count"] == 1
    assert float(data["reservations"]["total_collected_lkr"]) == 9000.00

    # Promotions: 1 active
    assert data["promotions"]["total_promotions"] == 1
    assert data["promotions"]["active_count"] == 1


def test_admin_reports_summary_rbac(client, traveler_headers):
    """Verify non-admin users cannot access reports summary."""
    # Unauthenticated
    resp_unauth = client.get("/api/v1/admin/artisan/reports/summary")
    assert resp_unauth.status_code in (401, 403)

    # Traveler
    resp_traveler = client.get("/api/v1/admin/artisan/reports/summary", headers=traveler_headers)
    assert resp_traveler.status_code == 403


def test_export_businesses_csv(client, admin_headers, sample_report_data):
    """Verify CSV export for businesses sanitizes formula injection and exports valid rows."""
    response = client.get("/api/v1/admin/artisan/reports/export/businesses", headers=admin_headers)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment; filename=" in response.headers["content-disposition"]

    reader = csv.reader(io.StringIO(response.text))
    rows = list(reader)
    assert len(rows) == 3  # Header + 2 businesses
    header = rows[0]
    assert "Business Title" in header
    assert "Approval Status" in header

    # Formula injection test: listing1 title was "=cmd|' /C calc'!A0 Woodcarvings"
    # Must be escaped as "'=cmd|' /C calc'!A0 Woodcarvings"
    titles = [r[1] for r in rows[1:]]
    assert any(t.startswith("'=") for t in titles)


def test_export_reservations_csv_privacy(client, admin_headers, sample_report_data):
    """Verify reservations CSV does not leak traveler passwords, tokens, or hashes."""
    response = client.get("/api/v1/admin/artisan/reports/export/reservations", headers=admin_headers)
    assert response.status_code == 200
    reader = csv.reader(io.StringIO(response.text))
    rows = list(reader)
    assert len(rows) == 3  # Header + 2 reservations
    header = rows[0]

    # Verify header contains safe transactional columns
    assert "Reservation ID" in header
    assert "Total Price (LKR)" in header
    assert "Reservation Status" in header

    # Ensure no passwords or secret tokens exist in the CSV content
    assert "password" not in response.text.lower()
    assert "token" not in response.text.lower()


def test_export_invalid_report_type(client, admin_headers):
    """Verify invalid report type returns HTTP 400."""
    response = client.get("/api/v1/admin/artisan/reports/export/invalid_domain", headers=admin_headers)
    assert response.status_code == 400
    assert "Invalid report type" in response.json()["detail"]
