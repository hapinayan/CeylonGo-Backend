# Provider Management Backend Regression Stabilization

This pass covers the 22 failures reported after Phase 03A on `feature/provider-management`. The same failures had reproduced with the standalone Eco router disabled. The changes are limited to test fixtures and assertions. The existing publication, pricing, booking and payment rules remain in effect.

## Failure groups and corrections

| Failure group | Count | Root cause | Correction |
| --- | ---: | --- | --- |
| `test_homestay.py` public details, discovery, filters, pagination, media and availability | 18 | The legacy SQLite fixture creates a subset of tables. The public publication predicate now queries `provider_profiles`. Successful listing results also read `listing_references`, `advertising_payments` and Homestay offers. | Add the mapped provider and listing enrichment tables to the isolated fixture, including the promotion campaign table referenced by the payment model. Preserve the existing assertions and legacy approved listing data. |
| `test_homestay_owner_api.py::test_owner_create_list_update_flow` | 1 | The test expected a provider-created draft to be publicly visible immediately after an owner edit. | Verify the owner sees the updated draft in the private list. Assert public detail returns `404` for draft and pending states, then submit for review, approve with a separate administrator and verify the same public detail assertions. |
| `test_homestay_phase1_implementation.py::test_availability_blackout_blocks_and_booking_validation` | 1 | The test exercised public availability and traveler booking against a draft. Publication correctly rejected access before the blackout rules could be reached. | Verify both public paths reject the draft, then submit and approve through the existing APIs. Keep the overlap conflict, blocked availability, rejected booking and restored availability checks. |
| `test_promotion_api.py::test_active_promotions_filter_by_homestay_id` | 1 | The generic mock returned mock objects for `discount_type` and `discount_amount`, which the typed response rejects. | Return a real percentage-promotion ORM object, assert its serialized discount fields and verify the exact listing filter predicate. |
| `test_promotion_filter_integration.py::test_public_promotions_filter_by_homestay` | 1 | The reduced SQLite fixture omitted `provider_profiles`, which the joined listing publication predicate requires. | Include the table and keep the existing approved-listing filter assertions. Add a draft listing with an otherwise active offer and confirm the public endpoint returns no offer for it. |

## Migration 032 investigation

Read-only inspection first verified `DB_NAME()` was `CeylonGoDB_IntegrationTest`. Its ledger contains 001–031 and 033, with zero checksum mismatches among the recorded versions. There is no 032 row. Both `CK_tour_guides_coords` and `CK_tour_guide_packages_coords` are enabled and trusted, but their installed definitions match the older predicates from migration 022. These predicates can evaluate to `UNKNOWN` for a half-null coordinate pair, which SQL Server permits in a CHECK constraint. Migration 032 explicitly requires both coordinates in the range-check branch. The ledger and installed definitions together confirm that 032 is unapplied on the integration target.

The two preflight queries from 032 found zero coordinate-pair or range violations. Applying 032 would still require separate approval for the constraint replacement and its ledger write. This investigation made no changes to migration files, constraints, ledger or rows.

## Verification

The full offline run blocks SQL Server DBAPI connections and uses a temporary pytest directory. The guarded SQL Server suites run separately against `CeylonGoDB_IntegrationTest`. Existing category coverage includes Homestay booking and finance, Activity booking and capacity release, Rental native reservation timestamps, Artisan stock and receipts, Guide availability and reservation overlap, and Eco capacity, release and receipt races. The shared workspace and queue cover all six.

- Focused rerun of the five affected files passed all 71 tests.
- The full offline suite passed 908 tests with zero failures. The existing 19 opt-in SQL Server cases were skipped offline and exercised separately. All 22 original failures now pass. No tests were removed or given new skips.
- The guarded SQL Server rerun passed all 19 tests, including the two-session Eco races and the existing Phase 01–03 regressions.
- `git diff --check` passed. The Git index is empty.

## Files

- Updated `tests/test_homestay.py`, `tests/test_homestay_owner_api.py`, `tests/test_homestay_phase1_implementation.py`, `tests/test_promotion_api.py` and `tests/test_promotion_filter_integration.py`.
- Created this report and updated the [provider development tracker](PROVIDER_DEVELOPMENT_TRACKER.md).
