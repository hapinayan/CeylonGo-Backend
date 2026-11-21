# Provider Development Tracker

**Baseline:** `develop` at `16ecbc3`. **Update:** Phase 03B on `feature/provider-management`.
**Sources:** Service Provider Management Specification v1.0, 19 November 2026, and [Existing Provider Backend Audit](EXISTING_PROVIDER_BACKEND_AUDIT.md). The PDF has no requirement IDs. The IDs below are assigned here and remain stable for subsequent phases.

Use this tracker for backend completion. Implemented means every backend behavior in its row is available. Partial means some of the behavior exists or has an unresolved material gap. Database-only means a relevant persisted model exists without the workflow. Missing means no usable workflow was found. Blocked means approval or external policy is required. Dependent means a separate module or client must deliver the capability. One row equals one point. Divide the number of Implemented rows by all scored rows, including partial, database-only, missing, blocked and dependent rows. Excluded roadmap items are listed below the table. Evidence references the numbered audit sections.


## Phase 02 completion

| Status | Before Phase 02 | After Phase 02 | Weight |
| --- | ---: | ---: | ---: |
| Implemented | 7 | 8 | 1 |
| Partial | 25 | 24 | 0.5 |
| Database-only | 2 | 2 | 0.25 |
| Missing | 6 | 6 | 0 |
| Blocked | 1 | 1 | 0 |
| Dependent | 1 | 1 | 0 |
| **Total** | **42** | **42** | |

Fully implemented was 7/42 (16.7%) before Phase 02 and is 8/42 (19.0%) after it. The weighted result was `(7 + 25 * 0.5 + 2 * 0.25) / 42 = 47.6%` and is now `(8 + 24 * 0.5 + 2 * 0.25) / 42 = 48.8%`. The audited baseline was 4/42 (9.5%) fully implemented. PM-01 measures backend workspace authorization and capabilities. PM-38 tracks client navigation and acceptance separately. PM-14 remains Partial because general views and some category metrics are unavailable.

## Phase 03 completion

| Status | Before Phase 03 | After Phase 03 | Weight |
| --- | ---: | ---: | ---: |
| Implemented | 8 | 8 | 1 |
| Partial | 24 | 25 | 0.5 |
| Database-only | 2 | 1 | 0.25 |
| Missing | 6 | 6 | 0 |
| Blocked | 1 | 1 | 0 |
| Dependent | 1 | 1 | 0 |
| **Total** | **42** | **42** | |

Fully implemented remains 8/42 (19.0%). Weighted completion moves from `(8 + 24 * 0.5 + 2 * 0.25) / 42 = 48.8%` to `(8 + 25 * 0.5 + 1 * 0.25) / 42 = 49.4%`. PM-06 moves to Partial because Guide package and availability reads now support an active workflow. Languages, credentials, service area management and package CRUD remain open.

## Phase 03A completion

| Status | Before Phase 03A | After Phase 03A | Weight |
| --- | ---: | ---: | ---: |
| Implemented | 8 | 8 | 1 |
| Partial | 25 | 25 | 0.5 |
| Database-only | 1 | 1 | 0.25 |
| Missing | 6 | 6 | 0 |
| Blocked | 1 | 1 | 0 |
| Dependent | 1 | 1 | 0 |
| **Total** | **42** | **42** | |

Fully implemented remains 8/42 (19.0%). Weighted completion remains `(8 + 25 * 0.5 + 1 * 0.25) / 42 = 49.4%`. Phase 03A closes the standalone Eco reservation schema blocker and extends the shared queue to all six categories. Existing requirement rows still include work beyond the approved standalone reservation flow, such as season management, category-wide calendar coverage, online payments and reminders.

## Phase 03B completion

| Status | Before Phase 03B | After Phase 03B |
| --- | ---: | ---: |
| Implemented | 8 | 8 |
| Partial | 25 | 25 |
| Database-only | 1 | 1 |
| Missing | 6 | 6 |
| Blocked | 1 | 1 |
| Dependent | 1 | 1 |
| **Total** | **42** | **42** |

Fully implemented remains 8/42 (19.0%). Weighted completion remains `(8 + 25 * 0.5 + 1 * 0.25) / 42 = 49.4%`. Phase 03B closes the Guide booking and online capture gaps in scope. The broader requirements still include other category payment coverage, provider calendar and lifecycle gaps, automated expiry and refund execution.

### All 42 requirements

| ID | Spec | Requirement | Baseline | Phase 01 | Phase 02 | Phase 03 | Phase 03A | Phase 03B | Evidence and remaining work |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| PM-01 | 1, 3, 9–11 | Shared provider portal/API and role/category permissions | Partial | Partial | Implemented | Implemented | Implemented | Implemented | Audit sections 2–5 and 9. Phase 02 workspace, authenticated category permissions and capability navigation cover all six approved categories. See the Phase 02 report. |
| PM-02 | 2 | Homestay property, room, rates, amenities, availability and booking management | Partial | Partial | Partial | Partial | Partial | Partial | Audit section 4,7. Publication and dashboard fixes. Existing Homestay listing/booking flows remain; granular room inventory/workspace gaps remain. Phase 03 repairs existing cash, callback, cancellation and review writers. Granular room inventory remains open. |
| PM-03 | 2 | Artisan products, stock, business hours and cash pickup reservations | Partial | Partial | Partial | Partial | Partial | Partial | Audit section 4,7. Publish only verified shops. Existing item/promotion CRUD remains; pickup reservations and business hours are unwired. Phase 03 activates date-based stock reservations and reserve-and-pay-at-shop actions. Business hours and exact pickup time remain open. |
| PM-04 | 2 | Culture/Activity experiences, scheduling, capacity, prices and safety | Partial | Partial | Partial | Partial | Partial | Partial | Audit section 4,7,9. Fixed SQL Server predicates and publication checks; provider booking and full slot lifecycle remain incomplete. Phase 03 adds owned booking history and provider cancellation and completion actions. Full slot and booking lifecycle remains partial. |
| PM-05 | 2 | Eco experiences, evidence, group limits and seasonal rules | Partial | Partial | Partial | Partial | Partial | Partial | Audit section 4,7. Public media verified against listing visibility. Existing evidence and eligibility remain; full booking/availability and season management incomplete. Standalone Eco requests need an approved ownership and reservation schema. Existing Activity slots require an Activity parent. Phase 03A adds standalone Eco slots and reservations with group limits, published eligibility and local-calendar seasonal restrictions. Managed season-rule editing and online payment remain open. |
| PM-06 | 2 | Guide packages, languages, credentials, service areas and meeting points | Database-only | Database-only | Database-only | Partial | Partial | Partial | Audit section 4,7. Profile and base location work. Listed child workflows remain unwired. Phase 03 exposes active packages, availability windows and meeting points in the Guide booking workflow. Phase 03B supports same-day and continuous multi-day Guide packages. Separate Guide inventory and credential management gaps remain. |
| PM-07 | 2, 8 | Rental vehicle coverage, documents, deposits, pickup, handover and return | Partial | Partial | Partial | Partial | Partial | Partial | Audit section 4,7,9. Public visibility and booking eligibility fixed. Documents/deposits/inspection and expanded vehicle range remain. Phase 03 exposes native Rental booking queue, status actions and new status history. Deposits, vehicle coverage and inspections remain. |
| PM-08 | 3, 8 | Provider registration, resubmission and OTP email verification | Implemented | Implemented | Implemented | Implemented | Implemented | Implemented | Audit section 3. SQL Server locking and atomic challenge/grant consumption now protect the existing contract. |
| PM-09 | 3 | Login | Implemented | Implemented | Implemented | Implemented | Implemented | Implemented | Audit section 3. Provider and compatible shared JWT login remain. |
| PM-10 | 3, 7, 11 | Server logout and revocation | Missing | Missing | Missing | Missing | Missing | Missing | Audit section 3,5. No session revocation endpoint. |
| PM-11 | 3 | Forgot and reset password | Implemented | Implemented | Implemented | Implemented | Implemented | Implemented | Audit section 3. Shared recovery remains, subject to activation/approval. |
| PM-12 | 3, 8, 10 | Personal/business details, category selection, KYC and registration status | Implemented | Implemented | Implemented | Implemented | Implemented | Implemented | Audit section 3. Draft and submission APIs remain. Admin decisions and activation hardened. |
| PM-13 | 3, 8, 11 | Admin approval, rejection, reason and onboarding activation | Partial | Implemented | Implemented | Implemented | Implemented | Implemented | Audit section 3,9. Conditional decisions and grant/activation checks, existing reviewer fields, audit records, and pending/rejected contract. |
| PM-14 | 3, 7, 10 | Provider dashboard metrics, profile views, charts and notifications | Partial | Partial | Partial | Partial | Partial | Partial | Audit section 4–5. Existing Homestay review aggregates corrected. Unified dashboard, profile views and chart integration deferred. Phase 02 dashboard and monthly analytics cover supported metrics across six categories. General views, some category data and client charts remain. Phase 03 adds Artisan and Guide booking counts, monthly cash receipts and lifecycle activity. Eco bookings and general views remain unavailable. Phase 03A includes native Eco booking and verified cash receipt metrics. General profile and listing view gaps remain. |
| PM-15 | 3, 7, 10 | Edit own business profile, photos, address/map, hours and verification | Partial | Partial | Partial | Partial | Partial | Partial | Audit section 3–5. Existing listings and map management. Shared post-approval business profile edits remain. Phase 02 adds a shared business summary. Post-approval editing and managed media/hours are still pending. |
| PM-16 | 3, 8, 10–11 | Listing CRUD, private preview, deactivation, review and publication | Partial | Partial | Partial | Partial | Partial | Partial | Audit section 4–5,9. Existing preview/management paths retained and public gates consistent. Broader module CRUD gaps remain. |
| PM-17 | 3, 7, 10 | Pricing, schedule, stock, blackout blocks and capacity | Partial | Partial | Partial | Partial | Partial | Partial | Audit section 4,7. Homestay blocks and Activity reservations exist; Guide/Rental calendars and other parts remain. Phase 03 validates Guide windows and overlaps and atomically reserves Artisan stock. Full inventory configuration remains incomplete. Phase 03A adds owned Eco slot scheduling and atomic capacity holds. Other category inventory configuration remains incomplete. Phase 03B checks whole-interval Guide availability and blackouts under a guide lock. |
| PM-18 | 2–3, 10 | Additional category approval, switching and multi-service providers | Missing | Missing | Missing | Missing | Missing | Missing | Audit section 3,11. Current profile has one category. |
| PM-19 | 3, 7, 10–11 | Update documents/licenses, review and expiry | Partial | Partial | Partial | Partial | Partial | Partial | Audit section 3–4. Onboarding upload and admin-private access exist; managed renewal and expiry remain. |
| PM-20 | 4, 7–8, 10 | Provider reservation queue, valid transitions, history and calendar | Partial | Partial | Partial | Partial | Partial | Partial | Audit section 4–5. Existing Homestay workflow and Rental decisions. Provider queues/lifecycles uneven. Phase 02 adds native booking counts and upcoming previews for Homestay, Activities and Rental. Full provider queues and lifecycle actions remain uneven. Phase 03 adds the common provider queue and detail over five native booking sources. Eco reservations and some category lifecycle actions remain unavailable. Phase 03A brings Eco into the shared provider queue, detail and history. Category-wide lifecycle and calendar coverage still have gaps. Phase 03B adds Guide checkout hold, capture and expiry states to the provider queue and history. |
| PM-21 | 4, 11 | Authorized, minimal booking customer details | Partial | Partial | Partial | Partial | Partial | Partial | Audit section 4,9. Existing ownership checks and module-specific projections. Common least-privilege review still needed. Phase 03 scopes all common detail by provider and category, omits customer PII and validates history ownership. Category-specific customer detail policy remains separate. |
| PM-22 | 4, 7, 10 | Customer enquiries and open/resolved status | Missing | Missing | Missing | Missing | Missing | Missing | Audit section 5,11. No provider enquiry workflow. |
| PM-23 | 4 | Cancellation-policy configuration and booking snapshots | Partial | Partial | Partial | Partial | Partial | Partial | Audit section 4,7–8. Homestay cancellation and snapshots exist; other category policies incomplete. Phase 03 enforces the Guide cancellation snapshot and restricts Artisan cancellation to unpaid requests. Full cross-category policy configuration remains open. Phase 03A snapshots the Eco free-cancellation cutoff and enforces it for confirmed traveler cancellations. Paid cancellation and broader policy configuration remain open. Phase 03B blocks ordinary cancellation after Guide payment and uses the booking cancellation snapshot for eligible unpaid cancellations. |
| PM-24 | 4, 7–10 | Disputes and resolution tracking | Missing | Missing | Missing | Missing | Missing | Missing | Audit section 5,11. Finance reconciliation does not cover customer disputes. |
| PM-25 | 5, 7–8, 10–11 | Verified platform payments, cash recording and separate states | Partial | Partial | Partial | Partial | Partial | Partial | Audit section 4,8–9. Homestay PayHere and cash, Activities pay-at-location. Other modules not wired. Phase 03 synchronizes Homestay verified captures and registers Guide and Artisan cash receipts with separate payment states. Online payments outside Homestay remain unwired. Phase 03A records Eco cash receipts using the shared ledger. Eco online payment and wider category integration remain pending. Phase 03B adds Guide PayHere checkout and verified captures while preserving pay-at-location. |
| PM-26 | 5, 7–8, 10 | Earnings, commission, settlement history, refunds and transaction details | Partial | Partial | Partial | Partial | Partial | Partial | Audit section 5,8. Homestay implementation. No common ledger readers or supported provider payout requests. Phase 02 exposes verified Homestay ledger reporting and settlement snapshots. Additional category integrations, payout requests and payment writer validation remain. Phase 03 locks financial decisions, validates external references, synchronizes refunds and excludes cash from online commissions. External evidence verification and category settlement integrations remain open. Phase 03B records Guide late and duplicate captures for reconciliation. Guide refund execution and settlement remain pending. |
| PM-27 | 5–7, 11 | Secure audited bank detail management | Blocked | Blocked | Blocked | Blocked | Blocked | Blocked | Audit section 5,15. Confirm payout operations and approved bank-detail storage before implementing. |
| PM-28 | 5, 7, 10 | Download invoices, receipts and earnings statements | Missing | Missing | Missing | Missing | Missing | Missing | Audit section 5,11. Payment rows exist but no required download contracts. |
| PM-29 | 5, 7–8, 10–11 | Paid promotion packages, payment-backed activation, expiry and history | Partial | Partial | Partial | Partial | Partial | Partial | Audit section 4,8. Homestay full campaign flow, Eco campaign infrastructure, uneven category integrations. |
| PM-30 | 5, 7 | Listing discounts and eligible offers with validity | Partial | Partial | Partial | Partial | Partial | Partial | Audit section 4,8. Existing Homestay/Artisan promotions. Shared provider eligibility and offer integration remain. |
| PM-31 | 5–6, 11 | Sponsored labels, views, clicks, and attributed bookings | Partial | Partial | Partial | Partial | Partial | Partial | Audit section 4,8. Homestay attribution and Eco tracking. No uniform listing attribution across categories. Phase 02 exposes owned Eco sponsored impressions. General listing views and unified attribution remain unavailable. |
| PM-32 | 6–7, 9–11 | Provider community posts, media, tagging, own edits/deletes and engagement | Database-only | Database-only | Database-only | Database-only | Database-only | Database-only | Audit section 7,11. Existing Community schema/migration 028. No provider community API in the audited snapshot. |
| PM-33 | 6–7, 9, 11 | View, reply to and report reviews without deleting valid feedback | Partial | Partial | Partial | Partial | Partial | Partial | Audit section 5,8. Homestay reads/moderation map rating integration. No provider reply/report workflow. Phase 02 adds published review aggregates for owned shared listing references. Reply/report and consistent category wiring remain. Phase 03 repairs one-review-per-completed-Homestay booking and SQL timestamp persistence. Provider reply/report remains open. |
| PM-34 | 6–7, 9–11 | Date-filtered reporting and PDF/Excel exports | Partial | Partial | Partial | Partial | Partial | Partial | Audit section 5,8. Module summaries/ads/finance reads exist. Cross-module reports and exports missing. Phase 02 adds validated date filters and monthly series. PDF/Excel exports remain missing. |
| PM-35 | 6–7, 9–10 | Notifications for bookings, payments, decisions, announcements and expiry | Partial | Partial | Partial | Partial | Partial | Partial | Audit section 5,8. Shared inbox with selected event producers. Full fan-out, announcements and expiry remain. Phase 02 adds scoped unread counts. Unimplemented event fan-out and expiry remain. Phase 03 records reservation lifecycle notifications and audit history. Reminder and expiry automation remain open. Phase 03A adds Eco request, decision, completion and cash notifications. Scheduled reminders and expiry automation remain open. Phase 03B records Guide payment confirmation and checkout expiry notifications. Automated expiry and reminders remain open. |
| PM-36 | 6–7, 9 | Help center and support requests | Missing | Missing | Missing | Missing | Missing | Missing | Audit section 5,11. No provider support workflow. |
| PM-37 | 6–7, 10–11 | Account settings, password change, preferences, login history and sessions | Partial | Partial | Partial | Partial | Partial | Partial | Audit section 3,5. Login/recovery/profile read exist. Remaining settings and device management deferred. |
| PM-38 | 1, 7, 11 | Category-based navigation on Angular and applicable Flutter screens | Dependent | Dependent | Dependent | Dependent | Dependent | Dependent | Audit section 9. Frontend integration and client-specific acceptance are separate. The backend now emits navigation capability metadata. Angular and Flutter screens and acceptance remain separate. |
| PM-39 | 10–11 | SQL Server ownership, approval and one-time onboarding concurrency | Partial | Implemented | Implemented | Implemented | Implemented | Implemented | Audit section 9. Phase 01 conditional writes, transaction locks, uniqueness backstops and targeted SQL Server tests. |
| PM-40 | 10–11 | Sensitive uploads, no private data exposure, and audited status changes | Partial | Partial | Partial | Partial | Partial | Partial | Audit section 3,8–9. Problematic Eco files protected and provider reviews audited. Document lifecycle and wider financial audits remain. |
| PM-41 | 8, 10–11 | Booking/payment consistency, idempotent callbacks and no overselling | Partial | Partial | Partial | Partial | Partial | Partial | Audit section 4,8–9. SQL Server listing/availability gating and existing capacity/booking locks. Cross-module payment invariants and retries still require work. Phase 03 uses SQL Server locks, checked stock updates, native ownership and callback replay handling. Independent duplicate external charge reconciliation and Eco capacity remain open. Phase 03A adds SQL Server writer serialization, atomic capacity, unique active traveler holds and receipt replay protection. Cross-category payment reconciliation remains open. Phase 03B serializes Guide overlaps and shares capture replay protection with Homestay. |
| PM-42 | 11 | Regression verification of affected existing backend | Partial | Implemented | Implemented | Implemented | Implemented | Implemented | Audit section 10. Final Phase 01 run passed 248 tests. A separate pricing run passed 54 tests, including both previously failing price-preview cases. Prior isolated SQL Server verification passed four tests. Phase 02 reran 248 focused Phase 01 regressions and added workspace-specific data and authorization coverage. See the Phase 02 report for SQL Server evidence. Phase 03 reran affected regression suites and exercised native SQL Server reservation and transaction writers. See the Phase 03 report. Phase 03A covers native Eco requests, SQL Server races, migration rollback, and full backend regressions. See the Phase 03A report. See the Phase 03B report for final offline, focused and isolated SQL Server verification. |

**Non-scored scope.** Insurance provider onboarding is blocked pending a confirmed insurance model. Staff accounts, automatic bank settlements, advanced analytics, live chat and AI business assistance are future-only per section 12 and excluded from the denominator. Production policies for commission, payouts, refunds and insurance remain subject to independent approval.

See the [Phase 01 report](PHASE_01_SECURITY_AND_STABILITY_REPORT.md) for executed tests and verification limits.

See the [Phase 02 dashboard report](PHASE_02_PROVIDER_DASHBOARD_REPORT.md) for the API contract and verification.

See the [Phase 03 report](PHASE_03_BOOKING_AND_RESERVATION_REPORT.md) for workflows, verification and blockers.

See the [Phase 03A report](PHASE_03A_ECO_RESERVATIONS_REPORT.md) for the approved migration, API and verification evidence.

The PM-42 stabilization rerun passed 908 offline tests with zero failures and 19 opt-in SQL Server cases passed separately. The [backend regression stabilization report](BACKEND_REGRESSION_STABILIZATION_REPORT.md) records six-category booking coverage and the read-only migration 032 investigation. Stabilization adds no requirements and leaves the 42 requirement classifications and 49.4% weighted completion unchanged.

Phase 03B passed 915 offline tests with zero failures and 21 isolated SQL Server tests. See the [Phase 03B report and API contract](PHASE_03B_TOUR_GUIDE_BOOKING_AND_PAYMENTS_REPORT.md).
