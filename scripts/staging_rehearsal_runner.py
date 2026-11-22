"""
CeylonGo - Staging Migration Rehearsal Runner Wrapper
=====================================================
Wrapper around scripts/migrate.py that enforces strict fail-closed safety guards
ensuring that migration rehearsal commands can ONLY execute against
the isolated staging database: CeylonGoDB_Migration_Staging.

Prevents any accidental execution against CeylonGoDB_Development or CeylonGoDB.
Strictly guards subprocess execution against argument injection and unbounded upgrades.
Enforces an immutable rehearsal ceiling of migration 040 (strictly 030-040).
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from scripts.staging_guard import (
    StagingDatabaseSafetyError,
    verify_connected_staging_identity,
    verify_safe_staging_database_name,
)

# Immutably pinned staging database
IMMUTABLE_STAGING_DB = "CeylonGoDB_Migration_Staging"

# Strict allowlist of permitted migration runner subcommands
ALLOWED_SUBCOMMANDS = frozenset({"status", "plan", "upgrade", "validate"})

# Strict validation regex for migration target version: exactly 3 digits
TARGET_VERSION_REGEX = re.compile(r"^\d{3}$")

# Phase 5D-3C Rehearsal Target Boundaries
MIN_REHEARSAL_TARGET = 30
MAX_REHEARSAL_TARGET = 40


def validate_migration_target(target: str | None, is_execution: bool = False) -> str | None:
    """
    Validates that a migration target version adheres strictly to the Phase 5D-3C rehearsal boundary.

    Rules:
    - Maximum allowed target is 040. Versions 041 and higher are strictly rejected across all commands.
    - If is_execution is True (upgrade requested):
        - Target cannot be None or empty (unbounded upgrade strictly forbidden).
        - Target must be >= 030. Versions below 030 are rejected for execution.
    - If target is supplied, it must strictly match pattern r"^\\d{3}$".
    """
    if target is None or (isinstance(target, str) and not target.strip()):
        if is_execution:
            raise StagingDatabaseSafetyError(
                "MISSING MIGRATION TARGET: Staging database upgrade requires an explicit target "
                "version between 030 and 040. Unbounded upgrades are strictly forbidden."
            )
        return None

    if not isinstance(target, str) or not TARGET_VERSION_REGEX.match(target):
        raise StagingDatabaseSafetyError(
            f"MALFORMED MIGRATION TARGET: '{target}' is not a valid 3-digit version. "
            "Migration targets must match pattern '^\\d{3}$' (e.g. '040')."
        )

    target_int = int(target)

    # 1. Reject 041 and all higher versions across all paths
    if target_int > MAX_REHEARSAL_TARGET:
        raise StagingDatabaseSafetyError(
            f"DISALLOWED MIGRATION TARGET: Target '{target}' exceeds maximum rehearsal ceiling of '040'. "
            "Versions 041 and higher are strictly forbidden during Phase 5D-3C."
        )

    # 2. Reject versions below 030 for execution
    if is_execution and target_int < MIN_REHEARSAL_TARGET:
        raise StagingDatabaseSafetyError(
            f"DISALLOWED MIGRATION TARGET: Target '{target}' is below minimum rehearsal target of '030'. "
            "Rehearsal execution is restricted strictly to migrations 030 through 040."
        )

    return target


def run_staging_command(
    subcommand: str,
    target_version: str | None = None,
    dry_run: bool = False,
    confirmed: bool = False,
    verbose: bool = False,
    no_db: bool = False,
) -> int:
    """
    Executes a migrate.py command strictly pinned to CeylonGoDB_Migration_Staging.
    Enforces pre-flight identity verification, bounded execution, and fail-closed argument sanitization.
    """
    # 1. Guard check on pinned target database name string
    verify_safe_staging_database_name(IMMUTABLE_STAGING_DB)

    # 2. Subcommand allowlist check
    if subcommand not in ALLOWED_SUBCOMMANDS:
        raise StagingDatabaseSafetyError(
            f"DISALLOWED SUBCOMMAND: '{subcommand}' is not in approved list {sorted(ALLOWED_SUBCOMMANDS)}."
        )

    # 3. Target version sanitization & bounded upgrade enforcement
    is_execution = (subcommand == "upgrade")
    clean_target = validate_migration_target(target_version, is_execution=is_execution)

    print("\n" + "=" * 80)
    print(f" CEYLONGO STAGING REHEARSAL RUNNER — Command: {subcommand.upper()}")
    print(f" Target Staging Database: {IMMUTABLE_STAGING_DB} (IMMUTABLE)")
    if clean_target:
        print(f" Bounded Target Version : {clean_target} (Ceiling: 040)")
    print("=" * 80)

    # 4. Check live connection identity if not no_db
    if not no_db:
        from scripts.migrate import DatabaseConfig, get_connection
        config = DatabaseConfig.from_env(database_override=IMMUTABLE_STAGING_DB)
        try:
            conn = get_connection(config)
            try:
                identity = verify_connected_staging_identity(conn)
                print(f"Verified Connected Database: {identity['current_db']} on {identity['server_name']}")
            finally:
                conn.close()
        except StagingDatabaseSafetyError as err:
            print(f"\n[FATAL GUARD ERROR] Live identity safety guard failed: {err}", file=sys.stderr)
            return 1
        except Exception as exc:
            print(f"\n[CONNECTION NOTE] Could not connect to '{IMMUTABLE_STAGING_DB}': {exc}")
            print("Note: If the staging database has not been restored yet, this is expected.")
            if subcommand in ("upgrade", "status"):
                print("[ABORT] Cannot run migration command against non-existent database.")
                return 1

    # 5. Build migrate.py command with IMMUTABLE root-level --database
    cmd = [
        sys.executable,
        str(BACKEND_DIR / "scripts" / "migrate.py"),
        "--database",
        IMMUTABLE_STAGING_DB,
    ]

    if verbose:
        cmd.append("-v")

    cmd.append(subcommand)

    if clean_target:
        cmd.extend(["--target", clean_target])

    if no_db and subcommand == "validate":
        cmd.append("--no-db")

    if dry_run and subcommand == "upgrade":
        cmd.append("--dry-run")

    if confirmed and subcommand in ("upgrade", "baseline"):
        cmd.append("--yes")

    print(f"\nExecuting validated command (shell=False):\n  {' '.join(cmd)}\n")
    proc = subprocess.run(cmd, cwd=str(BACKEND_DIR), shell=False)
    return proc.returncode


def main(argv: list[str] | None = None) -> int:
    raw_args = argv if argv is not None else sys.argv[1:]

    # 1. Disallow database override flags
    for arg in raw_args:
        low = arg.lower()
        if "--database" in low or "--db" in low or low.startswith("-d"):
            print(
                "\n[SECURITY BLOCK] Overriding target database via CLI flags is strictly prohibited. "
                f"Target database is immutably pinned to '{IMMUTABLE_STAGING_DB}'.\n",
                file=sys.stderr,
            )
            return 1

    # 2. Disallow raw argument injection tokens
    for arg in raw_args:
        if any(c in arg for c in [";", "&", "|", "`", "$"]):
            print(
                f"\n[SECURITY BLOCK] Unsafe character detected in CLI argument: '{arg}'. "
                "Argument injection is strictly prohibited.\n",
                file=sys.stderr,
            )
            return 1

    parser = argparse.ArgumentParser(
        prog="staging_rehearsal_runner.py",
        description="CeylonGo Staging Rehearsal Runner (Immutably Pinned to CeylonGoDB_Migration_Staging)",
    )
    parser.add_argument(
        "subcommand",
        choices=["status", "plan", "upgrade", "validate"],
        help="Migration runner subcommand to execute against staging",
    )
    parser.add_argument(
        "--target",
        type=str,
        default=None,
        help="Target migration version (required for upgrade, strictly '030' through '040')",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview upgrade plan without executing DDL",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Explicit confirmation to execute upgrade against staging",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose output",
    )
    parser.add_argument(
        "--no-db",
        action="store_true",
        help="Skip database connection during validate command",
    )

    args = parser.parse_args(raw_args)

    try:
        return run_staging_command(
            subcommand=args.subcommand,
            target_version=args.target,
            dry_run=args.dry_run,
            confirmed=args.yes,
            verbose=args.verbose,
            no_db=args.no_db,
        )
    except StagingDatabaseSafetyError as err:
        print(f"\n[SECURITY BLOCK] Staging Guard rejected request: {err}\n", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
