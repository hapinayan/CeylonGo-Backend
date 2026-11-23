# DB-02 implementation plan

## Recommended first batch after approval

Build an offline schema-profile manifest and a test-only access recorder over existing ORM/SQL execution. Fail the proposed profile tests when a retained workflow touches a table outside the approved inventory. Start with user/auth versions, provider approval, one transaction per booking category, cash status, Community moderation and Map catalog. Use an injectable offline SQLite session where compatible, mocked integrations, and strict network/ODBC guards. Compare expected PK/FK/unique/check/index declarations to the profiles. Preserve all mounted API contracts in this batch. If an existing dependency is found, retain it or propose a separately reviewed refactor with concrete parity cases.

## Next gates

1. Approve Adventure's canonical path, the motorized bike licensing scope, transient AI planning, payment demo state mapping and retry behavior, and how shared/archived histories are accessed. Confirm whether any durable itinerary requirement changes the table count.
2. Reconcile Map and Community enrichment dependencies, eligibility, provider decision flow and shared legacy dispatch. Add targeted parity tests for each removed dependency. Preserve FK anchors while optional child families are isolated. Keep legacy route registration until future approval.
3. Only with explicit authorization, provision the reference scratch target and run catalog/ledger preflight. Separately approve bootstrap, migration replay and synthetic test writes. Then design the reduced scratch profile and compare against the full reference. Use the recovery procedure in DB01_SAFE_MIGRATION_STRATEGY.md.
4. Defer destructive schema changes and historical archival to independent review after usage, retention, dependency, recoverability and data reconciliation gates. Keep the existing schema and historical migration chain available to roll back application profile changes.

DB-01 ran static inspection and ORM mapper configuration only. No SQL Server or application test suite ran. Report actual isolated results before declaring schema readiness.
