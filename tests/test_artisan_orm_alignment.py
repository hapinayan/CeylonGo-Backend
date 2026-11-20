"""
Unit and integration tests for Artisan SQLAlchemy ORM Model Alignment (Phase P0 - Step 1).
Verifies that ArtisanReservation, ArtisanBusinessImage, ArtisanProductImage, and ShowcaseItem
mappings match the Master Database Documentation V1.0 schema specification exactly.
"""

from datetime import UTC, date, datetime, time
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, event, inspect
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import hash_password
from app.models.artisan import (
    ArtisanBusinessHours,
    ArtisanBusinessImage,
    ArtisanProductImage,
    ArtisanReservation,
)
from app.models.artisan_listing import ArtisanListing
from app.models.provider_profile import ProviderProfile
from app.models.showcase_item import ShowcaseItem
from app.models.user import Base, User

# In-memory SQLite engine for model tests
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
# Model Column & Table Schema Verification Tests
# ==============================================================================

def test_artisan_reservations_schema_columns():
    """Verify artisan_reservations table column names, types, and nullability match Master DB Doc Page 79."""
    mapper = inspect(ArtisanReservation)
    columns = {col.key: col for col in mapper.columns}

    # Verify exact column existence
    expected_columns = {
        "id",
        "showcase_item_id",
        "artisan_listing_id",
        "traveler_id",
        "quantity",
        "total_price",
        "currency",
        "reservation_status",
        "pickup_date",
        "special_notes",
        "expires_at",
        "cancelled_by",
        "cancellation_reason",
        "rejected_by",
        "rejected_at",
        "rejection_reason",
        "created_at",
        "updated_at",
    }
    assert set(columns.keys()) == expected_columns, (
        f"Column mismatch. Expected: {expected_columns}, Actual: {set(columns.keys())}"
    )

    # Verify critical nullability and types
    assert columns["showcase_item_id"].nullable is False
    assert columns["artisan_listing_id"].nullable is False
    assert columns["traveler_id"].nullable is False
    assert columns["quantity"].nullable is False
    assert columns["total_price"].nullable is False
    assert columns["currency"].nullable is False
    assert columns["reservation_status"].nullable is False
    assert columns["pickup_date"].nullable is True
    assert columns["cancelled_by"].nullable is True
    assert columns["special_notes"].nullable is True

    # Verify no phantom columns remain
    assert "reservation_code" not in columns
    assert "reservation_type" not in columns
    assert "time_slot" not in columns
    assert "cancelled_at" not in columns
    assert "user_id" not in columns
    assert "total_amount" not in columns


def test_showcase_item_schema_columns():
    """Verify showcase_items table has is_custom_order_available and all 19 documented columns."""
    mapper = inspect(ShowcaseItem)
    columns = {col.key: col for col in mapper.columns}

    expected_columns = {
        "id",
        "artisan_listing_id",
        "title",
        "item_type",
        "description",
        "price",
        "currency",
        "image_url",
        "materials",
        "duration_minutes",
        "is_available",
        "is_featured",
        "is_active",
        "stock_quantity",
        "sku",
        "is_custom_order_available",
        "approval_status",
        "created_at",
        "updated_at",
    }
    assert set(columns.keys()) == expected_columns
    assert columns["is_custom_order_available"].nullable is False


def test_artisan_business_images_schema_columns():
    """Verify artisan_business_images table has is_primary, caption, and matches Master DB Doc Page 75."""
    mapper = inspect(ArtisanBusinessImage)
    columns = {col.key: col for col in mapper.columns}

    expected_columns = {
        "id",
        "artisan_listing_id",
        "image_url",
        "caption",
        "display_order",
        "is_primary",
        "created_at",
    }
    assert set(columns.keys()) == expected_columns
    assert columns["is_primary"].nullable is False
    assert columns["caption"].nullable is True


def test_artisan_product_images_schema_columns():
    """Verify artisan_product_images table has caption, is_primary, and matches Master DB Doc Page 78."""
    mapper = inspect(ArtisanProductImage)
    columns = {col.key: col for col in mapper.columns}

    expected_columns = {
        "id",
        "showcase_item_id",
        "image_url",
        "caption",
        "display_order",
        "is_primary",
        "created_at",
    }
    assert set(columns.keys()) == expected_columns
    assert columns["is_primary"].nullable is False
    assert columns["caption"].nullable is True


def test_artisan_business_hours_schema_columns():
    """Verify artisan_business_hours table matches Master DB Doc Page 74."""
    mapper = inspect(ArtisanBusinessHours)
    columns = {col.key: col for col in mapper.columns}

    expected_columns = {
        "id",
        "artisan_listing_id",
        "day_of_week",
        "open_time",
        "close_time",
        "is_closed",
        "created_at",
    }
    assert set(columns.keys()) == expected_columns


# ==============================================================================
# Model Lifecycle & Relationship Navigation Tests
# ==============================================================================

def test_artisan_reservation_crud_and_relationships(db_session):
    """Test full ArtisanReservation entity lifecycle, relationships, and property aliases."""
    # 1. Create traveler user
    traveler = User(
        full_name="Traveler Kasun",
        email="kasun.traveler@example.com",
        password_hash=hash_password("Password123!"),
        role="traveler",
        is_active=True,
    )
    # 2. Create provider user and profile
    provider = User(
        full_name="Artisan Nimal",
        email="nimal.woodcraft@example.com",
        password_hash=hash_password("Password123!"),
        role="provider",
        is_active=True,
    )
    db_session.add_all([traveler, provider])
    db_session.commit()

    profile = ProviderProfile(
        user_id=provider.id,
        business_name="Nimal Woodcraft Workshop",
        provider_type="artisan",
        phone="+94771234567",
        district="Kandy",
        approval_status="approved",
    )
    db_session.add(profile)
    db_session.commit()

    # 3. Create listing
    listing = ArtisanListing(
        provider_profile_id=profile.id,
        title="Kandyan Traditional Masks",
        craft_category="Woodcarving & Masks",
        district="Kandy",
        city="Pilimathalawa",
        is_active=True,
    )
    db_session.add(listing)
    db_session.commit()

    # 4. Create showcase item with custom order flag
    item = ShowcaseItem(
        artisan_listing_id=listing.id,
        title="Hand-carved Raksha Mask",
        item_type="product",
        price=Decimal("8500.00"),
        currency="LKR",
        stock_quantity=5,
        sku="MASK-RAK-01",
        is_custom_order_available=True,
        is_available=True,
        is_active=True,
    )
    db_session.add(item)
    db_session.commit()

    # 5. Create reservation
    res = ArtisanReservation(
        showcase_item_id=item.id,
        artisan_listing_id=listing.id,
        traveler_id=traveler.id,
        quantity=2,
        total_price=Decimal("17000.00"),
        currency="LKR",
        reservation_status="pending",
        pickup_date=date(2026, 12, 1),
        special_notes="Will visit between 10am-12pm.",
    )
    db_session.add(res)
    db_session.commit()
    db_session.refresh(res)

    # Verify primary attributes
    assert res.id is not None
    assert res.traveler_id == traveler.id
    assert res.total_price == Decimal("17000.00")
    assert res.reservation_status == "pending"
    assert res.pickup_date == date(2026, 12, 1)
    assert res.special_notes == "Will visit between 10am-12pm."

    # Verify relationship navigation
    assert res.traveler.full_name == "Traveler Kasun"
    assert res.artisan_listing.title == "Kandyan Traditional Masks"
    assert res.showcase_item.title == "Hand-carved Raksha Mask"

    # Verify backrefs
    assert len(listing.reservations) == 1
    assert listing.reservations[0].id == res.id
    assert len(item.reservations) == 1
    assert item.reservations[0].id == res.id
    assert len(traveler.artisan_reservations) == 1
    assert traveler.artisan_reservations[0].id == res.id

    # Verify backward-compatible property aliases
    assert res.user.id == traveler.id
    assert res.user_id == traveler.id
    assert res.status == "pending"
    assert res.total_amount == Decimal("17000.00")
    assert res.reservation_date == date(2026, 12, 1)

    # Test status update via property setter
    res.status = "confirmed"
    assert res.reservation_status == "confirmed"

    # Test cancellation assignment
    res.reservation_status = "cancelled"
    res.cancelled_by = traveler.id
    res.cancellation_reason = "Change of travel itinerary."
    db_session.commit()
    db_session.refresh(res)

    assert res.canceller.id == traveler.id
    assert res.cancellation_reason == "Change of travel itinerary."


def test_business_and_product_image_crud_and_aliases(db_session):
    """Test ArtisanBusinessImage and ArtisanProductImage creation, caption, is_primary, and is_cover alias."""
    provider = User(
        full_name="Artisan Kamala",
        email="kamala.batik@example.com",
        password_hash=hash_password("Password123!"),
        role="provider",
        is_active=True,
    )
    db_session.add(provider)
    db_session.commit()

    profile = ProviderProfile(
        user_id=provider.id,
        business_name="Kamala Batik Art",
        provider_type="artisan",
        phone="+94774445566",
        district="Galle",
        approval_status="approved",
    )
    db_session.add(profile)
    db_session.commit()

    listing = ArtisanListing(
        provider_profile_id=profile.id,
        title="Kamala Galle Batik Gallery",
        craft_category="Batik & Textiles",
        district="Galle",
        city="Galle Fort",
        is_active=True,
    )
    db_session.add(listing)
    db_session.commit()

    item = ShowcaseItem(
        artisan_listing_id=listing.id,
        title="Silk Batik Wall Hanging",
        item_type="product",
        price=Decimal("12000.00"),
        is_custom_order_available=False,
        is_available=True,
        is_active=True,
    )
    db_session.add(item)
    db_session.commit()

    # 1. Test Business Image
    biz_img = ArtisanBusinessImage(
        artisan_listing_id=listing.id,
        image_url="https://images.example.com/workshops/kamala-studio.jpg",
        caption="Workshop showroom in Galle Fort",
        display_order=1,
        is_primary=True,
    )
    db_session.add(biz_img)
    db_session.commit()
    db_session.refresh(biz_img)

    assert biz_img.is_primary is True
    assert biz_img.is_cover is True  # Alias getter
    assert biz_img.caption == "Workshop showroom in Galle Fort"
    assert len(listing.business_images) == 1

    # Test alias setter
    biz_img.is_cover = False
    assert biz_img.is_primary is False

    # 2. Test Product Image
    prod_img = ArtisanProductImage(
        showcase_item_id=item.id,
        image_url="https://images.example.com/products/batik-detail.jpg",
        caption="Close-up detail of traditional wax dye patterns",
        display_order=1,
        is_primary=True,
    )
    db_session.add(prod_img)
    db_session.commit()
    db_session.refresh(prod_img)

    assert prod_img.is_primary is True
    assert prod_img.caption == "Close-up detail of traditional wax dye patterns"
    assert len(item.product_images) == 1
    assert item.product_images[0].id == prod_img.id


def test_business_hours_crud_and_unique_constraint(db_session):
    """Test ArtisanBusinessHours CRUD and day_of_week mapping."""
    provider = User(
        full_name="Artisan Silva",
        email="silva.pottery@example.com",
        password_hash=hash_password("Password123!"),
        role="provider",
        is_active=True,
    )
    db_session.add(provider)
    db_session.commit()

    profile = ProviderProfile(
        user_id=provider.id,
        business_name="Silva Pottery Center",
        provider_type="artisan",
        phone="+94775556677",
        district="Kalutara",
        approval_status="approved",
    )
    db_session.add(profile)
    db_session.commit()

    listing = ArtisanListing(
        provider_profile_id=profile.id,
        title="Silva Clay & Terracotta Works",
        craft_category="Pottery & Ceramics",
        district="Kalutara",
        city="Mawgama",
        is_active=True,
    )
    db_session.add(listing)
    db_session.commit()

    hours = ArtisanBusinessHours(
        artisan_listing_id=listing.id,
        day_of_week=1,  # Monday
        open_time=time(9, 0),
        close_time=time(17, 30),
        is_closed=False,
    )
    db_session.add(hours)
    db_session.commit()
    db_session.refresh(hours)

    assert hours.id is not None
    assert hours.day_of_week == 1
    assert hours.open_time == time(9, 0)
    assert hours.close_time == time(17, 30)
    assert hours.is_closed is False
    assert len(listing.business_hours) == 1
