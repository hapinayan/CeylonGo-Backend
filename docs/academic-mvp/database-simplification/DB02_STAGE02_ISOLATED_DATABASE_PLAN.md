# DB-02 Stage 02 isolated database plan

No connection or database mutation was performed or approved during Stage 01.

1. Confirm Adventure and Admin scope, required vehicle documents, clearance/cancellation policy and demo payment state mapping. Extend offline tracing to worker execution with injected sessions, vehicle approval, native Eco safety/cancellation and scenario-specific demo payment tests once implemented.
2. Separately approve a named disposable SQL Server reference target and restricted identity. Begin with read-only instance, database, permission, catalog and ledger preflight. Protect main/shared databases and use synthetic records.
3. Seek separate approval for backup/restore rehearsal, the sanctioned pre-001 users bootstrap, sequential 001?043 replay and write-capable integration tests. Validate normalized checksums, both user version columns, FK and check trust, indexes, cascade behavior, object and permission drift. Stop on failure. Preserve the ledger and existing migration files.
4. Validate SQL Server transaction locking, concurrent last-unit booking, cash/demonstration payment idempotency, evidence retention, authorization and rollback on the full reference schema. Propose a separate reduced disposable schema only after tracing retained callers, routes and workers and approving its own initialization lineage.
5. On failure stop writes, capture sanitized evidence and restore to a new isolated target using the rehearsed restore. Reconcile intervening writes and failed ledger state before restart. Existing schemas, records and historical migrations stay intact.

See [DB-01 migration and recovery strategy](DB01_SAFE_MIGRATION_STRATEGY.md).
