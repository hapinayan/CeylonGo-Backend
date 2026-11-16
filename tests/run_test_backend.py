"""
CeylonGo M05 – Proposal B: Standalone Ephemeral Test Backend Launcher (Port 8001).
Completely isolated from CeylonGoDB. Uses SQLite in tests/temp_provider_test.db.
Usage:
    cd CeylonGo-Backend
    .venv312\\Scripts\\python tests/run_test_backend.py
"""

from datetime import UTC, datetime, timedelta
import os
from pathlib import Path
import sys

# Ensure backend root is on sys.path
backend_root = Path(__file__).resolve().parent.parent
if str(backend_root) not in sys.path:
    sys.path.insert(0, str(backend_root))

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
import uvicorn

# 1. Critical Safety Block: Prevent any connection to SQL Server CeylonGoDB
import app.database.connection as connection

def block_sql_server(*args, **kwargs):
    raise RuntimeError("CRITICAL SAFETY BLOCK: Direct connection to SQL Server CeylonGoDB is forbidden!")

connection.engine.connect = block_sql_server

# 2. Configure isolated SQLite test database
DB_PATH = Path(__file__).resolve().parent / "temp_provider_test.db"
if DB_PATH.exists():
    try:
        os.remove(DB_PATH)
    except Exception:
        pass

SQLITE_URL = f"sqlite:///{DB_PATH.as_posix()}"
test_engine = create_engine(SQLITE_URL, connect_args={"check_same_thread": False})

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

# 3. Create all tables in isolated SQLite database
from app.core.security import hash_password
from app.main import app
from app.models.artisan_listing import ArtisanListing
from app.models.promotion import Promotion
from app.models.provider_profile import ProviderProfile
from app.models.showcase_item import ShowcaseItem
from app.models.user import Base, User

Base.metadata.create_all(bind=test_engine)

# 4. Override FastAPI get_db dependency
def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()

app.dependency_overrides[connection.get_db] = override_get_db

# 5. Seed isolated test data
def seed_test_data():
    db = TestingSessionLocal()
    try:
        # Artisan 1: Approved Artisan Nimal
        nimal = User(
            full_name="Master Artisan Nimal",
            email="artisan.nimal@example.com",
            password_hash=hash_password("ArtisanTest123!"),
            role="provider",
            is_active=True,
        )
        db.add(nimal)
        db.commit()
        db.refresh(nimal)

        nimal_profile = ProviderProfile(
            user_id=nimal.id,
            business_name="Nimal Woodcraft & Mask Studio",
            provider_type="artisan",
            phone="+94771234567",
            district="Galle",
            description="Authentic traditional Ambalangoda mask carving.",
            approval_status="approved",
        )
        db.add(nimal_profile)
        db.commit()
        db.refresh(nimal_profile)

        # Traveler: Bob
        bob = User(
            full_name="Traveler Bob",
            email="traveler.bob@example.com",
            password_hash=hash_password("TravelerTest123!"),
            role="traveler",
            is_active=True,
        )
        db.add(bob)
        db.commit()

        # Seed initial Artisan Listing for Nimal
        listing = ArtisanListing(
            provider_profile_id=nimal_profile.id,
            title="Nimal Woodcraft Heritage",
            craft_category="Wood & Mask Carving",
            district="Galle",
            city="Ambalangoda",
            description="Generations of traditional mask carving masters.",
            phone="+94771234567",
            email="nimal@example.com",
            is_active=True,
            is_verified=True,
            is_featured=True,
        )
        db.add(listing)
        db.commit()
        db.refresh(listing)

        # Seed initial Showcase Item for Nimal
        showcase = ShowcaseItem(
            artisan_listing_id=listing.id,
            title="Hand-carved Peacock Mask",
            item_type="product",
            price=8500,
            currency="LKR",
            description="Traditional Mayura mask carved from Kaduru wood.",
            materials="Kaduru Wood, Natural Pigments",
            is_available=True,
            is_featured=True,
            is_active=True,
        )
        db.add(showcase)
        db.commit()
        db.refresh(showcase)

        # Seed initial Promotion for Nimal
        now = datetime.now(UTC)
        promo = Promotion(
            artisan_listing_id=listing.id,
            showcase_item_id=showcase.id,
            title="Ambalangoda Cultural Week 15% Off",
            promo_code="PEACOCK15",
            discount_percentage=15,
            start_date=now - timedelta(days=1),
            end_date=now + timedelta(days=14),
            is_active=True,
        )
        db.add(promo)
        db.commit()
        db.refresh(promo)

        print("-" * 65)
        print("EPHEMERAL TEST BACKEND READY")
        print("-" * 65)
        print("Listening on: http://127.0.0.1:8001")
        print("Database: Isolated SQLite (tests/temp_provider_test.db)")
        print("Safety: Physical block active against SQL Server CeylonGoDB")
        print("\nPre-seeded Test Credentials:")
        print("  1. Approved Artisan Provider:")
        print("     Email:    artisan.nimal@example.com")
        print("     Password: ArtisanTest123!")
        print("  2. Traveler Account:")
        print("     Email:    traveler.bob@example.com")
        print("     Password: TravelerTest123!")
        print("-" * 65)
    finally:
        db.close()

if __name__ == "__main__":
    seed_test_data()
    try:
        uvicorn.run(app, host="127.0.0.1", port=8001, log_level="info")
    finally:
        if DB_PATH.exists():
            try:
                os.remove(DB_PATH)
                print("\n[INFO] Temporary SQLite database cleaned up.")
            except Exception:
                pass
