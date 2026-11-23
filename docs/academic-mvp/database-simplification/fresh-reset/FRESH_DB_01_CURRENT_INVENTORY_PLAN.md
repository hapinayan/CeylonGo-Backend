# Fresh DB 01 ? Current inventory and read-only preflight

## Offline evidence

Verified feature/academic-mvp-backend-simplification at 3bf9822f6be99780d0e25c8752ed263ac407c3c6 and approved V1.1 requirements blob 31859af08c1bd0f88084205178b7cb0fdbe88582. DB-01 lists all 99 configured ORM mappings, 163 FK column references, and all 43 historical forward migrations with digests and DDL markers. DB-02 recorded 53 passing offline tests and 67 tables touched. The existing native-Activity profile is 59 mapped tables and the exercised compatibility profile is 72. The fresh design is a separate application contract. No live state was checked.

- [Scope](../../CEYLONGO_ACADEMIC_MVP_MASTER_REQUIREMENTS_V1.1.md)
- [DB-01 schema and migration inventory](../DB01_EXISTING_SCHEMA_INVENTORY.md) ? [dependency matrix](../DB01_TABLE_DEPENDENCY_MATRIX.md)
- [DB-02 runtime trace](../DB02_STAGE01_TABLE_ACCESS_MATRIX.md) ? [profile and blockers](../DB02_STAGE01_REVISED_MVP_SCHEMA.md)

## Intended environment and approval

Repository fallback is local .\SQLEXPRESS, with DB_SERVER override. Earlier operator messages identify VISHNUGA-KAJARASH\SQLEXPRESS. Treat both as candidate identifiers only. No credentials, configured process identity, topology, production status, users or clients were inspected. SSMS visibility of CeylonGoDB, CeylonGoDB_IntegrationTest, CeylonGoDB_JWT_Test, CeylonGoDB_MergeTest, CeylonGoDB_Rehearsal and CeylonGoDB_Rehearsal_V2 is operator-supplied. Online status, physical inventory, usage, unique data, and backup results are unknown.

Before connecting, obtain an owner attestation that the exact machine and instance are local and dedicated, corroborated by approved local service/config inspection and deployment ownership evidence. If shared or ambiguous, stop. Request a separate explicit authorization naming the instance, Windows principal, approved database list, and read-only query scope including any permitted master/msdb metadata and DMV permissions. Earlier Community/Eco approvals do not cover this operation. Use least-privilege integrated authentication with a stated identity. Any gap in visibility is reported, with no privilege changes during preflight.

## Read-only work after approval

1. Begin with instance identity, machine, instance name, database, original/effective principal and authentication scheme using SELECT and catalog metadata. Verify permissions immediately. Stop on mismatches, unexpected elevation, or inadequate visibility for the authorized task.
2. Enumerate database name, state, owner, access/update modes, recovery model and create date from approved metadata. Record only approved databases and no credentials or business-row samples.
3. In each approved catalog enumerate user table counts and schemas. For the main database list qualified table names, partition row estimates (heap or clustered index only), keys, FK columns/actions/trust, check/default definitions, filtered and unique indexes, triggers, views, synonym and cross-database references. Approximate row counts from DMVs are not exact. Use targeted COUNT_BIG only if explicitly approved where a reconciliation requires it.
4. Check existence and shape of dbo.schema_migrations before selecting version, file, checksum, order and execution method. Compare normalized SHA-256 to Git inventory and report drift. Use no runner command that can initialize a ledger.
5. If authorized inspect msdb backup history, backup coverage, checksum flags and metadata, Agent job targets, and current connection/application identities. Catalog backup history does not prove media or restore validity. Correlate job/config owner references and application inventory with active sessions, since idle consumers may be absent. Sanitize paths, account details, job command text and connection metadata in reports.
6. For each potentially retired test database review dependencies, owner, approved usage window, ledger/dataset fingerprints and synthetic-data provenance. If uniqueness cannot be ruled out, preserve it. A name or zero current sessions does not establish disposability.
