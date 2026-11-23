# DB-01 safe migration strategy

## Isolation and approval

Use two newly named, independently approved scratch targets, proposed names CeylonGoDB_Academic_MVP_Reference_Test and CeylonGoDB_Academic_MVP_Profile_Test. Confirm actual SQL machine and instance, exact database name, status, original and effective login, operation-specific permissions, and migration ledger on every connection. Use a separate restricted verifier from the executor. Deny access to main/shared databases. Use synthetic seed data or an independently approved sanitized export. Protect backups and application media. No connection or execution was performed in DB-01.

## Reproduce the reference

1. Inspect the pinned runner's bootstrap behavior and entry points before any use. Migration 001 expects dbo.users before its FK. Confirm the sanctioned pre-001 users definition and its compatibility with 002, 006, 040 and 043. Do not bulk-create all current ORM tables ahead of historical replay.
2. Verify the ordered 001?043 file inventory and normalized ledger digest rules. Preserve every original file. Take a pre-change backup or verified clean restore point and rehearse restore to a different scratch target. Catalog and backup evidence must be reviewed before write approval.
3. Use the full lineage on the reference database only after approval. Stop immediately on mismatched checksum, unexpected object, permissions, or migration failure. 027 precedes shared booking and payment usage, Community 039 extends 028 and references 027, 041 checks the ledger through 040 and Eco/shared constraints, and 042 requires 041. No skip, fake ledger, baseline shortcut, or automatic retry.
4. Verify exact applied versions and normalized checksums, PKs, FKs, unique/filtered indexes, check trust, default definitions, FK cascade and update actions, plus triggers, views, dependencies and jobs where authorized. Compare ORM and DDL metadata without assuming equal counts. Verify both users version columns and booking/payment ownership constraints.

## Rehearse the database profile separately

Start with a complete schema and limit profile usage through application dependency isolation first. After retained routes and services pass with the allowlisted tables, build a separately approved clean-profile provisioning design with its own auditable lineage. The full legacy migration chain cannot initialize a reduced catalog by skipping excluded tables. Treat switching a live catalog or dropping any existing table as a separate later decision. Compare real executions on isolated targets under synthetic data before any deployment.

Check all four booking types, times and inventory units, duplicate request/retry, concurrent last-unit allocation, rollback, ownership, stale approvals and payment state. Verify moderation evidence, notifications, ORM and DB cascades, expected archive access, and read/write authorization. Compare row counts, parent/child counts, orphan checks, unique keys, status totals, and booking/payment identities before and after each approved run.

## Recovery

On a failure, capture sanitized error, migration version and status. Stop writes. Compare ledger, catalog, and data with the preflight manifest. Recover on a new scratch target using the tested restore procedure. Preserve the failed target for diagnosis where allowed. Do not blindly run a rollback or alter the ledger. Migration 041 widened the Eco refund status constraint, so restoring the old check can fail once new values exist. Prefer a reviewed forward repair or tested restore with explicit reconciliation of intervening writes. Export approval, retention and destruction decisions separately.

Sources: [migration digest and discovery](../../../scripts/migrate.py#L314), [migration ledger DDL](../../../scripts/migrate.py#L594), [migration 001](../../../migrations/001_create_password_resets.sql), [041](../../../migrations/041_extend_eco_native_safety_and_refunds.sql), [042](../../../migrations/042_create_eco_sponsorship_ledger.sql).
