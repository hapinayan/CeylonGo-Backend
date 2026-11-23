"""
CeylonGo Academic MVP — Unified Schema Bootstrap Script
Applies scripts/bootstrap_mvp_schema.sql to an approved SQL Server database with fail-closed safety guards.
"""

import hashlib
import os
import re
import sys
from pathlib import Path
import pyodbc

# Approved target database names
ALLOWED_TARGETS = {
    "CeylonGoDB_MVP_Rehearsal",
    "CeylonGoDB_IntegrationTest",
    "CeylonGoDB_Rehearsal_V3",
    "CeylonGoDB_Test",
    # Main database allowed ONLY when explicit env var MVP_CUTOVER_AUTHORIZED=true is set
}

def bootstrap(target_db: str, allow_main: bool = False):
    print(f"=== CEYLONGO ACADEMIC MVP SCHEMA BOOTSTRAP ===")
    print(f"Target Database: {target_db}")
    
    if target_db == "CeylonGoDB" and not allow_main:
        raise PermissionError(
            "SAFETY GUARD HALT: Targeting 'CeylonGoDB' requires explicit authorization "
            "(MVP_CUTOVER_AUTHORIZED=true). Halting."
        )
    elif target_db != "CeylonGoDB" and target_db not in ALLOWED_TARGETS:
        raise ValueError(f"Target '{target_db}' is not in the approved database whitelist.")

    sql_path = Path(__file__).resolve().parent / "bootstrap_mvp_schema.sql"
    if not sql_path.exists():
        raise FileNotFoundError(f"Missing DDL script at {sql_path}")

    sql_content = sql_path.read_text(encoding="utf-8")
    sql_hash = hashlib.sha256(sql_content.encode("utf-8")).hexdigest()
    print(f"Loaded {sql_path.name} (SHA-256: {sql_hash})")

    server = os.getenv("DB_SERVER", r".\SQLEXPRESS")
    while "\\\\" in server:
        server = server.replace("\\\\", "\\")

    conn_str = f"DRIVER={{ODBC Driver 18 for SQL Server}};SERVER={server};DATABASE={target_db};Trusted_Connection=yes;TrustServerCertificate=yes;"
    print(f"Connecting to {server}/{target_db}...")
    conn = pyodbc.connect(conn_str, autocommit=True, timeout=60)
    cur = conn.cursor()

    # Split batches on GO
    batches = [b.strip() for b in re.split(r"(?i)^\s*GO\s*$", sql_content, flags=re.MULTILINE) if b.strip()]
    print(f"Executing {len(batches)} SQL batches...")

    for i, batch in enumerate(batches, 1):
        try:
            cur.execute(batch)
        except Exception as e:
            print(f"ERROR executing batch {i}: {e}")
            print(f"Statement preview: {batch[:200]}...")
            raise

    print("All schema DDL batches applied.")

    # Record / Update Ledger
    cur.execute("""
        IF NOT EXISTS (SELECT 1 FROM dbo.mvp_schema_versions WHERE version = '1.0.0')
        BEGIN
            INSERT INTO dbo.mvp_schema_versions (version, artifact_name, sha256, applied_at, applied_by)
            VALUES ('1.0.0', 'bootstrap_mvp_schema.sql', ?, SYSUTCDATETIME(), SUSER_SNAME());
        END
    """, sql_hash)
    print("Version 1.0.0 recorded in dbo.mvp_schema_versions.")

    # Verification counts
    cur.execute("SELECT COUNT(*) FROM sys.tables WHERE schema_id = SCHEMA_ID('dbo')")
    tables_count = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM sys.key_constraints WHERE type = 'PK'")
    pk_count = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM sys.foreign_keys")
    fk_count = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM sys.check_constraints")
    ck_count = cur.fetchone()[0]

    print(f"\nVerification Results:")
    print(f"  Tables:             {tables_count} (Expected 18)")
    print(f"  Primary Keys:       {pk_count} (Expected 18)")
    print(f"  Foreign Keys:       {fk_count}")
    print(f"  Check Constraints:  {ck_count}")

    conn.close()
    print("=== BOOTSTRAP COMPLETE ===")

if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else os.getenv("DB_NAME", "CeylonGoDB_MVP_Rehearsal")
    allow_main_cutover = os.getenv("MVP_CUTOVER_AUTHORIZED", "false").lower() == "true"
    bootstrap(target, allow_main=allow_main_cutover)
