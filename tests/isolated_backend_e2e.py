"""
CeylonGo M05 – Proposal B: Live HTTP End-to-End Test Runner for Ephemeral Test Backend (Port 8001).
Completely isolated from CeylonGoDB. Uses SQLite in tests/temp_provider_test.db.
"""

import sys
from pathlib import Path

# Add backend root to Python module search path
backend_root = Path(__file__).resolve().parent.parent
if str(backend_root) not in sys.path:
    sys.path.insert(0, str(backend_root))

from datetime import UTC, datetime, timedelta
import os
import threading
import time
import httpx
import uvicorn
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# 1. Enforce physical safety: Patch connection.engine to ensure NO SQL Server access is possible
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
from app.models.user import Base, User
from app.models.provider_profile import ProviderProfile
from app.models.artisan_listing import ArtisanListing
from app.models.showcase_item import ShowcaseItem
from app.models.promotion import Promotion
from app.core.security import hash_password
from app.main import app

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

        # Artisan 2: Approved Artisan Kamala (for cross-provider isolation checks)
        kamala = User(
            full_name="Artisan Kamala",
            email="artisan.kamala@example.com",
            password_hash=hash_password("ArtisanTest123!"),
            role="provider",
            is_active=True,
        )
        db.add(kamala)
        db.commit()
        db.refresh(kamala)

        kamala_profile = ProviderProfile(
            user_id=kamala.id,
            business_name="Kamala Batik Art",
            provider_type="artisan",
            phone="+94779876543",
            district="Kandy",
            description="Traditional Kandyan hand-painted batik.",
            approval_status="approved",
        )
        db.add(kamala_profile)
        db.commit()
        db.refresh(kamala_profile)

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

        print("[INFO] Test database seeded successfully.")
        return listing.id, showcase.id, promo.id
    finally:
        db.close()

# 6. Run live Uvicorn server in a separate daemon thread on Port 8001
class ServerThread(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        config = uvicorn.Config(app=app, host="127.0.0.1", port=8001, log_level="warning")
        self.server = uvicorn.Server(config)

    def run(self):
        self.server.run()

    def stop(self):
        self.server.should_exit = True

def main():
    print("=" * 70)
    print("CeylonGo M05 – Proposal B: Isolated Test Backend Verification (Port 8001)")
    print("=" * 70)

    initial_listing_id, initial_showcase_id, initial_promo_id = seed_test_data()

    server_thread = ServerThread()
    server_thread.start()

    # Wait for server on port 8001 to respond
    client = httpx.Client(base_url="http://127.0.0.1:8001", timeout=10.0)
    print("[WAIT] Connecting to temporary backend on http://127.0.0.1:8001...")
    for _ in range(30):
        try:
            resp = client.get("/api/v1/artisan-listings")
            if resp.status_code == 200:
                print("[READY] Temporary backend is up and running on port 8001!")
                break
        except Exception:
            time.sleep(0.2)
    else:
        print("[ERROR] Timed out waiting for test server on port 8001.")
        server_thread.stop()
        return 1

    results = []

    def log_result(test_name, passed, details=""):
        status_str = "PASS" if passed else "FAIL"
        results.append((test_name, passed, details))
        print(f"[{status_str}] {test_name}: {details}")

    try:
        # ----------------------------------------------------------------------
        # Test 1: Real Authentication & JWT Generation
        # ----------------------------------------------------------------------
        # 1a. Login as Nimal (Approved Artisan Provider)
        nimal_login = client.post("/api/v1/auth/login", json={
            "email": "artisan.nimal@example.com",
            "password": "ArtisanTest123!",
        })
        nimal_token = nimal_login.json().get("access_token") if nimal_login.status_code == 200 else None
        nimal_headers = {"Authorization": f"Bearer {nimal_token}"}
        log_result("Auth: Login Approved Artisan", nimal_login.status_code == 200 and bool(nimal_token), f"Status {nimal_login.status_code}")

        # 1b. Login as Kamala (Second Approved Artisan)
        kamala_login = client.post("/api/v1/auth/login", json={
            "email": "artisan.kamala@example.com",
            "password": "ArtisanTest123!",
        })
        kamala_token = kamala_login.json().get("access_token") if kamala_login.status_code == 200 else None
        kamala_headers = {"Authorization": f"Bearer {kamala_token}"}
        log_result("Auth: Login Second Artisan", kamala_login.status_code == 200 and bool(kamala_token), f"Status {kamala_login.status_code}")

        # 1c. Login as Bob (Traveler)
        bob_login = client.post("/api/v1/auth/login", json={
            "email": "traveler.bob@example.com",
            "password": "TravelerTest123!",
        })
        bob_token = bob_login.json().get("access_token") if bob_login.status_code == 200 else None
        bob_headers = {"Authorization": f"Bearer {bob_token}"}
        log_result("Auth: Login Traveler", bob_login.status_code == 200 and bool(bob_token), f"Status {bob_login.status_code}")

        # ----------------------------------------------------------------------
        # Test 2: M05.1 Artisan Listings CRUD
        # ----------------------------------------------------------------------
        # 2a. READ: Get authenticated provider's listings
        my_listings = client.get("/api/v1/artisan-listings/me/listings", headers=nimal_headers)
        log_result("M05.1 Read: Get My Listings", my_listings.status_code == 200 and len(my_listings.json()) >= 1, f"Found {len(my_listings.json())} listings")

        # 2b. CREATE: Create new artisan listing
        create_listing = client.post("/api/v1/artisan-listings", headers=nimal_headers, json={
            "title": "Nimal Traditional Masks Workshop",
            "craft_category": "Wood & Mask Carving",
            "district": "Galle",
            "city": "Ambalangoda",
            "description": "Authentic live demonstration of mask carving.",
            "phone": "+94771234567",
        })
        created_listing_id = create_listing.json().get("id") if create_listing.status_code == 201 else None
        log_result("M05.1 Create: New Artisan Listing", create_listing.status_code == 201 and created_listing_id is not None, f"Created ID {created_listing_id}")

        # 2c. UPDATE: Update listing
        update_listing = client.put(f"/api/v1/artisan-listings/{created_listing_id}", headers=nimal_headers, json={
            "title": "Nimal Traditional Masks Workshop (Updated)",
            "city": "Ambalangoda Coastal Town",
        })
        log_result("M05.1 Update: Modify Listing", update_listing.status_code == 200 and update_listing.json().get("title") == "Nimal Traditional Masks Workshop (Updated)", f"Status {update_listing.status_code}")

        # 2d. SOFT DELETE: Deactivate listing
        delete_listing = client.delete(f"/api/v1/artisan-listings/{created_listing_id}", headers=nimal_headers)
        log_result("M05.1 Delete: Soft-delete Listing", delete_listing.status_code == 200, f"Status {delete_listing.status_code}")

        # 2e. VERIFY INACTIVE: Public endpoint excludes deactivated listing
        public_check = client.get(f"/api/v1/artisan-listings/{created_listing_id}")
        log_result("M05.1 Verify: Deactivated Listing Inactive", public_check.status_code == 404, f"Public status {public_check.status_code} (Not Found)")

        # ----------------------------------------------------------------------
        # Test 3: M05.2 Showcase Items CRUD
        # ----------------------------------------------------------------------
        # 3a. READ: Get showcase items for listing
        get_showcase = client.get(f"/api/v1/artisan-listings/{initial_listing_id}/showcase-items")
        log_result("M05.2 Read: Get Showcase Items", get_showcase.status_code == 200 and get_showcase.json().get("total") >= 1, f"Found {get_showcase.json().get('total')} items")

        # 3b. CREATE: Create new showcase service/workshop
        create_showcase = client.post("/api/v1/showcase-items", headers=nimal_headers, json={
            "artisan_listing_id": initial_listing_id,
            "title": "Traditional Mask Carving Workshop (2 Hours)",
            "item_type": "service",
            "price": 4500,
            "currency": "LKR",
            "description": "Hands-on guided workshop where travelers carve and paint their own mini mask.",
            "duration_minutes": 120,
            "materials": "Kaduru wood blocks, gouges, paints",
            "is_available": True,
        })
        created_showcase_id = create_showcase.json().get("id") if create_showcase.status_code == 201 else None
        log_result("M05.2 Create: New Showcase Service", create_showcase.status_code == 201 and created_showcase_id is not None, f"Created ID {created_showcase_id}")

        # 3c. UPDATE: Update showcase item
        update_showcase = client.put(f"/api/v1/showcase-items/{created_showcase_id}", headers=nimal_headers, json={
            "price": 5000,
            "duration_minutes": 150,
        })
        log_result("M05.2 Update: Modify Showcase Item", update_showcase.status_code == 200 and float(update_showcase.json().get("price")) == 5000.0, f"Status {update_showcase.status_code}")

        # 3d. SOFT DELETE: Deactivate showcase item
        delete_showcase = client.delete(f"/api/v1/showcase-items/{created_showcase_id}", headers=nimal_headers)
        log_result("M05.2 Delete: Soft-delete Showcase Item", delete_showcase.status_code == 200, f"Status {delete_showcase.status_code}")

        # 3e. VERIFY INACTIVE: Public endpoint returns 404 for deactivated item
        public_showcase_check = client.get(f"/api/v1/showcase-items/{created_showcase_id}")
        log_result("M05.2 Verify: Deactivated Item Inactive", public_showcase_check.status_code == 404, f"Public status {public_showcase_check.status_code} (Not Found)")

        # ----------------------------------------------------------------------
        # Test 4: M05.3 Promotions CRUD
        # ----------------------------------------------------------------------
        # 4a. READ: Get active promotions
        get_promos = client.get(f"/api/v1/promotions?artisan_listing_id={initial_listing_id}")
        log_result("M05.3 Read: Get Active Promotions", get_promos.status_code == 200 and get_promos.json().get("total") >= 1, f"Found {get_promos.json().get('total')} promotions")

        # 4b. CREATE: Create new fixed-amount promotion
        now_dt = datetime.now(UTC)
        create_promo = client.post("/api/v1/promotions", headers=nimal_headers, json={
            "artisan_listing_id": initial_listing_id,
            "title": "LKR 1000 Off Seasonal Craft Sale",
            "promo_code": "CRAFT1000",
            "discount_amount": 1000,
            "discount_percentage": None,
            "start_date": now_dt.isoformat(),
            "end_date": (now_dt + timedelta(days=30)).isoformat(),
        })
        created_promo_id = create_promo.json().get("id") if create_promo.status_code == 201 else None
        log_result("M05.3 Create: New Fixed-Amount Promo", create_promo.status_code == 201 and created_promo_id is not None, f"Created ID {created_promo_id}")

        # 4c. UPDATE: Update promotion
        update_promo = client.put(f"/api/v1/promotions/{created_promo_id}", headers=nimal_headers, json={
            "title": "LKR 1500 Off Special Festival Sale",
            "discount_amount": 1500,
        })
        log_result("M05.3 Update: Modify Promotion", update_promo.status_code == 200 and float(update_promo.json().get("discount_amount")) == 1500.0, f"Status {update_promo.status_code}")

        # 4d. SOFT DELETE: Deactivate promotion
        delete_promo = client.delete(f"/api/v1/promotions/{created_promo_id}", headers=nimal_headers)
        log_result("M05.3 Delete: Soft-delete Promotion", delete_promo.status_code == 200, f"Status {delete_promo.status_code}")

        # ----------------------------------------------------------------------
        # Test 5: Role-Based Authorization & Cross-Tenant Isolation
        # ----------------------------------------------------------------------
        # 5a. Traveler cannot create artisan listing (403 Forbidden)
        traveler_listing = client.post("/api/v1/artisan-listings", headers=bob_headers, json={
            "title": "Illicit Traveler Listing",
            "craft_category": "Wood & Mask Carving",
            "district": "Galle",
            "city": "Ambalangoda",
        })
        log_result("Security: Traveler Blocked from Listing Create", traveler_listing.status_code == 403, f"Status {traveler_listing.status_code}")

        # 5b. Traveler cannot create showcase item (403 Forbidden)
        traveler_showcase = client.post("/api/v1/showcase-items", headers=bob_headers, json={
            "artisan_listing_id": initial_listing_id,
            "title": "Traveler Showcase Item",
            "item_type": "product",
            "price": 1000,
        })
        log_result("Security: Traveler Blocked from Showcase Create", traveler_showcase.status_code == 403, f"Status {traveler_showcase.status_code}")

        # 5c. Traveler cannot create promotion (403 Forbidden)
        traveler_promo = client.post("/api/v1/promotions", headers=bob_headers, json={
            "artisan_listing_id": initial_listing_id,
            "title": "Traveler Promo",
            "discount_percentage": 20,
            "start_date": now_dt.isoformat(),
            "end_date": (now_dt + timedelta(days=7)).isoformat(),
        })
        log_result("Security: Traveler Blocked from Promotion Create", traveler_promo.status_code == 403, f"Status {traveler_promo.status_code}")

        # 5d. Cross-Provider Isolation: Kamala cannot modify Nimal's listing (403 Forbidden)
        kamala_update_listing = client.put(f"/api/v1/artisan-listings/{initial_listing_id}", headers=kamala_headers, json={
            "title": "Hijacked Listing",
        })
        log_result("Security: Cross-Provider Listing Update Forbidden", kamala_update_listing.status_code == 403, f"Status {kamala_update_listing.status_code}")

        # 5e. Cross-Provider Isolation: Kamala cannot delete Nimal's listing (403 Forbidden)
        kamala_delete_listing = client.delete(f"/api/v1/artisan-listings/{initial_listing_id}", headers=kamala_headers)
        log_result("Security: Cross-Provider Listing Delete Forbidden", kamala_delete_listing.status_code == 403, f"Status {kamala_delete_listing.status_code}")

        # 5f. Cross-Provider Isolation: Kamala cannot add showcase item to Nimal's listing (403 Forbidden)
        kamala_add_showcase = client.post("/api/v1/showcase-items", headers=kamala_headers, json={
            "artisan_listing_id": initial_listing_id,
            "title": "Kamala item on Nimal listing",
            "item_type": "product",
            "price": 2000,
        })
        log_result("Security: Cross-Provider Showcase Create Forbidden", kamala_add_showcase.status_code == 403, f"Status {kamala_add_showcase.status_code}")

        # 5g. Unauthenticated access blocked (401 Unauthorized)
        unauth_call = client.post("/api/v1/artisan-listings", json={
            "title": "Unauthenticated Listing",
            "craft_category": "Batik & Textiles",
            "district": "Galle",
            "city": "Galle",
        })
        log_result("Security: Unauthenticated Create Listing Rejected", unauth_call.status_code == 401, f"Status {unauth_call.status_code}")

    finally:
        server_thread.stop()
        client.close()
        # Clean up database
        try:
            if DB_PATH.exists():
                os.remove(DB_PATH)
                print("[INFO] Temporary database file removed.")
        except Exception:
            pass

    # Summary
    passed_count = sum(1 for _, p, _ in results if p)
    failed_count = sum(1 for _, p, _ in results if not p)
    print("=" * 70)
    print(f"VERIFICATION SUMMARY: {passed_count} PASSED, {failed_count} FAILED (Total {len(results)})")
    print("=" * 70)

    return 0 if failed_count == 0 else 1

if __name__ == "__main__":
    import sys
    sys.exit(main())
