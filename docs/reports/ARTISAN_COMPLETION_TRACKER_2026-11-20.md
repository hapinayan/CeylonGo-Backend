# Artisan Completion Tracker

**Checkpoint:** Final Completion Phase 02, 20 November 2026  
**Branch:** `feature/artisan-integration`, base `1043307`

Phase 02 verified the five reservation and stock targets against both API shapes and the approved integration database. The five targets moved from partially completed to completed for the backend. Their previous and current percentages are shown below. The other 38 feature scores and statuses are unchanged.

See the [complete audit](ARTISAN_COMPLETE_DEVELOPMENT_AUDIT_2026-11-20.md) for the source, API and test evidence per feature and the [Phase 02 verification report](ARTISAN_FINAL_COMPLETION_PHASE_02_VERIFICATION_2026-11-20.md) for current results. Each feature has equal weight. Verified completion counts only rows classified as COMPLETED under the audit method.

| Area | Completed | Verification pending | Partial | Not implemented | Implementation | Verified complete |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Traveler | 5 | 3 | 8 | 2 | 79.2% | 5/18 (27.8%) |
| Service provider | 10 | 0 | 5 | 3 | 77.5% | 10/18 (55.6%) |
| Admin | 1 | 0 | 4 | 2 | 65.0% | 1/7 |
| Overall | 16 | 3 | 17 | 7 | 76.2% | 16/43 (37.2%) |

## Verification evidence and remaining requirements

- **T-11, T-12, P-07, P-12, P-13:** Phase 02 SQL Server and offline verification passed. Client acceptance for pickup timing, cash status, sold-out cues and error handling remains separate.
- **T-14:** Migration 038, favorites uniqueness and the guarded favorites service test passed. Backend verification is complete. Angular and Flutter acceptance remains separate.
- **T-07, T-08, T-15:** Client acceptance still pending.
- **T-17, P-15, P-16, A-04, A-05:** Paid sponsorship remains deferred.

## Traveler

| ID | Requirement | Current verification status | Previous | Current | Remaining evidence or work |
| --- | --- | --- | ---: | ---: | --- |
| T-01 | Browse approved active local businesses | COMPLETED | 100% | 100% | Confirm with product owner whether approval should suffice without location verification. |
| T-02 | Search district, location, business and product category | PARTIALLY COMPLETED | 70% | 70% | Add governed product categories and coherent product filter contract. |
| T-03 | Browse published local products | COMPLETED | 100% | 100% | Exercise public filters in client smoke tests. |
| T-04 | See description, price, stock and product category | PARTIALLY COMPLETED | 80% | 80% | Expose selected category and client-ready availability. |
| T-05 | View multiple product photos | PARTIALLY COMPLETED | 85% | 85% | Enforce or explicitly approve media visibility and test direct URL access after suspension. |
| T-06 | View business story, specialties and products | PARTIALLY COMPLETED | 80% | 80% | Confirm description reuse or add explicit story/specialties contract. |
| T-07 | Shop map pin and directions | IMPLEMENTED — VERIFICATION PENDING | 100% | 100% | Validate directions from real Angular and Flutter clients with valid and missing coordinates. |
| T-08 | Find nearby shops with location permission | IMPLEMENTED — VERIFICATION PENDING | 100% | 100% | Verify consent, denial, and manual-location UX on both clients; add direct route load/geo cases. |
| T-09 | Contact seller by phone, WhatsApp or email when supplied | PARTIALLY COMPLETED | 65% | 65% | Add/approve safe WhatsApp and phone normalization and verify client contact links. |
| T-10 | View published business opening hours | PARTIALLY COMPLETED | 90% | 90% | Unify public visibility and test suspended/inactive accounts. |
| T-11 | Reserve quantity and pickup date subject to provider approval | COMPLETED | 85% | 100% | Both payloads use atomic stock holds. SQL race and expiry verified. Verify pickup-date UX in clients. |
| T-12 | View own reservation status/details and cancel | COMPLETED | 90% | 100% | Ownership, payment/expiry response and duplicate-safe cancellation verified. Verify both client response adapters. |
| T-13 | Read and submit ratings and moderated feedback | PARTIALLY COMPLETED | 90% | 90% | Align publication predicate and prove duplicate review behavior in SQL Server. |
| T-14 | Save products and businesses | COMPLETED | 100% | 100% | Backend save/remove and SQL Server uniqueness verified. Exercise client use and legacy alias migration. |
| T-15 | Show badge only when admin verified | IMPLEMENTED — VERIFICATION PENDING | 100% | 100% | Check badge consumption and reconcile bool with workflow status under P-18. |
| T-16 | Show valid offers and discounted prices | PARTIALLY COMPLETED | 65% | 65% | Define offer priority/stacking, currency and server price preview/snapshot with floor zero. |
| T-17 | Label and show paid sponsored placement | NOT IMPLEMENTED | 0% | 0% | Approve proposals C/D and build relevance-aware labeled selection after payment verification. |
| T-18 | Share public product/business link | NOT IMPLEMENTED | 0% | 0% | Agree public URLs and deep links, verify share intents/auth-free opens and hidden resource handling. |

## Service provider

| ID | Requirement | Current verification status | Previous | Current | Remaining evidence or work |
| --- | --- | --- | ---: | ---: | --- |
| P-01 | Overview of business, products and reservations | COMPLETED | 100% | 100% | Verify Phase 03 status presentation in clients. |
| P-02 | Create and update owned business profile | COMPLETED | 100% | 100% | Smoke test frontend onboarding and catalog publishing as two distinct steps. |
| P-03 | Maintain background/story and specialties | PARTIALLY COMPLETED | 75% | 75% | Agree whether a single craft category is sufficient; add explicit fields if required. |
| P-04 | Create, edit and deactivate owned products | COMPLETED | 100% | 100% | Confirm whether material edits require re-moderation. |
| P-05 | Upload, reorder and remove product photos | COMPLETED | 100% | 100% | Add deployed storage and upload throttling smoke coverage. |
| P-06 | Set LKR prices | PARTIALLY COMPLETED | 85% | 85% | Enforce currency and decimal precision/limits across CRUD, offers and reservation. |
| P-07 | Manage available/out-of-stock state | COMPLETED | 90% | 100% | Sold-out and insufficient stock rejected; two SQL Server sessions proved no oversell. Verify client display of flag and stock count. |
| P-08 | Assign approved product categories | NOT IMPLEMENTED | 0% | 0% | Approve taxonomy, category management, assignment validation/filter and incremental migration. |
| P-09 | Maintain address and coordinates | COMPLETED | 100% | 100% | Validate edits and readback in clients. |
| P-10 | Maintain safe phone, WhatsApp and email | PARTIALLY COMPLETED | 65% | 65% | Decide WhatsApp number semantics and validate contact data rather than treating URLs as trusted actions. |
| P-11 | Set weekly open/close and closure days | COMPLETED | 100% | 100% | Confirm overnight and special-calendar scope with product owner. |
| P-12 | Accept and reject owned pickup requests | COMPLETED | 90% | 100% | Manual confirmation, rejection, stock restoration, audits, concurrency and rollback verified. Verify owner action UI. |
| P-13 | Mark collected and cash paid | COMPLETED | 85% | 100% | Pickup-day-only completion, single cash receipt and duplicate protection verified. Verify cash and pickup UI. |
| P-14 | Manage dated discounts and special offers | COMPLETED | 100% | 100% | Confirm provider UI status and inactive offers. |
| P-15 | Purchase/request paid sponsorship | NOT IMPLEMENTED | 0% | 0% | Approve commercial policies and reuse shared infrastructure via an Artisan-specific state machine. |
| P-16 | View promotion views, clicks and inquiries where tracked | NOT IMPLEMENTED | 0% | 0% | Approve event attribution, deduplication, retention, aggregation and tenant isolation. |
| P-17 | View customer feedback | PARTIALLY COMPLETED | 80% | 80% | Confirm provider visibility policy and dashboard workflow; add own-listing feedback query if accepted. |
| P-18 | See approval and verification state | PARTIALLY COMPLETED | 90% | 90% | Define one source of truth and test transitions, re-verification, and rejected messages. |

## Admin

| ID | Requirement | Current verification status | Previous | Current | Remaining evidence or work |
| --- | --- | --- | ---: | ---: | --- |
| A-01 | Approve or reject submitted business profiles | COMPLETED | 100% | 100% | Verify approval/verification UI wording and permissions. |
| A-02 | Grant/revoke verified badge with audit | PARTIALLY COMPLETED | 85% | 85% | Define atomic verified/status sync and append audit actor, reason and timestamp. |
| A-03 | Hide or flag inappropriate products | PARTIALLY COMPLETED | 90% | 90% | Persist reason and decision history; verify no access through media links after hide. |
| A-04 | Manage ad packages, activation and expiry | NOT IMPLEMENTED | 0% | 0% | Implement approved campaign/ledger flow after C/D approval. |
| A-05 | Verify promotion payment | NOT IMPLEMENTED | 0% | 0% | Add signed, locked, idempotent Artisan payment handler and reconciliation only after approval. |
| A-06 | Suspend business and remove public visibility | PARTIALLY COMPLETED | 90% | 90% | Test every public surface and explicitly define historical and media access. |
| A-07 | Basic business, product, reservation and promotion reports | PARTIALLY COMPLETED | 90% | 90% | Add rejected aggregation and, after sponsorship approval, paid metrics and reconciliation checks. |
