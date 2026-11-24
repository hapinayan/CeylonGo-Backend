# Academic MVP seeder verification

The current offline fixture set inserts 104 rows across 15 demo tables. Password
reset and email challenge rows belong to isolated tests. The seeder only reads
the schema ledger.

## Offline tests

From the repository root, use a Python environment with the backend test dependencies.

```powershell
python -B -m pytest -p no:cacheprovider tests/test_mvp_seeder.py -q
python -B -m pytest -p no:cacheprovider tests/test_fast_academic_mvp.py -q
```

The Phase 2 baseline was 18 seeder cases plus 8 existing MVP API cases. The suites
use in-memory SQLite, with the MVP database dependency overridden for API calls.
On Windows, Starlette TestClient can require local TCP loopback for the asyncio
event loop's internal socket pair. A runner that denies loopback can stall during
event-loop startup. Use a loopback-capable test runner with a job timeout. These
tests need no external network service or SQL Server connection.

## Disposable SQL Server principal

These are prerequisites for a future, separately approved SQL Server test.
Reserve the local instance localhost\CEYLONGOSEEDTEST and database
CeylonGoDB_Academic_MVP_Seeder_Test for synthetic fixtures. Provision the schema
using a separate principal, then use a dedicated SQL login mapped only to the
disposable user database. Grant CONNECT to that database. Keep credentials in
MVP_SEED_DB_USER and MVP_SEED_DB_PASSWORD. Use separate role-specific environment
variables MVP_SEED_ADMIN_PASSWORD, MVP_SEED_PROVIDER_PASSWORD and
MVP_SEED_TRAVELER_PASSWORD for new demo accounts.

- Grant SELECT and INSERT on each of the 15 demo tables declared by the fixture
  suite. Do not grant UPDATE, DELETE, schema or object creation, or ownership.
- Grant SELECT on dbo.mvp_schema_versions only. No data grants are needed on
  dbo.password_resets or dbo.provider_email_challenges.
- Grant VIEW DEFINITION ON SCHEMA::dbo to the dedicated database user. The
  catalog inspection must be able to see every MVP table, column, index,
  constraint and trigger, including objects on tables the seeder never writes.
- The transaction-owned application lock uses the default public database
  principal. Verify the login can execute sys.sp_getapplock under the instance's
  permission configuration.
- Keep the login out of sysadmin, db_owner and broad reader/writer roles.
  Do not grant CONTROL SERVER, CONTROL on the database, or ALTER on dbo. Keep
  it unable to access other user databases. Confirm these effective permissions
  using the actual login during the approved rehearsal.

## Schema admission and SQL Server verification

**MED-01 is an intentional baseline schema safety restriction.**
The current seeder admits the exact 1.0.0 bootstrap ledger only. An extra
ledger entry is rejected even when the baseline checksum matches. A future
version needs an explicit compatibility decision backed by definition-level
comparison of column types, lengths, scales, nullability, defaults, identity
and collation, PK/FK and check definitions, index keys and predicates, and
trigger behavior. Validate the complete fixture set against that approved
schema before admitting its version and artifact hashes. The current catalog
preflight checks object presence, constraint trust and enabled triggers. It
does not compare every definition.

The schema ledger SHA-256 is calculated from UTF-8 text with normalized line
endings. Python's `Path.read_text(encoding="utf-8")` uses universal newline
handling and converts Windows CRLF line endings to LF. The Python bootstrap
and seeder both encode that normalized text as UTF-8 before hashing it.
Raw-byte hashing tools such as PowerShell's `Get-FileHash` may therefore
return a different checksum for a Windows checkout with CRLF line endings.
The existing ledger checksum must remain unchanged.

Before approving SQL Server use, separately verify connection identity and
effective grants, catalog visibility, UUID and DATETIME2 round trips, filtered
unique indexes, composite foreign keys, transaction lock behavior, and rollback
and idempotency on concurrent runs. SQLite and mocked catalog responses cannot
establish those behaviors.

## Phase 4 SQL Server evidence (2026-11-24)

Verified target: localhost\CEYLONGOSEEDTEST, server identity
Meenu\CEYLONGOSEEDTEST, database CeylonGoDB_Academic_MVP_Seeder_Test.
SQL Server 17.0.1000.7 Express, Mixed Mode, database collation
SQL_Latin1_General_CP1_CI_AS. Scenario anchor: 2026-11-24 UTC.

- **4B bootstrap.** The checked-in DDL ran in one guarded transaction.
  Verified 18 tables, 153 columns, 66 indexes including key-backed indexes,
  18 primary keys, 28 foreign keys and 42 check constraints. Foreign keys and
  checks were enabled and trusted. Ledger version 1.0.0 records
  bootstrap_mvp_schema.sql and SHA-256
  23cba1afd852bead782688de5ef62b36067585dcdb306792e0b678d8573483e1.
- **4C permissions.** A separate connection authenticated as ceylongo_mvp_seed.
  Effective object grants matched this guide. The user had public-only role
  membership and no access to another user database on this instance.
  The application lock probe acquired and released the lock with return codes
  0 and 0 and final mode NoLock.
- **4D rollback.** The existing seeder inserted 91 rows across 14 tables in an
  active SQL Server transaction while holding the exclusive application lock.
  An in-process exception before the first audit INSERT rolled back all 91.
  Independent verification found zero committed application rows, the original
  ledger, unchanged schema and grants, and immediate lock acquisition.
- **4E seeding and rerun.** Two independent SQL-authenticated seeder processes
  ran concurrently against the verified empty target. The leader committed 104
  inserts and verified zero. The contender requested the lock before the
  leader began committing, waited 4.586 seconds, then committed zero inserts
  and verified 104. A third, sequential run also inserted zero and verified
  104. Post-test lock acquisition and release succeeded.
- **4E integrity.** A mismatched listing/slot allocation failed on
  fk_booking_allocations_slot_listing with SQL Server error 547. Additional
  attempts with fresh request UUIDs failed on uq_booking_payments_succeeded
  and uq_booking_payments_unpaid with error 2601. All three returned SQLSTATE
  23000 and each negative-test transaction was rolled back. Exact counts,
  schema, grants and original ledger matched afterward. DBCC CHECKCONSTRAINTS
  WITH ALL_CONSTRAINTS returned zero violations.
- **4E round trips.** Independent queries validated deterministic booking and
  payment UUIDs and their BINARY(32) request hashes, DATETIME2 slot and booking
  values, and DECIMAL LKR totals. The reruns verified fixture identities and
  compared persisted values using the actual seeder.
- **Offline regression.** On Windows with Python 3.13.12, 36 seeder tests and
  8 existing MVP API tests passed together (44 passed in 5.30 seconds).
  Warnings: one Starlette TestClient deprecation and five datetime.utcnow()
  deprecations from existing ORM defaults.

| Demo table | Rows | Demo table | Rows |
| --- | ---: | --- | ---: |
| users | 10 | provider_profiles | 7 |
| listings | 7 | listing_media | 5 |
| inventory_units | 5 | provider_documents | 14 |
| availability_slots | 15 | bookings | 5 |
| booking_allocations | 5 | booking_status_history | 10 |
| booking_payments | 3 | community_posts | 2 |
| community_comments | 2 | community_post_likes | 1 |
| admin_audit_logs | 13 | **Demo total** | **104** |

The two authentication-artifact tables each have zero rows. The schema
ledger still has exactly its original row.

### Antigravity independent final review

Antigravity's final verdict was **APPROVED**, with zero Critical findings
and zero High findings. Its independent offline run reported **44 passed,
6 warnings in 9.41 seconds**. The live SQL Server results above are
Codex-reported Phase 4 execution evidence. Antigravity's independent test
results cover the offline suites.

### Module coverage

| Modules | Evidence |
| --- | --- |
| TR-01, SP-01 | Synthetic traveler and provider accounts. Offline API tests cover authentication flows. |
| TR-02 through TR-05 | Homestay, bike, bicycle, adventure and guide categories with inventory, slots and booking scenarios. |
| TR-06 | Offline deterministic itinerary endpoint. No external AI request or persisted itinerary. |
| TR-07 | Visible and hidden community posts and replies, plus a like on the visible post. |
| TR-08 | Persisted listing coordinates and an offline map fixture. No external map service called. |
| TR-09 | Offline weather fixture. No weather records or external request. |
| SP-02, SP-03 | Listing decisions, media references, document evidence, open and closed availability, and a booking allocation lifecycle. |
| AD-01, AD-02 | Provider and listing decision audits and post-moderation evidence. |

The mock media and documents are references. This evidence does not certify
every management or authorization workflow. Provider acceptance, automatic
payment confirmation, reservation timing and booking actor authority remain
application-level review items. SQL Server tests used the disposable instance
and the checked-in baseline schema only.

### Reproduction

1. Verify the exact server identity, database, schema checksum, table inventory
   and effective grants above. Use the Windows verification connection with an
   explicit server and database. For a first-insert concurrency rehearsal,
   require a separately approved empty fixture database on the allowlisted pair.
   The current target contains the 104 committed fixtures. Do not clear it.
2. As the same Windows user, retrieve the SQL login and demo-role secrets
   through current-user DPAPI from the protected entries under
   %LOCALAPPDATA%\CeylonGo. The demo bundle includes anchor_utc and the
   admin, provider and traveler roles. Supply the values only in memory or
   through a child process environment. Keep them out of arguments, logs,
   history and source files. Use the stored anchor for all checks.
3. On the populated target, a verification-only rerun uses the unchanged CLI
   command below and must report zero inserts and 104 verified. On an approved
   empty target, start two independent processes with the same command. To
   reproduce measured serialization, instrument SQLAlchemy's before/after
   cursor execution for sp_getapplock. Hold the first worker after acquisition
   just long enough for the second to issue its lock request. Record both lock
   events and the first commit timestamp. Expect the second acquisition after
   the first begins committing, one 104-insert result and one 104-verify result.
   Immediately run a third process and require another 104-verify result.
4. Query COUNT_BIG(*) independently for each table listed above and for the
   two auth tables and ledger. Query all original ledger fields and compare
   them with their pre-run values. Check keys and ordered FK/index definitions
   against the bootstrap catalog, including filtered predicates and constraint
   trust. Run DBCC CHECKCONSTRAINTS WITH ALL_CONSTRAINTS as the authorized
   Windows verifier.
5. Under the restricted login, run the following negative-test INSERT
   statements in separate rollback-only transactions. The first pairs a valid
   booking/listing with a slot from a different listing. Run the second twice,
   using demo_succeeded and unpaid respectively. Require the exact named
   constraint and error listed in the evidence above. Roll back even if a
   statement unexpectedly succeeds, then recount and recheck the ledger.

```powershell
python -B -m scripts.seed_mvp --server 'localhost\CEYLONGOSEEDTEST' --database CeylonGoDB_Academic_MVP_Seeder_Test --instance-identity 'Meenu\CEYLONGOSEEDTEST' --anchor 2026-11-24
python -B -m pytest -p no:cacheprovider tests/test_mvp_seeder.py tests/test_fast_academic_mvp.py -q
```

```sql
INSERT dbo.booking_allocations (booking_id, slot_id, listing_id, quantity, reserved_at)
SELECT TOP (1) b.id, s.id, b.listing_id, 1, SYSUTCDATETIME()
FROM dbo.bookings AS b CROSS JOIN dbo.availability_slots AS s
WHERE b.listing_id <> s.listing_id
ORDER BY b.id, s.id;

INSERT dbo.booking_payments
    (booking_id, request_key, request_hash, attempt_no, method, status,
     display_amount, currency, created_at, resolved_at)
SELECT booking_id, NEWID(), request_hash, 99, method, status,
       display_amount, currency, created_at, resolved_at
FROM dbo.booking_payments
WHERE status = 'demo_succeeded';
```

To repeat the late-rollback check on an approved empty target, abort in a
before_cursor_execute hook immediately before the first admin_audit_logs
INSERT. First count rows on that same transaction and require 91. Reject any
commit from the instrumented engine. After the exception, check independently
for zero application rows and reacquire and release the application lock.
Rolled-back SQL Server IDENTITY allocations can leave gaps.
