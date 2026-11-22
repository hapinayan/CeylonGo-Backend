# Artisan Final Completion Phase 01 Verification

**Checkpoint** 20 November 2026 · `feature/artisan-integration` · base `1043307`

## Result

**Phase 01 is closed.** The corrected guard authorized the existing integration database, the backup passed checksum verification, reconciliation and schema checks passed, and the SQL Server and offline suites passed. These results cover the integration database and backend. Phase 02, client acceptance, sponsorship, and live deployment remain outside this checkpoint.

Implementation remains **74.8%**. Verified backend completion increased from **10/43 (23.3%)** to **11/43 (25.6%)**. T-14 product favorites moved from verification pending to completed after migration 038 and SQL Server service/uniqueness tests passed. Its implementation score stays at 100%. No implementation percentage changed. The [43-feature tracker](ARTISAN_COMPLETION_TRACKER_2026-11-20.md) records both scores, current verification status, and remaining work for every feature.

## Target and recovery backup

| Check | Result |
| --- | --- |
| Target | `localhost\SQLEXPRESS` → `VISHNUGA-KAJARASH\SQLEXPRESS` → `CeylonGoDB_IntegrationTest`, Windows Authentication |
| Guard | Configured target and connected SQL Server identity verified before backup, reconciliation, migration, or test writes. |
| Writer checks | No other integration-database user sessions were observed at the backup and reconciliation gates or immediately before the guarded upgrade. Offline pytest blocked all ODBC connections. |
| Backup | Full `COPY_ONLY` with `CHECKSUM` in the SQL Server instance's configured backup directory. |
| Verification | `RESTORE VERIFYONLY WITH CHECKSUM` passed. Header confirmed the expected database, full backup type, copy-only flag, and backup checksums. SQL Server reported finish time `2026-11-20 20:52:09`. No restore ran. |

Backup path: `C:\Program Files\Microsoft SQL Server\MSSQL17.SQLEXPRESS\MSSQL\Backup\CeylonGoDB_IntegrationTest_phase01_3ba63362ff54458bb547057d98f10f60.bak`.

## Migration ledger and data preservation

The original ledger and table counts were exported before any reconciliation writes. Entries 001–032 matched the repository. The Eco entry at 033 had the same checksum as repository 034, and the prior read-only catalog audit had verified the Eco schema. Under the existing session-owned migration application lock, a single transaction moved that row to 034, ran the original files 033, 035, and 036, and inserted their ledger rows. Before commit, checks verified matching checksums through 036, unchanged Eco metadata and pre-existing table counts, Rental and Guide columns and trusted checks, both Guide filtered indexes, and the trusted finance status checks. The Eco entry retained its original checksum, applied timestamp, execution duration, and method.

| Version | Final result |
| --- | --- |
| 001–032 | Original matching ledger entries preserved. |
| 033 | Applied `033_extend_rental_module_final_gaps.sql`. |
| 034 | Relabeled the verified Eco entry to `034_create_eco_reservations.sql`. Eco DDL was not replayed. |
| 035 | Applied `035_extend_tour_guide_module_final_gaps.sql`. |
| 036 | Applied `036_align_shared_payment_status_constraints.sql`. |
| 037 | Existing guarded runner applied `037_extend_artisan_reservation_rejection.sql` in four batches. |
| 038 | Existing guarded runner applied `038_create_artisan_product_favorites.sql` in two batches. |

The final ledger matches all repository filenames and checksums **001–038**, with no pending versions in that inventory. Before tests, every pre-existing table's row count matched the pre-reconciliation export. After tests, only the uniquely marked concurrency fixture contributed new rows. That fixture was retained to preserve the no-deletion boundary. The integration database retains reservation **14**, marker `Phase 01 rejection fixture 3b5c7cd84779479f9547fe6d05dbd2f2`, with one provider profile, two test users, one listing, one showcase product, one booking reference, two history entries and two notifications. The reservation is rejected and the product stock is restored to one. The other fixture-based tests rolled back their changes.

[Original ledger and table counts](ARTISAN_INTEGRATION_PRE_RECONCILIATION_2026-11-20.json) · [Final ledger and retained fixture counts](ARTISAN_INTEGRATION_POST_RECONCILIATION_2026-11-20.json)

## SQL Server schema and ORM

The catalog matched mapped columns, type/length or precision, nullability, identity, and FK targets and delete actions across all eight Artisan tables. FKs were enabled and trusted. New production-specific checks and indexes were inspected separately.

| Table | Matched columns | Matched FKs |
| --- | ---: | ---: |
| artisan_listings | 25 | 2 |
| showcase_items | 19 | 1 |
| promotions | 13 | 2 |
| artisan_business_images | 7 | 1 |
| artisan_product_images | 7 | 1 |
| artisan_reservations | 18 | 5 |
| artisan_business_hours | 7 | 1 |
| artisan_product_favorites | 4 | 2 |

Migration 037's rejection status and audit consistency checks are enabled and trusted. The existing status index remains present from the baseline and the new rejector FK points to `users.id`. Migration 038's unique `(user_id, product_id)` key, product lookup index, and cascade FKs are present. The duplicate insert test proved uniqueness enforcement.

## Test results

| Verification | Result |
| --- | --- |
| Guarded SQL Server application suite | **4 passed**, 0 failed. Rejection audit, exactly-once stock restoration, repeat rejection conflict, favorites idempotency and SQL uniqueness, application-lock release after rollback and commit, and rollback after an injected notification failure. Two physical sessions raced to reject the same reservation and produced one 200 and one 409, with one rejection notification. |
| Complete offline regression | **1,421 passed, 0 failed, 0 skipped**. SQL Server-only suites were excluded and the standalone offline rental calculation was included. Runtime 273.45 seconds, with eight deprecation warnings. |
| OpenAPI and mapper configuration | **90 mappers, 429 paths, 516 unique operations**. No duplicate registered method/path patterns. |

The SQL Server suite added a two-session rejection case and an injected-failure rollback case. Both run behind the exact target guard and ledger checks. [SQL Server integration tests](../../tests/test_artisan_phase03_sqlserver.py)

## Remaining work

Reservation acceptance, expiry parity, cash-response consistency, public visibility, product taxonomy, client verification, and sponsorship remain tracked. No original migration file, guard rule, main application connection, or live database was changed in this checkpoint. No branch change, staging, commit, push, or merge occurred.
