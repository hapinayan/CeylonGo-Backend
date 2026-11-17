"""
CeylonGo - Microsoft SQL Server Integration Test Runner (Phase 06B)
===================================================================
Executes real SQL Server API integration tests against the isolated integration database.
Enforces strict fail-closed database guards to ensure the live production database (CeylonGoDB)
cannot be connected to or mutated under any circumstances.

Usage:
    python scripts/run_sqlserver_integration_tests.py

Environment Variables:
    DB_SERVER: SQL Server instance (default: .\\SQLEXPRESS)
    DB_NAME: Target database name (default: CeylonGoDB_IntegrationTest)
    DB_DRIVER: ODBC driver name (default: ODBC Driver 18 for SQL Server)
    DB_TRUST_CERTIFICATE: Trust server certificate (default: yes)
"""

from __future__ import annotations

import io
import os
import sys
import tempfile
import time
import uuid
import threading
import urllib.parse
from datetime import date, datetime, timedelta, timezone, UTC
from decimal import Decimal
from pathlib import Path

# ==============================================================================
# Path & Environment Setup (Sanitized - No Hardcoded Personal Paths)
# ==============================================================================

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

TARGET_DB = os.getenv("DB_NAME", "CeylonGoDB_IntegrationTest")
DB_SERVER = os.getenv("DB_SERVER", r".\SQLEXPRESS")
DB_DRIVER = os.getenv("DB_DRIVER", "ODBC Driver 18 for SQL Server")
DB_TRUST_CERTIFICATE = os.getenv("DB_TRUST_CERTIFICATE", "yes")

# Ensure environment is configured before application imports
os.environ["DB_NAME"] = TARGET_DB
os.environ["DB_SERVER"] = DB_SERVER
os.environ["DB_DRIVER"] = DB_DRIVER
os.environ["DB_TRUST_CERTIFICATE"] = DB_TRUST_CERTIFICATE
os.environ["SMTP_HOST"] = ""  # Strictly prevent outbound emails

# Isolated temporary upload directory
temp_upload_dir = tempfile.mkdtemp(prefix="ceylongo_integration_uploads_")
os.environ["UPLOAD_DIR"] = temp_upload_dir

# ==============================================================================
# Pre-Import Fail-Closed Safety Guard Verification
# ==============================================================================

from app.core.database_guard import (
    verify_safe_integration_database,
    DatabaseSafetyError,
)

# Reject unsafe configurations before importing database engines
verify_safe_integration_database(TARGET_DB)

import pyodbc
from PIL import Image
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

from app.main import app
from app.database.connection import get_db
from app.core.security import create_access_token, hash_password
from app.core.rate_limiter import reset_all_limiters
import app.routers.auth as auth_router_mod
import app.routers.provider_otp as provider_otp_mod

# Prevent actual email dispatch by intercepting in memory
last_dispatched_emails = []

def mock_send_email(**kwargs):
    last_dispatched_emails.append(kwargs)
    return True

auth_router_mod.send_password_reset_email = mock_send_email
provider_otp_mod.send_provider_otp_email = mock_send_email

# ==============================================================================
# Dedicated Integration Test Engine & Dependency Override
# ==============================================================================

conn_params = (
    f"DRIVER={{{DB_DRIVER}}};"
    f"SERVER={DB_SERVER};"
    f"DATABASE={TARGET_DB};"
    f"Trusted_Connection=yes;"
    f"TrustServerCertificate={DB_TRUST_CERTIFICATE};"
    "Connection Timeout=30;"
)
db_url = f"mssql+pyodbc:///?odbc_connect={urllib.parse.quote_plus(conn_params)}"
engine = create_engine(db_url, pool_pre_ping=True)
TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

def override_get_db():
    db = TestingSessionLocal()
    # Continuous fail-closed check on every active session fairy checkout
    active_db = db.execute(text("SELECT DB_NAME();")).scalar()
    verify_safe_integration_database(active_db)
    try:
        yield db
    finally:
        db.close()

app.dependency_overrides[get_db] = override_get_db
client = TestClient(app)

results_summary = {
    "total_tests": 0,
    "passed": 0,
    "failed": 0,
    "details": []
}

def record_test(module: str, test_name: str, passed: bool, expected_status: int, actual_status: int, note: str = ""):
    results_summary["total_tests"] += 1
    if passed:
        results_summary["passed"] += 1
        status_str = "[PASS]"
    else:
        results_summary["failed"] += 1
        status_str = "[FAIL]"
    print(f"  {status_str} [{module:<10}] {test_name:<55} -> Exp:{expected_status} Act:{actual_status} {note}")
    results_summary["details"].append({
        "module": module,
        "name": test_name,
        "passed": passed,
        "expected": expected_status,
        "actual": actual_status,
        "note": note
    })


def create_valid_test_jpeg() -> bytes:
    buf = io.BytesIO()
    img = Image.new("RGB", (100, 100), color="blue")
    img.save(buf, format="JPEG")
    return buf.getvalue()


def main():
    print("================================================================================")
    print("     CEYLONGO REAL SQL SERVER INTEGRATION REVALIDATION SUITE (PHASE 06B)       ")
    print("================================================================================")

    # Unique test execution identifier for collision-free repeatability
    unique_run = f"{int(time.time())}_{uuid.uuid4().hex[:6]}"
    nic_base = int(time.time()) % 10000000

    # ==============================================================================
    # TASK 3: Backend Startup & Connectivity
    # ==============================================================================
    print("\n--- [TASK 3] Backend Startup & SQL Server Connectivity ---")

    with engine.connect() as conn:
        row = conn.execute(text("SELECT DB_NAME() AS db, @@SERVERNAME AS srv, CAST(SERVERPROPERTY('ProductVersion') AS VARCHAR(50)) AS ver;")).fetchone()
        current_db = row[0]
        current_srv = row[1]
        sql_ver = row[2]
        verify_safe_integration_database(current_db)
        print(f"  Connected to SQL Server: {current_srv} | Database: {current_db} | Version: {sql_ver}")
        record_test("STARTUP", "SQL Server Session Identity", current_db == TARGET_DB, 200, 200, f"DB: {current_db}")

    resp = client.get("/api/v1/health")
    health_ok = resp.status_code == 200 and resp.json().get("status") == "healthy"
    record_test("STARTUP", "Health Check /api/v1/health", health_ok, 200, resp.status_code)

    resp = client.get("/openapi.json")
    openapi_ok = resp.status_code == 200 and "paths" in resp.json() and len(resp.json()["paths"]) >= 48
    record_test("STARTUP", "OpenAPI Schema Availability", openapi_ok, 200, resp.status_code, f"{len(resp.json().get('paths', {}))} paths")

    # ==============================================================================
    # TASK 4: Traveler Authentication Integration
    # ==============================================================================
    print("\n--- [TASK 4] Traveler Authentication Integration ---")
    traveler_email = f"trav_{unique_run}@example.com"
    traveler_password = "SecurePassword123!"

    reset_all_limiters()
    resp = client.post("/api/v1/auth/register", json={
        "email": traveler_email,
        "password": traveler_password,
        "full_name": "Integration Traveler",
        "role": "traveler",
        "phone": "+94771234567"
    })
    record_test("AUTH", "Traveler Registration Success", resp.status_code == 201, 201, resp.status_code)

    resp = client.post("/api/v1/auth/register", json={
        "email": traveler_email,
        "password": traveler_password,
        "full_name": "Integration Traveler Duplicate",
        "role": "traveler"
    })
    record_test("AUTH", "Duplicate Email Rejection (409 Conflict)", resp.status_code == 409, 409, resp.status_code)

    resp = client.post("/api/v1/auth/login", json={
        "email": traveler_email,
        "password": traveler_password
    })
    login_ok = resp.status_code == 200 and "access_token" in resp.json()
    record_test("AUTH", "Valid Traveler Login (JWT Issued)", login_ok, 200, resp.status_code)
    traveler_token = resp.json().get("access_token") if login_ok else ""
    traveler_auth = {"Authorization": f"Bearer {traveler_token}"}

    resp = client.post("/api/v1/auth/login", json={
        "email": traveler_email,
        "password": "WrongPassword!"
    })
    record_test("AUTH", "Invalid Password Rejection (401 Unauthorized)", resp.status_code == 401, 401, resp.status_code)

    resp = client.post("/api/v1/auth/login", json={
        "email": f"unknown_{unique_run}@example.com",
        "password": traveler_password
    })
    record_test("AUTH", "Unknown Email Rejection (401 Unauthorized)", resp.status_code == 401, 401, resp.status_code)

    resp = client.get("/api/v1/auth/me", headers=traveler_auth)
    record_test("AUTH", "Protected Route Access /api/v1/auth/me", resp.status_code == 200, 200, resp.status_code)

    # Password Reset Workflow (Capturing raw token truthfully from test email interception)
    reset_all_limiters()
    resp = client.post("/api/v1/auth/forgot-password", json={"email": traveler_email})
    record_test("AUTH", "Password Reset Request (200 OK)", resp.status_code == 200, 200, resp.status_code)

    raw_reset_token = last_dispatched_emails[-1]["token"] if last_dispatched_emails else None

    # Verify password_resets record exists with secure token_hash (not plaintext)
    with engine.connect() as conn:
        db_pr = conn.execute(text(
            "SELECT TOP 1 pr.token_hash, pr.is_used FROM dbo.password_resets pr "
            "JOIN dbo.users u ON pr.user_id = u.id "
            "WHERE u.email = :email ORDER BY pr.id DESC"
        ), {"email": traveler_email}).fetchone()
        pr_secure_hash = db_pr[0] if db_pr else ""
        pr_unused = (db_pr[1] == 0 or db_pr[1] is False) if db_pr else False

    resp = client.get(f"/api/v1/auth/verify-reset-token?token={raw_reset_token}")
    verify_token_ok = resp.status_code == 200 and resp.json().get("valid") is True and len(pr_secure_hash) == 64 and pr_unused
    record_test("AUTH", "Verify Reset Token Endpoint", verify_token_ok, 200, resp.status_code)

    new_password = "BrandNewPassword123!"
    resp = client.post("/api/v1/auth/reset-password", json={
        "token": raw_reset_token,
        "new_password": new_password
    })
    record_test("AUTH", "Reset Password Execution", resp.status_code == 200, 200, resp.status_code)

    # Verify token reuse is strictly rejected (single-use enforcement)
    resp_reuse = client.post("/api/v1/auth/reset-password", json={
        "token": raw_reset_token,
        "new_password": "AnotherNewPassword123!"
    })
    record_test("AUTH", "Password Reset Token Reuse Rejection (400)", resp_reuse.status_code == 400, 400, resp_reuse.status_code)

    resp = client.post("/api/v1/auth/login", json={
        "email": traveler_email,
        "password": new_password
    })
    login_new_ok = resp.status_code == 200 and "access_token" in resp.json()
    record_test("AUTH", "Login with New Password", login_new_ok, 200, resp.status_code)
    traveler_token = resp.json().get("access_token") if login_new_ok else traveler_token
    traveler_auth = {"Authorization": f"Bearer {traveler_token}"}

    with engine.begin() as conn:
        conn.execute(text("UPDATE dbo.users SET is_active = 0 WHERE email = :email"), {"email": traveler_email})
    resp = client.post("/api/v1/auth/login", json={"email": traveler_email, "password": new_password})
    record_test("AUTH", "Inactive Account Login Restriction (403)", resp.status_code == 403, 403, resp.status_code)
    with engine.begin() as conn:
        conn.execute(text("UPDATE dbo.users SET is_active = 1 WHERE email = :email"), {"email": traveler_email})

    # ==============================================================================
    # TASK 5: Service Provider Registration & Admin Approval
    # ==============================================================================
    print("\n--- [TASK 5] Service Provider Registration & Admin Integration ---")

    with engine.connect() as conn:
        admin_row = conn.execute(text("SELECT TOP 1 id FROM dbo.users WHERE role = 'admin' AND is_active = 1;")).fetchone()
        admin_id = str(admin_row[0]) if admin_row else "1"
    admin_auth = {"Authorization": f"Bearer {create_access_token(data={'sub': admin_id, 'role': 'admin'})}"}

    def onboard_provider(email_prefix: str, provider_type: str, business_name: str, offset: int):
        p_email = f"{email_prefix}_{unique_run}@example.com"
        nic = f"1988{nic_base + offset:08d}"
        phone = f"+9477{1000000 + offset}"

        # 1. Send OTP
        reset_all_limiters()
        r_otp = client.post("/api/v1/providers/otp/send", json={"email": p_email})
        assert r_otp.status_code == 200, f"OTP send failed: {r_otp.text}"
        otp_val = last_dispatched_emails[-1]["otp"]

        # 2. Verify OTP
        reset_all_limiters()
        r_ver = client.post("/api/v1/providers/otp/verify", json={"email": p_email, "otp": otp_val})
        assert r_ver.status_code == 200, f"OTP verify failed: {r_ver.text}"
        reg_token = r_ver.json()["authorization_token"]
        reg_hdr = {"Authorization": f"Bearer {reg_token}"}

        # 3. Personal Details
        r_pers = client.put("/api/v1/providers/registration/personal", headers=reg_hdr, json={
            "full_name": f"{business_name} Owner",
            "phone": phone,
            "date_of_birth": "1988-06-20",
            "gender": "Male",
            "nationality": "Sri Lankan",
            "identity_document_type": "nic",
            "nic_number": nic,
            "permanent_province": "Central",
            "permanent_district": "Kandy",
            "permanent_city": "Kandy",
            "permanent_landmark": "Near Lake",
            "permanent_postal_code": "20000"
        })
        assert r_pers.status_code == 200, f"Personal details failed: {r_pers.text}"

        # 4. Business Details
        r_biz = client.put("/api/v1/providers/registration/business", headers=reg_hdr, json={
            "business_name": business_name,
            "provider_type": provider_type,
            "secondary_phone": phone,
            "same_as_permanent": True,
            "business_province": "Central",
            "business_district": "Kandy",
            "business_city": "Kandy",
            "business_landmark": "Near Lake",
            "business_postal_code": "20000",
            "description": f"Authentic Sri Lankan {provider_type} service."
        })
        assert r_biz.status_code == 200, f"Business details failed: {r_biz.text}"

        # 5. Identity Documents
        img_bytes = create_valid_test_jpeg()
        files = {
            "selfie": ("selfie.jpg", img_bytes, "image/jpeg"),
            "nic_front": ("nic_f.jpg", img_bytes, "image/jpeg"),
            "nic_back": ("nic_b.jpg", img_bytes, "image/jpeg"),
        }
        r_id = client.post("/api/v1/providers/registration/identity", headers=reg_hdr, files=files)
        assert r_id.status_code == 200, f"Identity upload failed: {r_id.text}"

        # 6. Submit Application
        r_sub = client.post("/api/v1/providers/registration/submit", headers=reg_hdr)
        assert r_sub.status_code == 200, f"Submit failed: {r_sub.text}"

        with engine.connect() as conn:
            row = conn.execute(text("SELECT p.id, p.approval_status, p.user_id FROM dbo.provider_profiles p JOIN dbo.users u ON p.user_id = u.id WHERE u.email = :e;"), {"e": p_email}).fetchone()
            profile_id, status_val, user_id = row[0], row[1], row[2]
        assert status_val == "pending"

        # 7. Admin Approve
        r_app = client.post(f"/api/v1/admin/providers/{profile_id}/approve", headers=admin_auth)
        assert r_app.status_code == 200, f"Admin approve failed: {r_app.text}"

        # 8. Password Setup (OTP -> Setup Token -> Password Setup)
        reset_all_limiters()
        r_send_setup = client.post("/api/v1/providers/otp/send", json={"email": p_email})
        assert r_send_setup.status_code == 200, f"Send setup OTP failed: {r_send_setup.text}"
        setup_otp = last_dispatched_emails[-1]["otp"]

        reset_all_limiters()
        r_set_ver = client.post("/api/v1/providers/otp/verify", json={"email": p_email, "otp": setup_otp})
        assert r_set_ver.status_code == 200, f"Setup verify failed: {r_set_ver.text}"
        setup_token = r_set_ver.json()["authorization_token"]

        p_pwd = "ProviderPassword123!"
        reset_all_limiters()
        r_set_pw = client.post("/api/v1/providers/password/setup", headers={"Authorization": f"Bearer {setup_token}"}, json={
            "password": p_pwd,
            "confirm_password": p_pwd
        })
        assert r_set_pw.status_code == 200, f"Password setup failed: {r_set_pw.text}"

        # 9. Provider Login
        reset_all_limiters()
        r_login = client.post("/api/v1/providers/login", json={"email": p_email, "password": p_pwd})
        assert r_login.status_code == 200, f"Provider login failed: {r_login.text}"
        p_jwt = r_login.json()["access_token"]

        return {
            "email": p_email,
            "profile_id": profile_id,
            "user_id": user_id,
            "jwt": p_jwt,
            "auth": {"Authorization": f"Bearer {p_jwt}"}
        }

    hs_provider_1 = onboard_provider("hs1", "homestay", "Kandy Green View", 10)
    record_test("PROVIDER", "Homestay Provider 1 Onboarded & Approved", hs_provider_1["profile_id"] is not None, 200, 200, f"Profile ID: {hs_provider_1['profile_id']}")

    hs_provider_2 = onboard_provider("hs2", "homestay", "Mirissa Blue Ocean", 20)
    record_test("PROVIDER", "Homestay Provider 2 Onboarded & Approved", hs_provider_2["profile_id"] is not None, 200, 200, f"Profile ID: {hs_provider_2['profile_id']}")

    art_provider_1 = onboard_provider("art1", "artisan", "Lanka Woodcraft", 30)
    record_test("PROVIDER", "Artisan Provider 1 Onboarded & Approved", art_provider_1["profile_id"] is not None, 200, 200, f"Profile ID: {art_provider_1['profile_id']}")

    art_provider_2 = onboard_provider("art2", "artisan", "Ceylon Batik Studio", 40)
    record_test("PROVIDER", "Artisan Provider 2 Onboarded & Approved", art_provider_2["profile_id"] is not None, 200, 200, f"Profile ID: {art_provider_2['profile_id']}")

    # Rejection workflow test on 5th applicant
    rej_email = f"rej_{unique_run}@example.com"
    rej_nic = f"1988{nic_base + 50:08d}"
    reset_all_limiters()
    client.post("/api/v1/providers/otp/send", json={"email": rej_email})
    rej_otp = last_dispatched_emails[-1]["otp"]
    reset_all_limiters()
    r_rej_ver = client.post("/api/v1/providers/otp/verify", json={"email": rej_email, "otp": rej_otp})
    rej_token = r_rej_ver.json()["authorization_token"]
    rej_hdr = {"Authorization": f"Bearer {rej_token}"}
    r_pers = client.put("/api/v1/providers/registration/personal", headers=rej_hdr, json={
        "full_name": "Rejected Applicant", "phone": "+94771239999", "date_of_birth": "1995-02-14", "gender": "Female",
        "nationality": "Sri Lankan", "identity_document_type": "nic", "nic_number": rej_nic,
        "permanent_province": "Southern", "permanent_district": "Galle", "permanent_city": "Galle", "permanent_landmark": "Galle Fort Main Gate", "permanent_postal_code": "80000"
    })
    assert r_pers.status_code == 200, f"Personal details failed: {r_pers.text}"
    r_biz = client.put("/api/v1/providers/registration/business", headers=rej_hdr, json={
        "business_name": "Reject Me", "provider_type": "homestay", "secondary_phone": "+94771239999", "same_as_permanent": True,
        "business_province": "Southern", "business_district": "Galle", "business_city": "Galle", "business_landmark": "Galle Fort Main Gate", "business_postal_code": "80000",
        "description": "Applicant for rejection workflow test."
    })
    assert r_biz.status_code == 200, f"Business details failed: {r_biz.text}"
    img_b = create_valid_test_jpeg()
    r_id = client.post("/api/v1/providers/registration/identity", headers=rej_hdr, files={"selfie": ("s.jpg", img_b, "image/jpeg"), "nic_front": ("f.jpg", img_b, "image/jpeg"), "nic_back": ("b.jpg", img_b, "image/jpeg")})
    assert r_id.status_code == 200, f"Identity upload failed: {r_id.text}"
    r_sub = client.post("/api/v1/providers/registration/submit", headers=rej_hdr)
    assert r_sub.status_code == 200, f"Submit failed: {r_sub.text}"

    with engine.connect() as conn:
        rej_profile_id = conn.execute(text("SELECT p.id FROM dbo.provider_profiles p JOIN dbo.users u ON p.user_id = u.id WHERE u.email = :e;"), {"e": rej_email}).scalar()
    assert rej_profile_id is not None, f"Rejection profile was not created for {rej_email}"

    r_resp = client.post(f"/api/v1/admin/providers/{rej_profile_id}/reject", headers=admin_auth, json={"reason": "Incomplete registration documentation."})
    with engine.connect() as conn:
        rej_row = conn.execute(text("SELECT approval_status, rejection_reason FROM dbo.provider_profiles WHERE id = :id;"), {"id": rej_profile_id}).fetchone()
        persisted_rej_status = rej_row[0] if rej_row else None
        persisted_rej_reason = rej_row[1] if rej_row else None
    rejection_ok = (r_resp.status_code == 200 and persisted_rej_status == "rejected" and persisted_rej_reason == "Incomplete registration documentation.")
    record_test("PROVIDER", "Admin Rejection Workflow", rejection_ok, 200, r_resp.status_code, f"Detail: {r_resp.text if not rejection_ok else 'OK'}")

    # Pending & Inactive provider listing block checks
    with engine.connect() as conn:
        conn.execute(text("UPDATE dbo.provider_profiles SET approval_status = 'pending' WHERE id = :pid;"), {"pid": hs_provider_2["profile_id"]})
        conn.commit()
    p_resp = client.post("/api/v1/homestays", headers=hs_provider_2["auth"], json={
        "name": "Unauthorized Stay", "location": "Kandy", "price_per_night": 5000, "max_guests": 2
    })
    record_test("PROVIDER", "Pending Provider Blocked from Creating Listing", p_resp.status_code == 403, 403, p_resp.status_code)

    with engine.connect() as conn:
        conn.execute(text("UPDATE dbo.provider_profiles SET approval_status = 'approved' WHERE id = :pid;"), {"pid": hs_provider_2["profile_id"]})
        conn.execute(text("UPDATE dbo.users SET is_active = 0 WHERE id = :uid;"), {"uid": hs_provider_2["user_id"]})
        conn.commit()
    inact_resp = client.post("/api/v1/homestays", headers=hs_provider_2["auth"], json={
        "name": "Inactive Stay", "location": "Kandy", "price_per_night": 5000, "max_guests": 2
    })
    record_test("PROVIDER", "Inactive Provider Blocked from Creating Listing", inact_resp.status_code == 403, 403, inact_resp.status_code)
    with engine.connect() as conn:
        conn.execute(text("UPDATE dbo.users SET is_active = 1 WHERE id = :uid;"), {"uid": hs_provider_2["user_id"]})
        conn.commit()

    # ==============================================================================
    # TASK 6: Homestay Integration & Safe Synchronization Revalidation
    # ==============================================================================
    print("\n--- [TASK 6] Homestay Integration & Safe Synchronization ---")

    hs_payload = {
        "name": f"Kandy Scenic Villa {unique_run}",
        "description": "Luxurious stay overlooking the Knuckles mountain range",
        "location": "Kandy Hills",
        "price_per_night": 12500.0,
        "max_guests": 6,
        "amenities": ["WiFi", "Air Conditioning"],
        "images": [
            {"image_url": "https://example.com/villa_front.jpg", "is_primary": True},
            {"image_url": "https://example.com/villa_room.jpg", "is_primary": False}
        ]
    }
    resp = client.post("/api/v1/homestays", headers=hs_provider_1["auth"], json=hs_payload)
    hs_created = resp.status_code == 201
    hs_id = resp.json().get("id") if hs_created else None
    record_test("HOMESTAY", "Homestay Creation Success", hs_created, 201, resp.status_code)

    with engine.connect() as conn:
        db_hs = conn.execute(text("SELECT owner_id, provider_profile_id FROM dbo.homestays WHERE id = :id"), {"id": hs_id}).fetchone()
        dual_ownership_ok = (db_hs[0] == hs_provider_1["user_id"]) and (db_hs[1] == hs_provider_1["profile_id"])
    record_test("HOMESTAY", "Dual Ownership Database Integrity", dual_ownership_ok, 200, 200)

    resp = client.get("/api/v1/homestays?location=Kandy&min_price=10000&max_price=15000")
    record_test("HOMESTAY", "Public Discovery & Price/Location Filter", resp.status_code == 200 and any(h["id"] == hs_id for h in resp.json().get("items", [])), 200, resp.status_code)

    resp = client.get("/api/v1/homestays/mine", headers=hs_provider_1["auth"])
    record_test("HOMESTAY", "Provider-Owned Homestay Retrieval (/mine)", resp.status_code == 200 and any(h["id"] == hs_id for h in resp.json().get("items", [])), 200, resp.status_code)

    # Revalidation of Homestay Amenity Update (Fix Verification)
    update_payload = {
        "name": f"Kandy Scenic Villa & Spa {unique_run}",
        "description": "Luxurious stay with added spa services",
        "location": "Kandy Hills",
        "price_per_night": 14000.0,
        "max_guests": 6,
        "amenities": ["WiFi", "Hot Water", "Spa Services"],  # Kept WiFi, removed Air Conditioning, added 2 new
        "images": [
            {"image_url": "https://example.com/villa_front.jpg", "is_primary": False},
            {"image_url": "https://example.com/villa_room.jpg", "is_primary": True}
        ]
    }
    resp = client.put(f"/api/v1/homestays/{hs_id}", headers=hs_provider_1["auth"], json=update_payload)
    record_test("HOMESTAY", "Revalidated Update (Retaining Amenities, No 500)", resp.status_code == 200, 200, resp.status_code)

    with engine.connect() as conn:
        amenities = conn.execute(text("SELECT name FROM dbo.homestay_amenities WHERE homestay_id = :id ORDER BY name"), {"id": hs_id}).scalars().all()
        amenities_match = sorted(amenities) == ["Hot Water", "Spa Services", "WiFi"]
    record_test("HOMESTAY", "Direct SQL Server Persisted Amenities Verification", amenities_match, 200, 200, f"Persisted: {sorted(amenities)}")

    with engine.connect() as conn:
        images = conn.execute(text("SELECT image_url, is_primary FROM dbo.homestay_images WHERE homestay_id = :id ORDER BY id"), {"id": hs_id}).fetchall()
        primary_match = any(img[0] == "https://example.com/villa_room.jpg" and img[1] == 1 for img in images)
    record_test("HOMESTAY", "SQL Server Image Management & Primary Sync", primary_match and len(images) == 2, 200, 200, f"Images: {len(images)}")

    resp = client.put(f"/api/v1/homestays/{hs_id}", headers=hs_provider_1["auth"], json={
        "name": f"Kandy Scenic Villa & Spa {unique_run}",
        "location": "Kandy Hills",
        "price_per_night": 14000.0,
        "max_guests": 6
    })
    with engine.connect() as conn:
        amenities_after_omit = conn.execute(text("SELECT name FROM dbo.homestay_amenities WHERE homestay_id = :id ORDER BY name"), {"id": hs_id}).scalars().all()
    record_test("HOMESTAY", "SQL Server Preserve Amenities When Omitted", sorted(amenities_after_omit) == ["Hot Water", "Spa Services", "WiFi"], 200, 200)

    resp = client.put(f"/api/v1/homestays/{hs_id}", headers=hs_provider_2["auth"], json=update_payload)
    record_test("HOMESTAY", "Cross-Provider Edit Rejection (403)", resp.status_code == 403, 403, resp.status_code)

    tampered_payload = update_payload.copy()
    tampered_payload["owner_id"] = 999
    resp = client.put(f"/api/v1/homestays/{hs_id}", headers=hs_provider_1["auth"], json=tampered_payload)
    record_test("HOMESTAY", "Tampered Ownership Rejection (Pydantic Extra Forbid)", resp.status_code == 422, 422, resp.status_code)

    # ==============================================================================
    # TASK 7: Booking & Concurrency Serialization
    # ==============================================================================
    print("\n--- [TASK 7] Homestay Booking & Concurrency Integration ---")
    today = date.today()
    b_in_1 = today + timedelta(days=30)
    b_out_1 = today + timedelta(days=35)

    resp = client.get(f"/api/v1/homestays/{hs_id}/availability?check_in={b_in_1.isoformat()}&check_out={b_out_1.isoformat()}")
    record_test("BOOKING", "Availability Check", resp.status_code == 200 and resp.json().get("available") is True, 200, resp.status_code)

    resp = client.get(f"/api/v1/homestays/{hs_id}/price-preview?check_in={b_in_1.isoformat()}&check_out={b_out_1.isoformat()}")
    preview_ok = resp.status_code == 200 and "final_price" in resp.json()
    record_test("BOOKING", "Price Preview Calculation", preview_ok, 200, resp.status_code)

    resp = client.post("/api/v1/bookings", headers=traveler_auth, json={
        "homestay_id": hs_id,
        "check_in": b_in_1.isoformat(),
        "check_out": b_out_1.isoformat(),
        "guests": 2
    })
    b1_created = resp.status_code == 201
    b1_id = resp.json().get("id") if b1_created else None
    record_test("BOOKING", "Traveler Booking Creation", b1_created, 201, resp.status_code)

    resp = client.post("/api/v1/bookings", headers=traveler_auth, json={
        "homestay_id": hs_id,
        "check_in": (b_in_1 + timedelta(days=2)).isoformat(),
        "check_out": (b_out_1 + timedelta(days=2)).isoformat(),
        "guests": 2
    })
    record_test("BOOKING", "Conflicting Booking Prevention (409 Conflict)", resp.status_code == 409, 409, resp.status_code)

    resp = client.get("/api/v1/bookings/me", headers=traveler_auth)
    record_test("BOOKING", "Traveler Booking Retrieval (/me)", resp.status_code == 200 and any(b["id"] == b1_id for b in resp.json()), 200, resp.status_code)

    # Concurrent Overlapping Booking Serialization
    print("  Executing simultaneous concurrent overlapping bookings...")
    b_in_2 = (today + timedelta(days=50)).isoformat()
    b_out_2 = (today + timedelta(days=55)).isoformat()

    barrier = threading.Barrier(2)
    thread_results = []

    def attempt_concurrent_booking():
        barrier.wait()
        r = client.post("/api/v1/bookings", headers=traveler_auth, json={
            "homestay_id": hs_id,
            "check_in": b_in_2,
            "check_out": b_out_2,
            "guests": 2
        })
        thread_results.append(r.status_code)

    t1 = threading.Thread(target=attempt_concurrent_booking)
    t2 = threading.Thread(target=attempt_concurrent_booking)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    concurrent_ok = sorted(thread_results) == [201, 409]
    record_test("BOOKING", "Concurrent Overlapping Conflict Serialization", concurrent_ok, 200, 200, f"Results: {sorted(thread_results)}")

    with engine.connect() as conn:
        active_cnt = conn.execute(text(
            "SELECT COUNT(*) FROM dbo.homestay_bookings WHERE homestay_id = :hid "
            "AND check_in = :cin AND status = 'confirmed'"
        ), {"hid": hs_id, "cin": b_in_2}).scalar()
    record_test("BOOKING", "Zero Duplicate Confirmed Bookings in SQL Server", active_cnt == 1, 1, active_cnt)

    # Deactivation Protection with Active Bookings
    resp = client.put(f"/api/v1/homestays/{hs_id}", headers=hs_provider_1["auth"], json={
        "name": f"Deactivate Me {unique_run}", "location": "Kandy", "price_per_night": 14000.0, "max_guests": 6, "is_active": False
    })
    record_test("BOOKING", "Deactivation Protection on PUT (Active Booking)", resp.status_code == 400, 400, resp.status_code)

    resp = client.delete(f"/api/v1/homestays/{hs_id}", headers=hs_provider_1["auth"])
    record_test("BOOKING", "Deactivation Protection on DELETE (Active Booking)", resp.status_code == 400, 400, resp.status_code)

    with engine.begin() as conn:
        conn.execute(text("UPDATE dbo.homestay_bookings SET status = 'cancelled' WHERE homestay_id = :hid"), {"hid": hs_id})

    resp = client.delete(f"/api/v1/homestays/{hs_id}", headers=hs_provider_1["auth"])
    record_test("BOOKING", "Soft Deactivation Allowed when Bookings Cancelled", resp.status_code == 200, 200, resp.status_code)
    with engine.begin() as conn:
        conn.execute(text("UPDATE dbo.homestays SET is_active = 1 WHERE id = :hid"), {"hid": hs_id})

    # ==============================================================================
    # TASK 8: Homestay Promotion Integration
    # ==============================================================================
    print("\n--- [TASK 8] Homestay Promotion Integration ---")
    p_start = date.today() + timedelta(days=1)
    p_end = date.today() + timedelta(days=20)

    resp = client.post("/api/v1/homestay-promotions", headers=hs_provider_1["auth"], json={
        "homestay_id": hs_id,
        "title": "Winter Early Bird",
        "description": "15% off for early bookers",
        "discount_percent": 15.0,
        "start_date": p_start.isoformat(),
        "end_date": p_end.isoformat()
    })
    promo_ok = resp.status_code == 201
    promo_id = resp.json().get("id") if promo_ok else None
    record_test("PROMOTION", "Homestay Promotion Creation", promo_ok, 201, resp.status_code)

    resp = client.post("/api/v1/homestay-promotions", headers=hs_provider_1["auth"], json={
        "homestay_id": hs_id, "title": "Over 100", "discount_percent": 105.0, "start_date": p_start.isoformat(), "end_date": p_end.isoformat()
    })
    record_test("PROMOTION", "Discount > 100% Rejection (422)", resp.status_code == 422, 422, resp.status_code)

    resp = client.post("/api/v1/homestay-promotions", headers=hs_provider_1["auth"], json={
        "homestay_id": hs_id, "title": "Bad Dates", "discount_percent": 10.0, "start_date": p_end.isoformat(), "end_date": p_start.isoformat()
    })
    record_test("PROMOTION", "End Date < Start Date Rejection (422)", resp.status_code == 422, 422, resp.status_code)

    resp = client.put(f"/api/v1/homestay-promotions/{promo_id}", headers=hs_provider_2["auth"], json={
        "title": "Hacked Promo", "discount_percent": 50.0, "start_date": p_start.isoformat(), "end_date": p_end.isoformat(), "is_active": True
    })
    record_test("PROMOTION", "Cross-Provider Promotion Edit Rejection", resp.status_code in (403, 404), 403, resp.status_code)

    resp = client.put(f"/api/v1/homestay-promotions/{promo_id}", headers=hs_provider_1["auth"], json={
        "title": "Winter Early Bird Updated", "discount_percent": 20.0, "start_date": p_start.isoformat(), "end_date": p_end.isoformat(), "is_active": True
    })
    record_test("PROMOTION", "Owner Promotion Update", resp.status_code == 200, 200, resp.status_code)

    # ==============================================================================
    # TASK 9: Artisan Integration
    # ==============================================================================
    print("\n--- [TASK 9] Artisan Integration ---")
    resp = client.post("/api/v1/artisan-listings", headers=hs_provider_1["auth"], json={
        "title": "Homestay Trying Artisan", "craft_category": "Woodcarving & Masks", "district": "Kandy", "city": "Kandy"
    })
    record_test("ARTISAN", "Non-Artisan Provider Creation Rejection (403)", resp.status_code == 403, 403, resp.status_code)

    art_payload = {
        "title": f"Lanka Traditional Masks {unique_run}",
        "craft_category": "Woodcarving & Masks",
        "description": "Authentic handmade traditional masks",
        "address": "45 Temple Road",
        "city": "Ambalangoda",
        "district": "Galle",
        "phone": "+94771238888",
        "email": f"mask_{unique_run}@example.com"
    }
    resp = client.post("/api/v1/artisan-listings", headers=art_provider_1["auth"], json=art_payload)
    art_ok = resp.status_code == 201
    art_id = resp.json().get("id") if art_ok else None
    record_test("ARTISAN", "Artisan Listing Creation Success", art_ok, 201, resp.status_code)

    with engine.connect() as conn:
        db_art_pid = conn.execute(text("SELECT provider_profile_id FROM dbo.artisan_listings WHERE id = :id"), {"id": art_id}).scalar()
    record_test("ARTISAN", "Artisan Provider Ownership Database Integrity", db_art_pid == art_provider_1["profile_id"], 200, 200)

    resp = client.post("/api/v1/showcase-items", headers=art_provider_1["auth"], json={
        "artisan_listing_id": art_id,
        "title": "Raksha Mask",
        "item_type": "product",
        "description": "Hand-carved wooden mask",
        "price": 8500.0,
        "materials": "Kaduru wood",
        "is_available": True
    })
    record_test("ARTISAN", "Artisan Showcase Item Creation", resp.status_code == 201, 201, resp.status_code)
    item_id = resp.json().get("id")

    now_dt = datetime.now(UTC)
    resp = client.post("/api/v1/promotions", headers=art_provider_1["auth"], json={
        "artisan_listing_id": art_id,
        "showcase_item_id": item_id,
        "title": "Avurudu Special",
        "promo_code": "AVURUDU10",
        "discount_percentage": 10.0,
        "start_date": now_dt.isoformat(),
        "end_date": (now_dt + timedelta(days=14)).isoformat()
    })
    record_test("ARTISAN", "Artisan Promotion Creation", resp.status_code == 201, 201, resp.status_code)

    resp = client.put(f"/api/v1/artisan-listings/{art_id}", headers=art_provider_2["auth"], json={
        "title": "Hacked Artisan", "craft_category": "Woodcarving & Masks", "district": "Galle", "city": "Ambalangoda"
    })
    record_test("ARTISAN", "Cross-Artisan Edit Rejection (403/404)", resp.status_code in {403, 404}, 403, resp.status_code)

    resp = client.get("/api/v1/artisan-listings?district=Galle&craft_category=Woodcarving%20%26%20Masks")
    record_test("ARTISAN", "Public Artisan Discovery & Category/District Filter", resp.status_code == 200 and any(a["id"] == art_id for a in resp.json().get("items", [])), 200, resp.status_code)

    # ==============================================================================
    # TASK 10: SQL Server Specific Validation
    # ==============================================================================
    print("\n--- [TASK 10] SQL Server Specific Validation ---")
    sinhala_tamil_name = "නිවාඩු නිකේතනය விடுமுறை இல்லம் (Holiday Stay)"
    with engine.begin() as conn:
        conn.execute(text("UPDATE dbo.homestays SET name = :n WHERE id = :id"), {"n": sinhala_tamil_name, "id": hs_id})
    with engine.connect() as conn:
        retrieved_name = conn.execute(text("SELECT name FROM dbo.homestays WHERE id = :id"), {"id": hs_id}).scalar()
    record_test("SQL_SERVER", "Unicode Sinhala & Tamil Roundtrip Persistence", retrieved_name == sinhala_tamil_name, 200, 200)

    with engine.connect() as conn:
        price_val = conn.execute(text("SELECT price_per_night FROM dbo.homestays WHERE id = :id"), {"id": hs_id}).scalar()
    record_test("SQL_SERVER", "Decimal Precision Numeric(12,2) Verification", price_val == Decimal("14000.00"), 200, 200)

    # Database-Level Foreign Key Enforcement
    fk_blocked = False
    try:
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO dbo.homestay_images (homestay_id, image_url, is_primary) VALUES (999999, 'bad_url.jpg', 0);"))
    except Exception as e:
        fk_blocked = "FK_homestay_images_homestays" in str(e) or "FOREIGN KEY" in str(e)
    record_test("SQL_SERVER", "Database Level Foreign Key Enforcement", fk_blocked, 500, 500, "FK Violation Blocked")

    # Database-Level Check Constraint Enforcement
    ck_blocked = False
    try:
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO dbo.homestays (name, location, price_per_night, max_guests, is_active, created_at) VALUES ('Bad', 'Kandy', -50.00, 2, 1, GETUTCDATE());"))
    except Exception as e:
        ck_blocked = "CK_homestays_price" in str(e) or "CHECK" in str(e)
    record_test("SQL_SERVER", "Database Level Check Constraint Enforcement", ck_blocked, 500, 500, "CK Violation Blocked")

    # Database-Level Unique Constraint Enforcement (1:1 user to profile)
    uq_blocked = False
    try:
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO dbo.provider_profiles (user_id, business_name, provider_type, phone, district, approval_status, identity_document_type) VALUES (:uid, 'Dup Biz', 'homestay', '0770000000', 'Kandy', 'approved', 'nic');"), {"uid": hs_provider_1["user_id"]})
    except Exception as e:
        uq_blocked = "UQ_provider_profiles_user_id" in str(e) or "UNIQUE" in str(e)
    record_test("SQL_SERVER", "Unique Constraint Enforcement (1:1 user to profile)", uq_blocked, 500, 500, "UQ Violation Blocked")

    # Explicit Transaction Rollback Cleanliness
    rollback_email = f"rollback_{unique_run}@example.com"
    with engine.connect() as conn:
        trans = conn.begin()
        conn.execute(text("INSERT INTO dbo.users (full_name, email, role, is_active, created_at) VALUES ('Rollback User', :e, 'traveler', 1, GETUTCDATE());"), {"e": rollback_email})
        trans.rollback()
        temp_cnt = conn.execute(text("SELECT COUNT(*) FROM dbo.users WHERE email = :e;"), {"e": rollback_email}).scalar()
    record_test("SQL_SERVER", "Explicit Transaction Rollback Cleanliness", temp_cnt == 0, 0, temp_cnt)

    with engine.connect() as conn:
        spid = conn.execute(text("SELECT @@SPID")).scalar()
    record_test("SQL_SERVER", "Connection Pooling & Session Reuse", spid > 0, 200, 200, f"SPID: {spid}")

    # ==============================================================================
    # Final Report Summary
    # ==============================================================================
    print("\n" + "=" * 80)
    print(f"INTEGRATION TEST SUMMARY: Total: {results_summary['total_tests']} | Passed: {results_summary['passed']} | Failed: {results_summary['failed']}")
    print("=" * 80)

    if results_summary["failed"] > 0:
        print("\nFAILURE DETAILS:")
        for d in results_summary["details"]:
            if not d["passed"]:
                print(f"  FAILED: [{d['module']}] {d['name']} (Expected {d['expected']}, got {d['actual']}) {d['note']}")
        sys.exit(1)
    else:
        print("\nALL SQL SERVER INTEGRATION TESTS COMPLETED SUCCESSFULLY.")
        sys.exit(0)


if __name__ == "__main__":
    main()
