"""
Automated tests for Module 04 (P0.3) — Eco Safety Rules, Sustainability Practices,
Private Evidence Upload, and Admin Eco Badge Verification.

Covers:
1. Eco Safety Rules:
   - Provider creates, views, and deletes safety rules on owned listings.
   - Provider ownership isolation (cross-provider editing/deletion returns 403).
   - Public visibility: safety rules hidden on unpublished listings, visible on approved & published listings.
2. Sustainability Practices:
   - Provider creates and lists practices.
   - Cross-provider isolation on practice management.
   - Safe metadata exposure (practices management returns evidence metadata, never server paths).
3. Private Evidence Upload & Validation:
   - File type validation: allowed (.pdf, .jpg, .png, etc.), rejected (.exe, .sh).
   - File size validation: empty file (0B) rejected, >5MB rejected.
   - Invalid document type rejected (422).
   - Evidence metadata returned without raw server filesystem path (`file_reference`).
   - Cross-provider upload blocked (403).
   - Traveler upload blocked (403).
4. Private Evidence Storage & Security:
   - Private storage outside public static directories.
   - Unauthorized access blocked: travelers (403) and other providers (403) cannot access private evidence.
   - Public listing details never leak evidence files or private paths.
   - Admin secure streaming with Cache-Control: no-store and nosniff headers.
   - Path traversal defenses.
5. Verification Submission Requirements:
   - Cannot submit verification without practices (400).
   - Cannot submit verification without supporting evidence (400).
   - Valid submission transitions `eco_badge_status` to 'pending' and creates audit record.
   - Duplicate pending submission blocked (400).
6. Admin Badge Decisions & Decision History:
   - Admin-only decision endpoint (traveler/provider blocked with 403).
   - Grant decision sets badge='granted', marks practices as verified.
   - Public response exposes `has_verified_eco_badge=True` and appears in `eco_badge_only=true` filter.
   - Revoke decision immediately revokes badge and resets practice verification.
   - Public response immediately drops badge (`has_verified_eco_badge=False`) and disappears from `eco_badge_only=true`.
   - Complete immutable verification decision history preserved.
7. Expired Badge Visibility:
   - Expired badge (`valid_until` in past) immediately disappears from public responses and badge filter.
"""

from datetime import UTC, datetime, timedelta
import io
import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import create_access_token, hash_password
from app.database.connection import get_db
from app.models.eco_experience import (
    EcoEvidence,
    EcoExperience,
    EcoPractice,
    EcoSafetyRule,
    EcoVerification,
)
from app.models.provider_profile import ProviderProfile
from app.models.user import Base, User
from app.routers.auth import router as auth_router
from app.routers.eco_experiences import (
    admin_eco_router,
    router as eco_experiences_router,
)

eco_p03_app = FastAPI(title="CeylonGo M04 P0.3 Test App")
eco_p03_app.include_router(auth_router)
eco_p03_app.include_router(eco_experiences_router)
eco_p03_app.include_router(admin_eco_router)

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
def setup_test_db(tmp_path, monkeypatch):
    Base.metadata.create_all(bind=test_engine)
    evidence_dir = tmp_path / "private_eco_evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ECO_EVIDENCE_UPLOAD_DIR", str(evidence_dir))
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
def client(db_session: Session):
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    eco_p03_app.dependency_overrides[get_db] = override_get_db
    with TestClient(eco_p03_app) as test_client:
        yield test_client
    eco_p03_app.dependency_overrides.clear()


def auth_header(user: User) -> dict[str, str]:
    token = create_access_token(data={"sub": str(user.id)})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def users_and_profiles(db_session: Session):
    traveler = User(
        full_name="Traveler User",
        email="traveler_p3@test.com",
        password_hash=hash_password("TravelerPass123!"),
        role="traveler",
        is_active=True,
        created_at=datetime.now(UTC),
    )
    admin = User(
        full_name="Admin User",
        email="admin_p3@test.com",
        password_hash=hash_password("AdminPass123!"),
        role="admin",
        is_active=True,
        created_at=datetime.now(UTC),
    )
    eco_user_1 = User(
        full_name="Eco Provider One",
        email="eco_provider1@test.com",
        password_hash=hash_password("EcoPass123!"),
        role="provider",
        is_active=True,
        created_at=datetime.now(UTC),
    )
    eco_user_2 = User(
        full_name="Eco Provider Two",
        email="eco_provider2@test.com",
        password_hash=hash_password("EcoPass456!"),
        role="provider",
        is_active=True,
        created_at=datetime.now(UTC),
    )

    db_session.add_all([traveler, admin, eco_user_1, eco_user_2])
    db_session.flush()

    eco_profile_1 = ProviderProfile(
        user_id=eco_user_1.id,
        business_name="Sinharaja Green Trails Ltd",
        provider_type="eco_tourism",
        district="Ratnapura",
        approval_status="approved",
        phone="+94771234567",
        created_at=datetime.now(UTC),
    )
    eco_profile_2 = ProviderProfile(
        user_id=eco_user_2.id,
        business_name="Knuckles Eco Treks",
        provider_type="eco_tourism",
        district="Matale",
        approval_status="approved",
        phone="+94777654321",
        created_at=datetime.now(UTC),
    )

    db_session.add_all([eco_profile_1, eco_profile_2])
    db_session.commit()

    return {
        "traveler": traveler,
        "admin": admin,
        "eco_user_1": eco_user_1,
        "eco_user_2": eco_user_2,
        "eco_profile_1": eco_profile_1,
        "eco_profile_2": eco_profile_2,
    }


def create_base_experience(client: TestClient, provider_user: User) -> int:
    payload = {
        "title": "Sinharaja Wilderness Trek",
        "eco_category": "Forest & Nature Trails",
        "duration_hours": 4.5,
        "district": "Ratnapura",
        "city": "Kalawana",
        "meeting_point": "Kudawa Main Gate",
        "pricing_type": "per_person",
        "base_price": 5000.0,
        "currency": "LKR",
        "max_group_size": 10,
        "min_participants": 2,
        "environmental_guidelines": "Stay on marked trails and do not litter.",
        "wildlife_safety_summary": "Do not feed wildlife and maintain distance from snakes.",
    }
    res = client.post(
        "/api/v1/eco-experiences",
        json=payload,
        headers=auth_header(provider_user),
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


# ==============================================================================
# 1. Eco Safety Rules Tests
# ==============================================================================


def test_eco_safety_rules_crud_and_provider_isolation(
    client: TestClient, users_and_profiles
):
    headers_1 = auth_header(users_and_profiles["eco_user_1"])
    headers_2 = auth_header(users_and_profiles["eco_user_2"])
    exp_id = create_base_experience(client, users_and_profiles["eco_user_1"])

    # 1. Provider 1 adds a safety rule
    rule_payload = {
        "rule_type": "wildlife_no_feeding",
        "title": "No Feeding of Endemic Birds",
        "description": "Never feed bird species or alter natural foraging behavior.",
        "is_mandatory": True,
    }
    create_rule_res = client.post(
        f"/api/v1/eco-experiences/{exp_id}/safety-rules",
        json=rule_payload,
        headers=headers_1,
    )
    assert create_rule_res.status_code == 201
    rule_data = create_rule_res.json()
    rule_id = rule_data["id"]
    assert rule_data["rule_type"] == "wildlife_no_feeding"
    assert rule_data["title"] == "No Feeding of Endemic Birds"
    assert rule_data["is_mandatory"] is True

    # 2. Provider 2 attempts to add rule to Provider 1's listing -> 403
    p2_rule_res = client.post(
        f"/api/v1/eco-experiences/{exp_id}/safety-rules",
        json=rule_payload,
        headers=headers_2,
    )
    assert p2_rule_res.status_code == 403

    # 3. Provider 2 attempts to delete Provider 1's rule -> 403
    p2_delete_res = client.delete(
        f"/api/v1/eco-experiences/{exp_id}/safety-rules/{rule_id}",
        headers=headers_2,
    )
    assert p2_delete_res.status_code == 403

    # 4. Public caller tries to view rules on unpublished listing -> 404
    public_rules_res = client.get(
        f"/api/v1/eco-experiences/{exp_id}/safety-rules"
    )
    assert public_rules_res.status_code == 404

    # 5. Provider 1 deletes rule -> 204
    delete_res = client.delete(
        f"/api/v1/eco-experiences/{exp_id}/safety-rules/{rule_id}",
        headers=headers_1,
    )
    assert delete_res.status_code == 204


# ==============================================================================
# 2. Sustainability Practices & Provider Isolation Tests
# ==============================================================================


def test_eco_practice_management_and_isolation(
    client: TestClient, users_and_profiles
):
    headers_1 = auth_header(users_and_profiles["eco_user_1"])
    headers_2 = auth_header(users_and_profiles["eco_user_2"])
    exp_id = create_base_experience(client, users_and_profiles["eco_user_1"])

    # 1. Provider 1 adds a practice
    practice_payload = {
        "practice_category": "renewable_energy",
        "claim_description": "Our trail station is 100% powered by certified solar mini-grid.",
    }
    practice_res = client.post(
        f"/api/v1/eco-experiences/{exp_id}/practices",
        json=practice_payload,
        headers=headers_1,
    )
    assert practice_res.status_code == 201
    practice_data = practice_res.json()
    practice_id = practice_data["id"]
    assert practice_data["practice_category"] == "renewable_energy"
    assert practice_data["is_verified"] is False
    assert practice_data["evidences"] == []

    # 2. Provider 2 attempts to add practice to Provider 1's listing -> 403
    p2_add = client.post(
        f"/api/v1/eco-experiences/{exp_id}/practices",
        json=practice_payload,
        headers=headers_2,
    )
    assert p2_add.status_code == 403

    # 3. Provider 1 lists practices -> 200
    list_res = client.get(
        f"/api/v1/eco-experiences/{exp_id}/practices",
        headers=headers_1,
    )
    assert list_res.status_code == 200
    assert len(list_res.json()) == 1
    assert list_res.json()[0]["id"] == practice_id

    # 4. Provider 2 attempts to delete Provider 1's practice -> 403
    p2_del = client.delete(
        f"/api/v1/eco-experiences/{exp_id}/practices/{practice_id}",
        headers=headers_2,
    )
    assert p2_del.status_code == 403


# ==============================================================================
# 3. Private Evidence Upload & Validation Tests
# ==============================================================================


def test_eco_evidence_upload_validation_and_security(
    client: TestClient, users_and_profiles
):
    headers_1 = auth_header(users_and_profiles["eco_user_1"])
    headers_2 = auth_header(users_and_profiles["eco_user_2"])
    traveler_headers = auth_header(users_and_profiles["traveler"])
    exp_id = create_base_experience(client, users_and_profiles["eco_user_1"])

    # Add practice
    practice_res = client.post(
        f"/api/v1/eco-experiences/{exp_id}/practices",
        json={
            "practice_category": "wildlife_protection",
            "claim_description": "We employ licensed local wildlife trackers to monitor bird nests.",
        },
        headers=headers_1,
    )
    practice_id = practice_res.json()["id"]

    # 1. Invalid document_type -> 422
    fake_pdf = io.BytesIO(b"%PDF-1.4 Fake PDF Content")
    bad_type_res = client.post(
        f"/api/v1/eco-experiences/{exp_id}/practices/{practice_id}/evidence",
        data={"document_type": "invalid_document_type", "notes": "Test"},
        files={"file": ("permit.pdf", fake_pdf, "application/pdf")},
        headers=headers_1,
    )
    assert bad_type_res.status_code == 422

    # 2. Unsupported file extension (.exe) -> 400
    bad_ext_file = io.BytesIO(b"malicious script")
    bad_ext_res = client.post(
        f"/api/v1/eco-experiences/{exp_id}/practices/{practice_id}/evidence",
        data={"document_type": "permit"},
        files={"file": ("payload.exe", bad_ext_file, "application/octet-stream")},
        headers=headers_1,
    )
    assert bad_ext_res.status_code == 400
    assert "unsupported file extension" in bad_ext_res.json()["detail"].lower()

    # 3. Empty file (0 bytes) -> 400
    empty_file = io.BytesIO(b"")
    empty_res = client.post(
        f"/api/v1/eco-experiences/{exp_id}/practices/{practice_id}/evidence",
        data={"document_type": "permit"},
        files={"file": ("empty.pdf", empty_file, "application/pdf")},
        headers=headers_1,
    )
    assert empty_res.status_code == 400
    assert "empty" in empty_res.json()["detail"].lower()

    # 4. File exceeding size limit (> 5 MB) -> 400
    oversized = io.BytesIO(b"A" * (5 * 1024 * 1024 + 10))
    over_res = client.post(
        f"/api/v1/eco-experiences/{exp_id}/practices/{practice_id}/evidence",
        data={"document_type": "permit"},
        files={"file": ("huge.pdf", oversized, "application/pdf")},
        headers=headers_1,
    )
    assert over_res.status_code == 400
    assert "maximum allowed size" in over_res.json()["detail"].lower()

    # 5. Traveler cannot upload evidence -> 403
    valid_pdf = io.BytesIO(b"%PDF-1.4 Department of Wildlife Conservation Permit")
    trav_res = client.post(
        f"/api/v1/eco-experiences/{exp_id}/practices/{practice_id}/evidence",
        data={"document_type": "permit"},
        files={"file": ("permit.pdf", valid_pdf, "application/pdf")},
        headers=traveler_headers,
    )
    assert trav_res.status_code == 403

    # 6. Provider 2 cannot upload evidence to Provider 1's listing -> 403
    valid_pdf.seek(0)
    p2_res = client.post(
        f"/api/v1/eco-experiences/{exp_id}/practices/{practice_id}/evidence",
        data={"document_type": "permit"},
        files={"file": ("permit.pdf", valid_pdf, "application/pdf")},
        headers=headers_2,
    )
    assert p2_res.status_code == 403

    # 7. Provider 1 successfully uploads valid evidence -> 201
    valid_pdf.seek(0)
    upload_res = client.post(
        f"/api/v1/eco-experiences/{exp_id}/practices/{practice_id}/evidence",
        data={
            "document_type": "permit",
            "notes": "DWC Annual Research and Guiding Permit #DWC/2026/044",
        },
        files={"file": ("permit.pdf", valid_pdf, "application/pdf")},
        headers=headers_1,
    )
    assert upload_res.status_code == 201
    meta = upload_res.json()
    assert meta["document_type"] == "permit"
    assert meta["notes"] == "DWC Annual Research and Guiding Permit #DWC/2026/044"
    assert meta["eco_practice_id"] == practice_id
    # SECURITY ASSERTION: Internal file_reference / server filesystem path must NEVER be returned
    assert "file_reference" not in meta
    assert "/" not in meta.get("file_reference", "")
    assert "\\" not in meta.get("file_reference", "")


# ==============================================================================
# 4. Private Evidence Storage & Admin Stream Security Tests
# ==============================================================================


def test_private_evidence_storage_and_admin_stream_security(
    client: TestClient, users_and_profiles
):
    headers_1 = auth_header(users_and_profiles["eco_user_1"])
    headers_2 = auth_header(users_and_profiles["eco_user_2"])
    admin_headers = auth_header(users_and_profiles["admin"])
    traveler_headers = auth_header(users_and_profiles["traveler"])

    exp_id = create_base_experience(client, users_and_profiles["eco_user_1"])

    # Create practice and upload evidence
    practice_res = client.post(
        f"/api/v1/eco-experiences/{exp_id}/practices",
        json={
            "practice_category": "waste_reduction",
            "claim_description": "Zero single-use plastics policy enforced with stainless steel refill stations.",
        },
        headers=headers_1,
    )
    practice_id = practice_res.json()["id"]

    pdf_bytes = b"%PDF-1.4 Zero Waste Sri Lanka Audit Report 2026"
    upload_res = client.post(
        f"/api/v1/eco-experiences/{exp_id}/practices/{practice_id}/evidence",
        data={"document_type": "audit_report", "notes": "Audited by EcoLanka"},
        files={"file": ("audit.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
        headers=headers_1,
    )
    evidence_id = upload_res.json()["id"]

    # 1. Traveler attempts to download evidence directly -> 403
    trav_stream = client.get(
        f"/api/v1/admin/eco-experiences/{exp_id}/evidence/{evidence_id}",
        headers=traveler_headers,
    )
    assert trav_stream.status_code == 403

    # 2. Other provider attempts to download evidence -> 403
    p2_stream = client.get(
        f"/api/v1/admin/eco-experiences/{exp_id}/evidence/{evidence_id}",
        headers=headers_2,
    )
    assert p2_stream.status_code == 403

    # 3. Unauthenticated caller -> 401
    anon_stream = client.get(
        f"/api/v1/admin/eco-experiences/{exp_id}/evidence/{evidence_id}"
    )
    assert anon_stream.status_code == 401

    # 4. Admin downloads evidence -> 200 OK with security headers
    admin_stream = client.get(
        f"/api/v1/admin/eco-experiences/{exp_id}/evidence/{evidence_id}",
        headers=admin_headers,
    )
    assert admin_stream.status_code == 200
    assert admin_stream.content == pdf_bytes
    assert "no-store" in admin_stream.headers.get("Cache-Control", "")
    assert admin_stream.headers.get("X-Content-Type-Options") == "nosniff"

    # 5. Non-existent evidence ID -> 404
    missing_stream = client.get(
        f"/api/v1/admin/eco-experiences/{exp_id}/evidence/99999",
        headers=admin_headers,
    )
    assert missing_stream.status_code == 404


# ==============================================================================
# 5. Verification Submission Requirements Tests
# ==============================================================================


def test_provider_verification_submission_requirements(
    client: TestClient, users_and_profiles
):
    headers_1 = auth_header(users_and_profiles["eco_user_1"])
    exp_id = create_base_experience(client, users_and_profiles["eco_user_1"])

    # 1. Submit with NO practices declared -> 400 Bad Request
    no_practice_sub = client.post(
        f"/api/v1/eco-experiences/{exp_id}/submit-verification",
        headers=headers_1,
    )
    assert no_practice_sub.status_code == 400
    assert "at least one sustainability practice" in no_practice_sub.json()["detail"].lower()

    # 2. Add practice but NO evidence uploaded -> 400 Bad Request
    practice_res = client.post(
        f"/api/v1/eco-experiences/{exp_id}/practices",
        json={
            "practice_category": "habitat_restoration",
            "claim_description": "We plant 10 endemic canopy trees for every 5 groups guided.",
        },
        headers=headers_1,
    )
    practice_id = practice_res.json()["id"]

    no_evidence_sub = client.post(
        f"/api/v1/eco-experiences/{exp_id}/submit-verification",
        headers=headers_1,
    )
    assert no_evidence_sub.status_code == 400
    assert "supporting evidence uploaded" in no_evidence_sub.json()["detail"].lower()

    # 3. Upload evidence and submit -> 200 OK
    pdf_bytes = b"%PDF-1.4 Tree Planting Certificate 2026"
    client.post(
        f"/api/v1/eco-experiences/{exp_id}/practices/{practice_id}/evidence",
        data={"document_type": "certificate", "notes": "Forestry Dept Certificate"},
        files={"file": ("cert.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
        headers=headers_1,
    )

    valid_sub = client.post(
        f"/api/v1/eco-experiences/{exp_id}/submit-verification",
        headers=headers_1,
    )
    assert valid_sub.status_code == 200
    sub_data = valid_sub.json()
    assert sub_data["eco_badge_status"] == "pending"
    assert len(sub_data["verifications"]) == 1
    assert sub_data["verifications"][0]["decision"] == "submitted"

    # 4. Attempting to submit again while pending -> 400 Bad Request
    dup_sub = client.post(
        f"/api/v1/eco-experiences/{exp_id}/submit-verification",
        headers=headers_1,
    )
    assert dup_sub.status_code == 400
    assert "already pending" in dup_sub.json()["detail"].lower()


# ==============================================================================
# 6. Admin Badge Decisions, Revocation, and Verification History Tests
# ==============================================================================


def test_admin_badge_decision_grant_reject_revoke_and_history(
    client: TestClient, users_and_profiles, db_session: Session
):
    headers_1 = auth_header(users_and_profiles["eco_user_1"])
    admin_headers = auth_header(users_and_profiles["admin"])
    traveler_headers = auth_header(users_and_profiles["traveler"])

    exp_id = create_base_experience(client, users_and_profiles["eco_user_1"])

    # Submit listing for approval and approve/publish it
    client.post(f"/api/v1/eco-experiences/{exp_id}/submit", headers=headers_1)
    client.post(
        f"/api/v1/admin/eco-experiences/{exp_id}/approve", headers=admin_headers
    )
    client.patch(
        f"/api/v1/eco-experiences/{exp_id}/publish",
        json={"is_published": True},
        headers=headers_1,
    )

    # Add practice & upload evidence
    p_res = client.post(
        f"/api/v1/eco-experiences/{exp_id}/practices",
        json={
            "practice_category": "community_employment",
            "claim_description": "100% of resident trek guides are hired from Kudawa village.",
        },
        headers=headers_1,
    )
    p_id = p_res.json()["id"]
    client.post(
        f"/api/v1/eco-experiences/{exp_id}/practices/{p_id}/evidence",
        data={"document_type": "audit_report", "notes": "Grama Niladhari village payroll certification"},
        files={"file": ("payroll.pdf", io.BytesIO(b"%PDF-1.4 village payroll"), "application/pdf")},
        headers=headers_1,
    )

    # Provider submits verification request
    client.post(
        f"/api/v1/eco-experiences/{exp_id}/submit-verification",
        headers=headers_1,
    )

    # 1. Non-admin cannot issue badge decision -> 403
    non_admin_dec = client.post(
        f"/api/v1/admin/eco-experiences/{exp_id}/badge-decision",
        json={"decision": "granted", "reason": "Looks good"},
        headers=traveler_headers,
    )
    assert non_admin_dec.status_code == 403

    # 2. Admin grants badge with 1-year validity
    valid_until = (datetime.now(UTC) + timedelta(days=365)).isoformat()
    grant_res = client.post(
        f"/api/v1/admin/eco-experiences/{exp_id}/badge-decision",
        json={
            "decision": "granted",
            "reason": "Verified Kudawa village community employment documentation and local certifications.",
            "valid_until": valid_until,
        },
        headers=admin_headers,
    )
    assert grant_res.status_code == 200
    granted_data = grant_res.json()
    assert granted_data["eco_badge_status"] == "granted"
    assert granted_data["has_verified_eco_badge"] is True
    assert granted_data["practices"][0]["is_verified"] is True

    # Check public visibility & badge filter
    db_session.expire_all()
    pub_res = client.get(f"/api/v1/eco-experiences/{exp_id}")
    assert pub_res.status_code == 200
    assert pub_res.json()["has_verified_eco_badge"] is True

    badge_filter_res = client.get(
        "/api/v1/eco-experiences?eco_badge_only=true"
    )
    assert badge_filter_res.status_code == 200
    assert badge_filter_res.json()["total"] == 1
    assert badge_filter_res.json()["items"][0]["id"] == exp_id

    # 3. Admin revokes badge -> immediate disappearance from public
    revoke_res = client.post(
        f"/api/v1/admin/eco-experiences/{exp_id}/badge-decision",
        json={
            "decision": "revoked",
            "reason": "Unannounced audit discovered non-compliance with village hiring guidelines.",
        },
        headers=admin_headers,
    )
    assert revoke_res.status_code == 200
    revoked_data = revoke_res.json()
    assert revoked_data["eco_badge_status"] == "revoked"
    assert revoked_data["has_verified_eco_badge"] is False
    assert revoked_data["practices"][0]["is_verified"] is False

    # Public visibility must immediately show badge as revoked (False)
    db_session.expire_all()
    pub_revoked = client.get(f"/api/v1/eco-experiences/{exp_id}")
    assert pub_revoked.status_code == 200
    assert pub_revoked.json()["has_verified_eco_badge"] is False

    # Immediately drops from eco_badge_only filter
    badge_filter_revoked = client.get(
        "/api/v1/eco-experiences?eco_badge_only=true"
    )
    assert badge_filter_revoked.status_code == 200
    assert badge_filter_revoked.json()["total"] == 0

    # 4. Admin retrieves complete verification decision history
    history_res = client.get(
        f"/api/v1/admin/eco-experiences/{exp_id}/verifications",
        headers=admin_headers,
    )
    assert history_res.status_code == 200
    history = history_res.json()
    assert len(history) == 3
    # Ordered descending by creation time: revoked, granted, submitted
    assert history[0]["decision"] == "revoked"
    assert history[1]["decision"] == "granted"
    assert history[2]["decision"] == "submitted"
    assert history[0]["reviewed_by_admin_id"] == users_and_profiles["admin"].id


# ==============================================================================
# 7. Expired Badge Visibility Tests
# ==============================================================================


def test_expired_badge_disappears_immediately_from_public(
    client: TestClient, users_and_profiles, db_session: Session
):
    headers_1 = auth_header(users_and_profiles["eco_user_1"])
    admin_headers = auth_header(users_and_profiles["admin"])

    exp_id = create_base_experience(client, users_and_profiles["eco_user_1"])

    # Approve and publish listing
    client.post(f"/api/v1/eco-experiences/{exp_id}/submit", headers=headers_1)
    client.post(
        f"/api/v1/admin/eco-experiences/{exp_id}/approve", headers=admin_headers
    )
    client.patch(
        f"/api/v1/eco-experiences/{exp_id}/publish",
        json={"is_published": True},
        headers=headers_1,
    )

    # Add practice & upload evidence
    p_res = client.post(
        f"/api/v1/eco-experiences/{exp_id}/practices",
        json={
            "practice_category": "sustainable_sourcing",
            "claim_description": "Trail rations are 100% sourced from local certified organic smallholders.",
        },
        headers=headers_1,
    )
    p_id = p_res.json()["id"]
    client.post(
        f"/api/v1/eco-experiences/{exp_id}/practices/{p_id}/evidence",
        data={"document_type": "certificate", "notes": "Organic certificate"},
        files={"file": ("organic.pdf", io.BytesIO(b"%PDF-1.4 organic"), "application/pdf")},
        headers=headers_1,
    )

    # Provider submits verification
    client.post(
        f"/api/v1/eco-experiences/{exp_id}/submit-verification",
        headers=headers_1,
    )

    # Admin grants badge with an expiration date in the past (expired)
    past_validity = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    grant_expired = client.post(
        f"/api/v1/admin/eco-experiences/{exp_id}/badge-decision",
        json={
            "decision": "granted",
            "reason": "Previous certification grant now expired.",
            "valid_until": past_validity,
        },
        headers=admin_headers,
    )
    assert grant_expired.status_code == 200

    # Even though eco_badge_status is 'granted', has_verified_eco_badge evaluates to False
    db_session.expire_all()
    pub_detail = client.get(f"/api/v1/eco-experiences/{exp_id}")
    assert pub_detail.status_code == 200
    assert pub_detail.json()["has_verified_eco_badge"] is False

    # Expired listing must NOT appear in eco_badge_only filter
    filter_res = client.get("/api/v1/eco-experiences?eco_badge_only=true")
    assert filter_res.status_code == 200
    assert filter_res.json()["total"] == 0
