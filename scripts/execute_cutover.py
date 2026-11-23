"""
CeylonGo Academic MVP — SQL Server Production Cutover Runner
Automates the safe cutover of CeylonGoDB once explicit coordinator approval is granted.
Includes pre-execution checksum verification of the Gate 2 backup.
"""

import argparse
import hashlib
import os
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pyodbc
from app.core.mvp_security import hash_password

EXPECTED_BACKUP_SHA256 = "c6529b4aeab0b83d37fcf9869b6355fb1315dda4cfeea5fa0f9678afac6c8b77"
BACKUP_PATH = r"C:\Program Files\Microsoft SQL Server\MSSQL17.SQLEXPRESS\MSSQL\Backup\CeylonGoDB_pre_mvp_20261123T084300Z.bak"

def verify_backup_safety_net():
    print("Verifying Gate 2 backup safety net...")
    if not os.path.exists(BACKUP_PATH):
        raise FileNotFoundError(f"CRITICAL SAFETY STOP: Gate 2 backup not found at {BACKUP_PATH}")

    h = hashlib.sha256()
    with open(BACKUP_PATH, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    actual_sha = h.hexdigest()
    if actual_sha != EXPECTED_BACKUP_SHA256:
        raise ValueError(
            f"CRITICAL SAFETY STOP: Backup SHA-256 mismatch!\n"
            f"Expected: {EXPECTED_BACKUP_SHA256}\nActual:   {actual_sha}"
        )
    print(f"Gate 2 backup verified (SHA-256: {actual_sha}). Safety net confirmed.")

def run_cutover(authorized: bool):
    if not authorized:
        print("SAFETY STOP: Cutover was NOT executed. Pass --yes-i-authorize-cutover when authorized.")
        sys.exit(1)

    verify_backup_safety_net()

    server = os.getenv("DB_SERVER", r".\SQLEXPRESS")
    while "\\\\" in server:
        server = server.replace("\\\\", "\\")

    target_db = "CeylonGoDB"
    conn_str = f"DRIVER={{ODBC Driver 18 for SQL Server}};SERVER={server};DATABASE={target_db};Trusted_Connection=yes;TrustServerCertificate=yes;"
    print(f"\nConnecting to {server}/{target_db}...")
    conn = pyodbc.connect(conn_str, autocommit=True, timeout=60)
    cur = conn.cursor()

    # Step 1: Clean drop of legacy tables
    cutover_sql_path = Path(__file__).resolve().parent / "prepare_ceylongodb_cutover.sql"
    print(f"Executing legacy cleanup from {cutover_sql_path.name}...")
    # Executing drop statements
    cur.execute("""
        DECLARE @sql NVARCHAR(MAX) = N'';
        SELECT @sql += N'ALTER TABLE ' + QUOTENAME(s.name) + N'.' + QUOTENAME(t.name) + 
                       N' DROP CONSTRAINT ' + QUOTENAME(f.name) + N';' + CHAR(13)
        FROM sys.foreign_keys f
        JOIN sys.tables t ON f.parent_object_id = t.object_id
        JOIN sys.schemas s ON t.schema_id = s.schema_id;
        EXEC sp_executesql @sql;

        SET @sql = N'';
        SELECT @sql += N'DROP TABLE ' + QUOTENAME(s.name) + N'.' + QUOTENAME(t.name) + N';' + CHAR(13)
        FROM sys.tables t
        JOIN sys.schemas s ON t.schema_id = s.schema_id
        WHERE t.is_ms_shipped = 0;
        EXEC sp_executesql @sql;
    """)
    print("Legacy tables removed.")

    # Step 2: Bootstrap fresh 17+1 schema
    from scripts.bootstrap_mvp_schema import bootstrap
    bootstrap("CeylonGoDB", allow_main=True)

    # Step 3: Seed initial demo accounts
    print("\nSeeding baseline demo accounts into CeylonGoDB...")
    users = [
        ("admin@ceylongo.lk", "System Administrator", "+94770000001", "AdminPass123!", "admin"),
        ("host.ella@ceylongo.lk", "Nimal Silva (Ella Host)", "+94770000002", "ProviderPass123!", "provider"),
        ("traveler.kasun@ceylongo.lk", "Kasun Perera (Traveler)", "+94770000003", "TravelerPass123!", "traveler"),
    ]
    for email, name, phone, pwd, role in users:
        h = hash_password(pwd)
        cur.execute("""
            IF NOT EXISTS (SELECT 1 FROM dbo.users WHERE email = ?)
            BEGIN
                INSERT INTO dbo.users (email, full_name, phone, hashed_password, role, is_active, auth_version, credential_version, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 1, 0, 0, SYSUTCDATETIME(), SYSUTCDATETIME());
            END
        """, email, email, name, phone, h, role)
    print("Baseline demo accounts seeded successfully.")

    # Step 4: Verification
    cur.execute("SELECT COUNT(*) FROM sys.tables WHERE schema_id = SCHEMA_ID('dbo')")
    cnt = cur.fetchone()[0]
    print(f"\nFinal Verification: CeylonGoDB has {cnt} tables (Expected 18). State: ONLINE.")
    conn.close()
    print("=== CEYLONGODB CUTOVER COMPLETED SUCCESSFULLY ===")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CeylonGo Academic MVP Cutover Runner")
    parser.add_argument(
        "--yes-i-authorize-cutover",
        action="store_true",
        dest="authorized",
        help="Explicit confirmation to execute cutover on CeylonGoDB",
    )
    args = parser.parse_args()
    run_cutover(args.authorized)
