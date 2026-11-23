"""
CeylonGo Rental Backend - Phase 4 Batch 1 Test Suite
====================================================
Automated isolated unit & API tests verifying Admin Portal requirements AD-02 and AD-03,
with integration verification of AD-01 and AD-04:
- AD-02: Rental Vehicle Listing Approval (pending queue, detailed inspection, approve/reject/suspend, reason enforcement, status guards)
- AD-03: Vehicle Document Verification (pending document queue, document approval/rejection, path traversal security, verification status updates)
- AD-01 & AD-04 Integration: Provider KYC approval & document verification integration
- Audit Logging: All actions logged to dbo.admin_audit_logs
- Traveler Discovery: Non-approved vehicles (pending, rejected, suspended) hidden from discovery
- Tamper Protection: Providers cannot modify admin-controlled approval or verification fields

All database tests execute strictly against an isolated in-memory SQLite engine
with zero modifications to CeylonGoDB in compliance with fail-closed database guards.
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import os
from pathlib import Path
import sqlite3
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database.connection import get_db
from app.models.provider_profile import ProviderProfile
from app.models.rental import (
    RentalLocation,
    RentalRatePlan,
    RentalVehicleDocument,
    RentalVehicleImage,
)
from app.models.rental_vehicle import RentalVehicle
from app.models.shared import (
    AdminAuditLog,
    BookingReference,
    ListingReference,
    Notification,
)
from app.models.user import Base, User
from app.routers.admin_providers import router as admin_providers_router
from app.routers.admin_rentals import router as admin_rentals_router
from app.routers.auth import get_current_user
from app.routers.rental_providers import router as rental_providers_router
from app.routers.rental_vehicles import router as rental_vehicles_router


# ==============================================================================
# Isolated SQLite Database Fixture
# ==============================================================================

@pytest.fixture
def isolated_db_session(tmp_path, monkeypatch):
    """Creates a completely isolated in-memory SQLite database session."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _register_sqlite_functions(dbapi_conn, _):
        if isinstance(dbapi_conn, sqlite3.Connection):
            dbapi_conn.create_function(
                "sysutcdatetime", 0,
                lambda: datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            )

    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    # Set temporary uploads folder
    test_docs_dir = str(tmp_path / "rental_docs")
    os.makedirs(test_docs_dir, exist_ok=True)
    monkeypatch.setenv("RENTAL_DOCUMENTS_DIR", test_docs_dir)

    session = TestingSessionLocal()
    try:
        yield session, test_docs_dir
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


def make_test_client(db, current_user: User | None = None):
    app = FastAPI()
    app.include_router(admin_rentals_router)
    app.include_router(admin_providers_router)
    app.include_router(rental_providers_router)
    app.include_router(rental_vehicles_router)

    app.dependency_overrides[get_db] = lambda: db
    if current_user:
        app.dependency_overrides[get_current_user] = lambda: current_user
    return TestClient(app)


@pytest.fixture
def seed_test_data(isolated_db_session):
    db, docs_dir = isolated_db_session
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # 1. Users
    admin_user = User(id=1, full_name="System Administrator", email="admin@ceylongo.lk", password_hash="pw", role="admin")
    prov_user = User(id=2, full_name="Bandara Perera", email="bandara@rentals.lk", password_hash="pw", role="provider")
    traveler_user = User(id=3, full_name="Charlie Traveler", email="charlie@traveler.com", password_hash="pw", role="traveler")
    db.add_all([admin_user, prov_user, traveler_user])
    db.flush()

    # 2. Provider Profile (Approved rental provider)
    provider = ProviderProfile(
        id=201,
        user_id=2,
        business_name="Bandara Galle Motorbikes",
        provider_type="rental",
        phone="+94771122334",
        district="Galle",
        approval_status="approved",
        selfie_image_path="/uploads/selfie.jpg",
        nic_front_image_path="/uploads/nic_front.jpg",
        nic_back_image_path="/uploads/nic_back.jpg",
        business_registration_number="BR-998877",
    )
    db.add(provider)
    db.flush()

    # 3. Location
    loc = RentalLocation(
        id=1,
        provider_id=201,
        location_name="Galle Fort Center",
        district="Galle",
        city="Galle",
        latitude=Decimal("6.0329"),
        longitude=Decimal("80.2168"),
    )
    db.add(loc)
    db.flush()

    # 4. Vehicles
    # v1: Motorized bike - Pending approval, unverified
    v1_bike_pending = RentalVehicle(
        id=101,
        provider_id=201,
        vehicle_name="Honda Activa 125",
        vehicle_type="bike",
        price_per_day=Decimal("3500.00"),
        status="available",
        approval_status="pending",
        verification_status="unverified",
        model_year=2023,
        license_plate="SP-BCC-1234",
        seating_capacity=2,
        transmission="automatic",
        fuel_type="petrol",
        district="Galle",
        city="Galle",
        location_id=1,
    )

    # v2: Non-motorized bicycle - Pending approval
    v2_bicycle_pending = RentalVehicle(
        id=102,
        provider_id=201,
        vehicle_name="Giant Escape City Bicycle",
        vehicle_type="bicycle",
        price_per_day=Decimal("1200.00"),
        status="available",
        approval_status="pending",
        verification_status="unverified",
        model_year=2024,
        seating_capacity=1,
        transmission="manual",
        fuel_type="manual",
        district="Galle",
        city="Galle",
        location_id=1,
    )

    # v3: Approved Tuk-Tuk
    v3_tuktuk_approved = RentalVehicle(
        id=103,
        provider_id=201,
        vehicle_name="Bajaj Maxima Z",
        vehicle_type="tuk_tuk",
        price_per_day=Decimal("5500.00"),
        status="available",
        approval_status="approved",
        verification_status="verified",
        model_year=2022,
        license_plate="SP-QZ-4321",
        seating_capacity=3,
        transmission="manual",
        fuel_type="petrol",
        district="Galle",
        city="Galle",
        location_id=1,
    )

    # v4: Rejected Bike
    v4_bike_rejected = RentalVehicle(
        id=104,
        provider_id=201,
        vehicle_name="Old Pulsar 150",
        vehicle_type="bike",
        price_per_day=Decimal("2000.00"),
        status="available",
        approval_status="rejected",
        verification_status="rejected",
        rejection_reason="Vehicle older than 10 years and invalid license",
        district="Galle",
        city="Galle",
        location_id=1,
    )
    db.add_all([v1_bike_pending, v2_bicycle_pending, v3_tuktuk_approved, v4_bike_rejected])
    db.flush()

    # Images for v1
    img1 = RentalVehicleImage(vehicle_id=101, image_url="https://images.ceylongo.lk/activa_front.jpg", display_order=0, is_primary=True)
    db.add(img1)

    # Rate plan for v1
    rp1 = RentalRatePlan(
        vehicle_id=101,
        plan_name="Standard Daily",
        duration_type="daily",
        rate=Decimal("3500.00"),
        included_km_per_day=100,
        extra_km_charge=Decimal("40.00"),
        is_active=True,
    )
    db.add(rp1)

    # Documents for v1: revenue_license & insurance_certificate
    # Create sample dummy files in docs_dir
    rev_file = os.path.join(docs_dir, "rev_license_101.pdf")
    with open(rev_file, "w") as f:
        f.write("%PDF-1.4 dummy revenue license")

    ins_file = os.path.join(docs_dir, "insurance_101.pdf")
    with open(ins_file, "w") as f:
        f.write("%PDF-1.4 dummy insurance certificate")

    doc1 = RentalVehicleDocument(
        id=1,
        vehicle_id=101,
        document_type="revenue_license",
        document_number="RL-2026-999",
        file_reference="rev_license_101.pdf",
        expiry_date=date(2027, 5, 31),
        is_verified=False,
    )
    doc2 = RentalVehicleDocument(
        id=2,
        vehicle_id=101,
        document_type="insurance_certificate",
        document_number="INS-POLICY-777",
        file_reference="insurance_101.pdf",
        expiry_date=date(2027, 8, 15),
        is_verified=False,
    )
    db.add_all([doc1, doc2])
    db.commit()

    return {
        "admin_user": admin_user,
        "prov_user": prov_user,
        "traveler_user": traveler_user,
        "provider": provider,
        "v1": v1_bike_pending,
        "v2": v2_bicycle_pending,
        "v3": v3_tuktuk_approved,
        "v4": v4_bike_rejected,
        "doc1": doc1,
        "doc2": doc2,
        "docs_dir": docs_dir,
    }


# ==============================================================================
# RBAC & AUTHORIZATION GUARDS TESTS
# ==============================================================================

def test_admin_authorization_and_unauthorized_access_guards(seed_test_data, isolated_db_session):
    """Verifies that only users with role='admin' can access admin rental endpoints."""
    db, _ = isolated_db_session

    # 1. Admin caller succeeds
    client_admin = make_test_client(db, current_user=seed_test_data["admin_user"])
    resp = client_admin.get("/api/v1/admin/rentals/vehicles")
    assert resp.status_code == 200, resp.text

    # 2. Service provider caller receives 403 Forbidden
    client_provider = make_test_client(db, current_user=seed_test_data["prov_user"])
    assert client_provider.get("/api/v1/admin/rentals/vehicles").status_code == 403
    assert client_provider.get("/api/v1/admin/rentals/documents/pending").status_code == 403

    # 3. Traveler caller receives 403 Forbidden
    client_traveler = make_test_client(db, current_user=seed_test_data["traveler_user"])
    assert client_traveler.get("/api/v1/admin/rentals/vehicles").status_code == 403
    assert client_traveler.get("/api/v1/admin/rentals/documents/pending").status_code == 403

    # 4. Anonymous caller receives 401 Unauthorized
    client_anon = make_test_client(db, current_user=None)
    assert client_anon.get("/api/v1/admin/rentals/vehicles").status_code == 401


# ==============================================================================
# AD-02: VEHICLE LISTING MODERATION TESTS
# ==============================================================================

def test_admin_list_and_pending_vehicle_queues(seed_test_data, isolated_db_session):
    """Verifies platform-wide vehicle listing, filters, and pending moderation queue."""
    db, _ = isolated_db_session
    client = make_test_client(db, current_user=seed_test_data["admin_user"])

    # 1. Platform-wide list returns all 4 seeded vehicles
    resp_all = client.get("/api/v1/admin/rentals/vehicles")
    assert resp_all.status_code == 200, resp_all.text
    data_all = resp_all.json()
    assert data_all["total"] == 4
    assert len(data_all["items"]) == 4

    # 2. Pending queue returns only the 2 pending vehicles (v1 and v2)
    resp_pending = client.get("/api/v1/admin/rentals/vehicles/pending")
    assert resp_pending.status_code == 200, resp_pending.text
    data_pending = resp_pending.json()
    assert data_pending["total"] == 2
    for item in data_pending["items"]:
        assert item["approval_status"] == "pending"

    # 3. Filter by category (tuk_tuk)
    resp_tuktuk = client.get("/api/v1/admin/rentals/vehicles?vehicle_type=tuk_tuk")
    assert resp_tuktuk.status_code == 200
    assert resp_tuktuk.json()["total"] == 1
    assert resp_tuktuk.json()["items"][0]["id"] == 103


def test_admin_vehicle_detail_inspection(seed_test_data, isolated_db_session):
    """Verifies detailed inspection endpoint returning provider info, specs, images, and documents."""
    db, _ = isolated_db_session
    client = make_test_client(db, current_user=seed_test_data["admin_user"])

    resp = client.get("/api/v1/admin/rentals/vehicles/101")
    assert resp.status_code == 200, resp.text
    v = resp.json()

    assert v["id"] == 101
    assert v["vehicle_name"] == "Honda Activa 125"
    assert v["provider_business_name"] == "Bandara Galle Motorbikes"
    assert v["provider_phone"] == "+94771122334"
    assert v["seating_capacity"] == 2
    assert v["fuel_type"] == "petrol"
    assert v["primary_image_url"] == "https://images.ceylongo.lk/activa_front.jpg"
    assert v["images_count"] == 1
    assert v["documents_count"] == 2
    assert v["verified_documents_count"] == 0
    assert v["rate_plans_count"] == 1


def test_motorized_vehicle_approval_blocked_without_verified_documents(seed_test_data, isolated_db_session):
    """Requirement 10: Motorized vehicle cannot be approved until required documents are verified."""
    db, _ = isolated_db_session
    client = make_test_client(db, current_user=seed_test_data["admin_user"])

    # Activa 125 has 2 unverified documents
    resp = client.post("/api/v1/admin/rentals/vehicles/101/review", json={"decision": "approved"})
    assert resp.status_code == 400
    assert "verified revenue_license and insurance_certificate documents are required" in resp.json()["detail"]

    # Vehicle remains pending
    vehicle = db.query(RentalVehicle).filter(RentalVehicle.id == 101).first()
    assert vehicle.approval_status == "pending"


def test_motorized_vehicle_approval_success_with_verified_documents(seed_test_data, isolated_db_session):
    """Approves motorized vehicle once revenue license & insurance are verified, verifying audit & notification."""
    db, _ = isolated_db_session
    client = make_test_client(db, current_user=seed_test_data["admin_user"])

    # 1. Verify the two documents
    doc1 = db.query(RentalVehicleDocument).filter(RentalVehicleDocument.id == 1).first()
    doc2 = db.query(RentalVehicleDocument).filter(RentalVehicleDocument.id == 2).first()
    doc1.is_verified = True
    doc2.is_verified = True
    db.commit()

    # 2. Approve the vehicle listing
    resp = client.post("/api/v1/admin/rentals/vehicles/101/review", json={"decision": "approved"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["new_approval_status"] == "approved"
    assert data["previous_approval_status"] == "pending"

    # 3. Verify database state
    vehicle = db.query(RentalVehicle).filter(RentalVehicle.id == 101).first()
    assert vehicle.approval_status == "approved"
    assert vehicle.reviewed_by == 1
    assert vehicle.reviewed_at is not None
    assert vehicle.rejection_reason is None

    # 4. Verify in-app notification sent to provider
    notif = db.query(Notification).filter(Notification.user_id == 2).order_by(Notification.created_at.desc()).first()
    assert notif is not None
    assert "Approved" in notif.title

    # 5. Verify audit log entry in dbo.admin_audit_logs
    audit = db.query(AdminAuditLog).filter(
        AdminAuditLog.entity_name == "rental_vehicles",
        AdminAuditLog.entity_id == 101,
        AdminAuditLog.action == "approve_rental_vehicle",
    ).first()
    assert audit is not None
    assert audit.admin_user_id == 1


def test_bicycle_approval_without_documents(seed_test_data, isolated_db_session):
    """Non-motorized vehicles (bicycles) can be approved without motorized insurance/revenue licenses."""
    db, _ = isolated_db_session
    client = make_test_client(db, current_user=seed_test_data["admin_user"])

    resp = client.post("/api/v1/admin/rentals/vehicles/102/review", json={"decision": "approved"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["new_approval_status"] == "approved"


def test_vehicle_rejection_requires_reason(seed_test_data, isolated_db_session):
    """Requirement 8: Rejection requires a mandatory reason, sets status to 'rejected', and notifies provider."""
    db, _ = isolated_db_session
    client = make_test_client(db, current_user=seed_test_data["admin_user"])

    # 1. Missing reason rejected with 400
    resp_empty = client.post("/api/v1/admin/rentals/vehicles/102/review", json={"decision": "rejected", "reason": "   "})
    assert resp_empty.status_code == 400
    assert "Rejection reason is mandatory" in resp_empty.json()["detail"]

    # 2. Valid reason succeeds
    resp_valid = client.post(
        "/api/v1/admin/rentals/vehicles/102/review",
        json={"decision": "rejected", "reason": "Bicycle photos are blurry and missing helmet policy."},
    )
    assert resp_valid.status_code == 200, resp_valid.text
    data = resp_valid.json()
    assert data["new_approval_status"] == "rejected"
    assert data["rejection_reason"] == "Bicycle photos are blurry and missing helmet policy."

    # 3. Notification to provider
    notif = db.query(Notification).filter(
        Notification.user_id == 2,
        Notification.notification_type == "vehicle_listing_rejected",
    ).first()
    assert notif is not None
    assert "blurry" in notif.message


def test_vehicle_suspension_behavior(seed_test_data, isolated_db_session):
    """Verifies that an approved vehicle can be suspended with reason, deactivating its public listing."""
    db, _ = isolated_db_session
    client = make_test_client(db, current_user=seed_test_data["admin_user"])

    # Suspend approved tuk-tuk (v3)
    resp = client.post(
        "/api/v1/admin/rentals/vehicles/103/review",
        json={"decision": "suspended", "reason": "Reported mechanical safety issues by travelers."},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["new_approval_status"] == "suspended"

    # Status updated to unavailable
    vehicle = db.query(RentalVehicle).filter(RentalVehicle.id == 103).first()
    assert vehicle.approval_status == "suspended"
    assert vehicle.status == "unavailable"


def test_invalid_status_transitions_prevented(seed_test_data, isolated_db_session):
    """Requirement 9: Prevent invalid transitions like approving already approved or rejected listings."""
    db, _ = isolated_db_session
    client = make_test_client(db, current_user=seed_test_data["admin_user"])

    # 1. Approve already approved vehicle -> 400
    resp_dup = client.post("/api/v1/admin/rentals/vehicles/103/review", json={"decision": "approved"})
    assert resp_dup.status_code == 400
    assert "already approved" in resp_dup.json()["detail"]

    # 2. Approve directly from rejected vehicle -> 400
    resp_rej = client.post("/api/v1/admin/rentals/vehicles/104/review", json={"decision": "approved"})
    assert resp_rej.status_code == 400
    assert "Cannot approve a rejected listing directly" in resp_rej.json()["detail"]


# ==============================================================================
# AD-03: VEHICLE DOCUMENT VERIFICATION TESTS
# ==============================================================================

def test_document_verification_queue_and_actions(seed_test_data, isolated_db_session):
    """Verifies listing pending compliance documents and verifying/rejecting documents with audit logging."""
    db, _ = isolated_db_session
    client = make_test_client(db, current_user=seed_test_data["admin_user"])

    # 1. Pending documents queue returns the 2 unverified docs
    resp_docs = client.get("/api/v1/admin/rentals/documents/pending")
    assert resp_docs.status_code == 200, resp_docs.text
    data_docs = resp_docs.json()
    assert data_docs["total"] == 2
    types = [d["document_type"] for d in data_docs["items"]]
    assert "revenue_license" in types
    assert "insurance_certificate" in types

    # 2. Verify single document #1 (approved)
    resp_v1 = client.post("/api/v1/admin/rentals/documents/1/verify", json={"decision": "approved", "notes": "License valid until 2027"})
    assert resp_v1.status_code == 200, resp_v1.text
    assert resp_v1.json()["is_verified"] is True

    # 3. Reject single document #2
    resp_v2 = client.post("/api/v1/admin/rentals/documents/2/verify", json={"decision": "rejected", "notes": "Insurance policy number unclear"})
    assert resp_v2.status_code == 200
    assert resp_v2.json()["is_verified"] is False

    # 4. Verify audit entries
    audits = db.query(AdminAuditLog).filter(AdminAuditLog.entity_name == "rental_vehicle_documents").all()
    assert len(audits) >= 2


def test_overall_vehicle_verification_status_updates(seed_test_data, isolated_db_session):
    """Requirement 15: Overall vehicle verification requires verified documents for motorized vehicles."""
    db, _ = isolated_db_session
    client = make_test_client(db, current_user=seed_test_data["admin_user"])

    # 1. Attempting to mark motorized vehicle as verified when documents are unverified fails with 400
    resp_fail = client.post("/api/v1/admin/rentals/vehicles/101/verify-status", json={"decision": "verified"})
    assert resp_fail.status_code == 400
    assert "required compliance documents" in resp_fail.json()["detail"]

    # 2. Verify both documents
    doc1 = db.query(RentalVehicleDocument).filter(RentalVehicleDocument.id == 1).first()
    doc2 = db.query(RentalVehicleDocument).filter(RentalVehicleDocument.id == 2).first()
    doc1.is_verified = True
    doc2.is_verified = True
    db.commit()

    # 3. Now marking vehicle as verified succeeds
    resp_ok = client.post("/api/v1/admin/rentals/vehicles/101/verify-status", json={"decision": "verified"})
    assert resp_ok.status_code == 200, resp_ok.text
    assert resp_ok.json()["new_verification_status"] == "verified"


@pytest.mark.parametrize("reference", [
    "rev_license_101.pdf",
    "uploads/rental_documents/rev_license_101.pdf",
    r"C:\legacy\uploads\rev_license_101.pdf",
])
def test_document_file_streams_valid_legacy_references(seed_test_data, isolated_db_session, reference):
    db, _ = isolated_db_session
    db.get(RentalVehicleDocument, 1).file_reference = reference
    admin = seed_test_data["admin_user"]
    admin.role = "operations_admin"
    db.commit()
    client = make_test_client(db, current_user=admin)

    response = client.get("/api/v1/admin/rentals/documents/1/file")
    assert response.status_code == 200
    assert b"%PDF-1.4" in response.content
    assert response.headers["Content-Type"] == "application/pdf"
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_document_file_rejects_finance_admin(seed_test_data, isolated_db_session):
    db, _ = isolated_db_session
    admin = seed_test_data["admin_user"]
    admin.role = "finance_admin"
    db.commit()
    client = make_test_client(db, current_user=admin)

    assert client.get("/api/v1/admin/rentals/documents/1/file").status_code == 403


def test_document_file_rejects_missing_record(seed_test_data, isolated_db_session):
    db, _ = isolated_db_session
    client = make_test_client(db, current_user=seed_test_data["admin_user"])

    response = client.get("/api/v1/admin/rentals/documents/999/file")
    assert response.status_code == 404
    assert response.json() == {"detail": "Vehicle document not found."}


@pytest.mark.parametrize(("reference", "status_code", "detail"), [
    ("missing.pdf", 404, "Document file not found on storage server."),
    ("directory.pdf", 404, "Document file not found on storage server."),
    ("rev_license_101.pdf/", 400, "Invalid document path reference."),
    ("rev_license_101.pdf:stream", 400, "Invalid document path reference."),
    ("../../rev_license_101.pdf", 400, "Invalid document path reference."),
    (r"..\rev_license_101.pdf", 400, "Invalid document path reference."),
])
def test_document_file_access_security_and_path_traversal(
    seed_test_data, isolated_db_session, reference, status_code, detail,
):
    db, docs_dir = isolated_db_session
    (Path(docs_dir) / "directory.pdf").mkdir()
    db.get(RentalVehicleDocument, 1).file_reference = reference
    db.commit()
    client = make_test_client(db, current_user=seed_test_data["admin_user"])

    response = client.get("/api/v1/admin/rentals/documents/1/file")
    assert response.status_code == status_code
    assert response.json() == {"detail": detail}


def test_document_file_rejects_resolved_target_outside_root(seed_test_data, isolated_db_session, monkeypatch):
    db, docs_dir = isolated_db_session
    root = Path(docs_dir).resolve()
    candidate = root / "rev_license_101.pdf"
    outside_dir = root.with_name(root.name + "_outside")
    outside_dir.mkdir()
    outside = outside_dir / candidate.name
    outside.write_bytes(b"private")
    original_resolve = Path.resolve

    def resolve(path, *args, **kwargs):
        if path == candidate:
            return outside
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    client = make_test_client(db, current_user=seed_test_data["admin_user"])

    response = client.get("/api/v1/admin/rentals/documents/1/file")
    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid document path reference."}


@pytest.mark.parametrize("link_kind", ["symlink", "junction"])
def test_document_file_rejects_filesystem_link_escape(seed_test_data, isolated_db_session, link_kind):
    db, docs_dir = isolated_db_session
    root = Path(docs_dir)
    link = root / "linked.pdf"
    outside = root.parent / "private"
    try:
        if link_kind == "junction":
            if os.name != "nt":
                pytest.skip("Directory junctions require Windows")
            import _winapi

            outside.mkdir()
            _winapi.CreateJunction(str(outside), str(link))
        else:
            outside.write_bytes(b"private")
            link.symlink_to(outside)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"Local filesystem cannot create a {link_kind}: {type(exc).__name__}")

    db.get(RentalVehicleDocument, 1).file_reference = link.name
    db.commit()
    client = make_test_client(db, current_user=seed_test_data["admin_user"])

    response = client.get("/api/v1/admin/rentals/documents/1/file")
    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid document path reference."}


# ==============================================================================
# TRAVELER DISCOVERY & PROVIDER TAMPER PROTECTION TESTS
# ==============================================================================

def test_traveler_discovery_visibility_reflects_admin_decisions(seed_test_data, isolated_db_session):
    """Requirement 16: Non-approved vehicles (pending, rejected, suspended) are hidden from Traveler discovery."""
    db, _ = isolated_db_session
    client = make_test_client(db)

    # 1. Discovery initially returns only v3 (the only approved vehicle)
    resp_disc = client.get("/api/v1/rental-vehicles")
    assert resp_disc.status_code == 200
    disc_ids = [v["id"] for v in resp_disc.json()["items"]]
    assert 103 in disc_ids
    assert 101 not in disc_ids  # pending
    assert 102 not in disc_ids  # pending
    assert 104 not in disc_ids  # rejected

    # 2. Detail view for non-approved vehicle returns 404
    assert client.get("/api/v1/rental-vehicles/101").status_code == 404
    assert client.get("/api/v1/rental-vehicles/104").status_code == 404

    # 3. Detail view for approved vehicle returns 200
    assert client.get("/api/v1/rental-vehicles/103").status_code == 200


def test_provider_cannot_tamper_with_admin_controlled_fields(seed_test_data, isolated_db_session):
    """Requirement 17: Providers cannot self-approve or self-verify vehicles via provider update API."""
    db, _ = isolated_db_session
    prov_user = seed_test_data["prov_user"]
    client_provider = make_test_client(db, current_user=prov_user)

    # Provider attempts to send approval_status='approved' and verification_status='verified'
    payload = {
        "vehicle_name": "Tampered Activa",
        "approval_status": "approved",
        "verification_status": "verified",
    }
    resp = client_provider.patch("/api/v1/provider/rentals/vehicles/101", json=payload)
    assert resp.status_code == 200

    # In database, approval_status must remain 'pending' and verification_status 'unverified'
    vehicle = db.query(RentalVehicle).filter(RentalVehicle.id == 101).first()
    assert vehicle.approval_status == "pending"
    assert vehicle.verification_status == "unverified"
    assert vehicle.vehicle_name == "Tampered Activa"


# ==============================================================================
# INTEGRATION: AD-01 & AD-04 PROVIDER ONBOARDING INTEGRATION
# ==============================================================================

def test_admin_provider_integration_verification(seed_test_data, isolated_db_session):
    """Verifies that admin provider approval and vehicle listing approval operate in complete unison."""
    db, _ = isolated_db_session
    client_admin = make_test_client(db, current_user=seed_test_data["admin_user"])

    # 1. Inspect provider application details via AD-04
    resp_prov = client_admin.get("/api/v1/admin/providers/201")
    assert resp_prov.status_code == 200, resp_prov.text
    prov_data = resp_prov.json()
    assert prov_data["id"] == 201
    assert prov_data["business_name"] == "Bandara Galle Motorbikes"
    assert prov_data["provider_type"] == "rental"
    assert prov_data["approval_status"] == "approved"

    # 2. Inspect provider's pending vehicles via AD-02
    resp_veh = client_admin.get("/api/v1/admin/rentals/vehicles?provider_id=201&approval_status=pending")
    assert resp_veh.status_code == 200
    assert resp_veh.json()["total"] == 2
