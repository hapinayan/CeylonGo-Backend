# Eco-Tourism requirements tracker — 2026-11-21

Baseline: `develop` at `180998f`, PR #31. Requirements were checked against the original *CeylonGo Eco-Tourism Complete Development Specification*, Module 04, 17 November 2026, pp. 1–3. IDs preserve the existing repository crosswalk (`docs/verification/module_04_50_requirement_status_matrix.md`). Historical status columns were not carried forward. PDF team instructions are reference material; only the user's audit and report request governed this work.

**34 complete, 15 partial, 0 missing, 1 blocked. Verified backend completion: 34 / 50 = 68.0%.** Complete means qualifying source, registered reachable behavior, and relevant offline evidence. It does not assert production SQL Server or gateway operation. Partial indicates an incomplete approved workflow. Blocked indicates a confirmed architecture/approval prerequisite for activation. Client delivery is outside the backend audit.

| Area | Total | Complete | Partial | Missing | Blocked |
|---|---:|---:|---:|---:|---:|
| Traveler | 18 | 12 | 5 | 0 | 1 |
| Provider | 18 | 13 | 5 | 0 | 0 |
| Admin | 8 | 4 | 4 | 0 | 0 |
| Eco-specific | 6 | 5 | 1 | 0 | 0 |
| Total | 50 | 34 | 15 | 0 | 1 |

## Evidence key

- E = `app/routers/eco_experiences.py`, under `/api/v1/eco-experiences` or `/api/v1/admin/eco-experiences`; image alias `/uploads/eco_experiences/{filename}`.
- R = `app/routers/eco_reservations.py`, M = `app/routers/eco_promotions.py`, W = `app/routers/wishlists.py`, PR = `app/routers/provider_reservations.py`, AP = `app/routers/admin_providers.py`, PAY = `app/routers/payments.py`, Q = `app/routers/eco_payments_proposed.py` (unmounted).
- RS = `app/services/eco_reservations.py`, PRS = `app/services/provider_reservations.py`, PW = `app/services/provider_workspace.py`, S = `app/schemas/eco_experience.py`. API paths use `/api/v1` unless expressly noted.
- DB shorthand: L = `eco_experiences`, I = images, S = safety/season rules, K = practices/evidence/verifications, A = `activity_slots` / `activity_bookings`, N = native `eco_availability_slots` / `eco_reservations`, W = `eco_wishlists`, M = Eco promotion tables, P = provider identity/KYC, B = booking references, F = shared payments/refunds/settlements, V = listing references/reviews, J = notifications/history. Numbers designate repository migrations, not an applied ledger.
- Test names are `tests/test_eco_<name>.py` for Eco-specific suffixes. Shared names such as `provider_workspace`, `interactive_map`, `admin_providers`, and `provider_registration` use `tests/test_<name>.py`. Only mounted routes and the named backend behavior earn completion credit.

## Traveler

| ID | Approved behavior | Status | Registered API / implementation | DB | Test evidence | Finding and remaining work |
|---|---|---|---|---|---|---|
| T01 | Discovery of approved Eco experiences | Complete | E:390 GET / | 17 L | experiences_api | Published listing and approved active provider filters. |
| T02 | Search and filters for location, category, price and duration | Complete | E:390 GET / | 17 L | experiences_api | Registered filtering and pagination. |
| T03 | Approved Eco categories | Complete | E:740 GET /categories | 17 L | experience_core, experiences_api | Shared taxonomy and metadata. |
| T04 | Experience details, itinerary and sustainability | Complete | E:774 GET /{id} | 17 L | experiences_api | Public eligibility and related data. |
| T05 | Public gallery for approved listings | Complete | E:3888,3910,3930 GET /{id}/images and both file routes | 17 L/I | experience_images_p05 | Registered file and visibility checks for public, owner and Admin. |
| T06 | Meeting point and external directions data | Complete | E:774 GET /{id}; Map router for routing | 17 L | experiences_api, interactive_map | Backend returns location and directions information; client rendering outside audit. |
| T07 | Available dates and slots | Complete | R:33 GET /eco-experiences/{id}/availability; RS:147 | 34 N + 17 S | reservations | Native slots, open state and remaining places. |
| T08 | Reserve a date and participants | Partial | R:81 POST /eco-reservations; RS:225 | 34 N + 27 B | reservations; staged booking inputs separately | Native counts and safety acknowledgement work; applicable participant age/permit/guide enforcement needs an explicit contract and tests. |
| T09 | Verified online payment to the CeylonGo account | Blocked | Proposed checkout at Q:60, unmounted and 503 without adapter; PAY:646 shared callback lacks Eco dispatch | 27 F + 34 N + 36 | payhere_integration, payhere_webhook_security, payments_proposed_router, payment_transaction_coordinator | Choose native architecture and approved capture/refund policy; mount initiation, status and trusted callback; verify sandbox merchant E2E. |
| T10 | Provider-approved pay-at-location booking | Complete | R:81 POST /eco-reservations; PR:393 POST /provider/bookings/{id}/action; RS:350 | 34 N + 27 B/F | reservations | Pending/unpaid until provider approval and separate cash receipt. |
| T11 | Own current and past reservations | Complete | R:86,99 GET /eco-reservations/me and /{id} | 34 N + 27 B | reservations | Owner-scoped list/detail for native reservations. |
| T12 | Policy-based cancellation | Partial | R:113 POST /eco-reservations/{id}/cancel; RS:355 | 34 N + 27 B/F | reservations, staged weather/cancellation tests | Native unpaid cutoff and capacity release work; paid cancellation/refund workflow unavailable. |
| T13 | Review an eligible completed Eco booking | Partial | E:1402,1545 POST/GET /{id}/reviews | 25 A + 27 B/V | phase_5b_reviews | Writer resolves only ActivityBooking; connect completed native Eco bookings to one verified review. |
| T14 | Save and remove Eco favorites | Complete | W:44,135,168 POST/DELETE /wishlists/eco/{id}, GET /wishlists/me | 18 W + 17 L | wishlists_p06 | Traveler-scoped writes and unique favorite. |
| T15 | Approved organizer contact information | Complete | E:774 GET /{id} | 17 L + 3 P | experiences_api | Protected by public listing eligibility. |
| T16 | Booking and schedule notifications | Partial | RS:420 PRS save_event; shared GET /notifications | 27 B/J | reservations, phase_5c_notifications | Booking lifecycle events exist; native schedule/emergency updates and full delivery matrix incomplete. |
| T17 | Clearly labeled paid sponsored Eco listings | Partial | E:390 GET /; M:736 tracking | 20 M + 17 L | promotions_discovery_p11d | Label, paid/active gating and tracking work for eligible records; actual payment-backed activation and expiry lifecycle remain open. |
| T18 | Display only Admin-verified Eco badge | Complete | E:774 GET /{id}; E:3476 Admin badge decision | 17 L/K | verification_p03, badge_notifications_p08 | Verified decisions and revocation. |

## Provider

| ID | Approved behavior | Status | Registered API / implementation | DB | Test evidence | Finding and remaining work |
|---|---|---|---|---|---|---|
| P01 | Dashboard with bookings, revenue and listings | Partial | E:851 GET /me/summary; PW:229,554 shared dashboard | 17 L + 25 A + 34 N + 27 F | dashboard_p07, phase_5a_metrics_and_earnings, provider_workspace | Eco summary counts legacy ActivityBooking; shared dashboard sees native and cash, but projections disagree. |
| P02 | Create Eco listing draft | Complete | E:1666 POST / | 17 L | experiences_api | Approved Eco role and schema validation. |
| P03 | Edit, publish and deactivate own listing | Complete | E:1784,1890,1945 PUT /{id}, POST /{id}/submit, PATCH /{id}/publish | 17 L | experiences_api | Owned mutations and approval gates; unpublish takes listing out of discovery. |
| P04 | Upload and remove listing photos | Complete | E:3627,3812 POST /{id}/images and DELETE /{id}/images/{image_id} | 17 I | experience_images_p05 | Validation and owner scope. |
| P05 | Assign approved Eco category | Complete | E:1666,1784 POST / and PUT /{id} | 17 L | experience_core, experiences_api | Schema/DB taxonomy. |
| P06 | Manage location, coordinates and meeting point | Complete | E:1666,1784 POST / and PUT /{id} | 17 L + 32 | experiences_api, interactive_map | Coordinate-pair and map review handling. |
| P07 | Manage per-person or group pricing | Complete | E:1666,1784 POST / and PUT /{id}; RS:225 | 17 L + 34 N | booking_pricing, reservations | Native Decimal totals and private group capacity. |
| P08 | Manage dates and time slots | Complete | R:47,62,72 GET/POST /provider/eco-experiences/{id}/slots and PATCH /{slot_id}/close | 34 N | reservations | Native create/list/close is mounted. Staged Activity-slot routes are unnecessary for these actions. |
| P09 | Enforce per-slot capacity | Complete | RS:109,225,331; native slots and reservations | 34 N | reservations; SQL race cases excluded | Conditional update, schedule lock, filtered unique index and release guard. SQL Server execution unverified in this audit. |
| P10 | Read incoming requests | Complete | PR:425 GET /provider/bookings, pending filter; PW:229 | 34 N + 27 B | provider_workspace, reservations | Approved category's native queue. |
| P11 | Accept or reject a request | Complete | PR:393 POST /provider/bookings/{id}/action; RS:350 | 34 N + 27 B/J | reservations | Ownership and valid prior state enforced. |
| P12 | Read upcoming, past and cancelled bookings | Complete | PR:425,480 GET /provider/bookings and /{id} | 34 N + 27 B/J | reservations, provider_workspace | Scoped dates/status/history on native queue. |
| P13 | Record eligible cash receipt | Complete | PR:393 POST /provider/bookings/{id}/action; RS:374,417 | 34 N + 27 F | reservations | Paid state and ledger write under the same transaction. |
| P14 | View online earnings and settlements | Partial | E:1008,1126 GET /me/earnings/{summary,settlements} | 25 A + 27 F | phase_5a_metrics_and_earnings | Legacy summaries and provider settlement reads exist; unify native/legacy ledger and approved financial policy. |
| P15 | Manage practices and submit evidence | Complete | E:2903,3055,3239 POST practice, evidence and verification | 17 K | verification_p03 | Ownership, upload checks and Admin review handoff. |
| P16 | Purchase paid promotion packages | Partial | M:390,418,520 GET /packages, POST /campaigns, GET /me/campaigns | 20 M | promotions_packages_p11b, promotions_campaigns_p11c | Request and history work; no Eco promotion checkout or verified activation. |
| P17 | Read own traveler feedback | Partial | E:1248 GET /me/reviews | 27 V + 17 L | phase_5b_reviews | Owned review read works; native completion cannot produce reviews through current writer. |
| P18 | Show views, clicks and booking conversions | Partial | M:973,1104 GET /me/analytics and /me/campaigns/{id}/analytics | 20 M + 31 shared attribution | promotions_analytics_p11e | Views/clicks and CTR exist; Eco booking attribution and durable replay protection missing. |

## Admin

| ID | Approved behavior | Status | Registered API / implementation | DB | Test evidence | Finding and remaining work |
|---|---|---|---|---|---|---|
| A01 | Approve or reject submitted Eco listings | Complete | E:2652,2704 POST Admin /{id}/approve and /reject | 17 L + 32 | experiences_api, interactive_map | Admin review and location gating. |
| A02 | Verify Eco organizers | Complete | AP:198,284,383 GET documents, POST approve/reject | 3–6 P | admin_providers, provider_registration | Shared onboarding and KYC is reachable by Admin. |
| A03 | Review sustainability evidence | Complete | E:3392,3476 GET Admin /{id}/evidence/{evidence_id}, POST badge decision | 17 K | verification_p03 | Admin-only file retrieval and decision. |
| A04 | Grant, revoke and audit Eco badges | Complete | E:3476,3584 POST Admin /{id}/badge-decision and GET /verifications | 17 K/L | verification_p03, badge_notifications_p08 | Audited lifecycle and compatibility alias. |
| A05 | Monitor bookings and disputes | Partial | E:2326 GET Admin /bookings | 25 A + 27 B | phase_5a_metrics_and_earnings | Legacy booking query; native monitoring and dispute state missing. |
| A06 | Monitor online payments, refunds and settlements | Partial | E:2432 GET Admin /payments/audit; PAY settlement/refund endpoints | 25 A + 27 F + 36 | phase_5a_metrics_and_earnings | Legacy payment/refund projection does not provide native coverage or verified payout/reconciliation. |
| A07 | Manage promotion packages and sponsored listings | Partial | M:176,238,278,345,1203,1291,1357 Admin /eco-promotions | 20 M | promotions_packages_p11b, promotions_campaigns_p11c | Editorial decisions and packages work; verified activation and expiry administration incomplete. |
| A08 | Eco listing, booking and revenue reports | Partial | E:2072 GET Admin /overview; M:1418 GET /admin/eco-promotions/analytics | 17 L + 25 A + 20 M + 27 F | dashboard_p07, phase_5a_metrics_and_earnings | Listing and legacy booking reports work; native and financial reconciliation missing. |

## Eco-specific

| ID | Approved behavior | Status | Registered API / implementation | DB | Test evidence | Finding and remaining work |
|---|---|---|---|---|---|---|
| E01 | Show site-specific environmental guidelines | Complete | E:774,1666 GET /{id}, POST / | 17 L/S | experience_core, experiences_api | Authoring and public projection. |
| E02 | Publish and enforce wildlife safety instructions | Complete | E:2761,2808 POST/GET /{id}/safety-rules; RS:225 | 17 S + 34 N | verification_p03, reservations | Provider management and native mandatory acknowledgement. |
| E03 | Enforce environmentally appropriate group sizes | Complete | RS:109,225 native slot and reservation POSTs | 17 L + 34 N | reservations | Listing bounds, slot cap and private-group rules. |
| E04 | Record and disclose conservation contribution separately | Complete | E:774,1666,1784 detail/create/update; S: Eco schemas | 17 L | experience_core, experiences_api | Dedicated contribution/partner fields and approval. |
| E05 | Store practices and supporting evidence for review | Complete | E:2903,3055,3392 practice/evidence upload and Admin read | 17 K | verification_p03 | Evidence access boundaries and review. |
| E06 | Block unsafe seasons/dates and cancel with reason | Partial | RS:53,147,225 eligibility; E:3320 POST /{id}/weather-emergency | 17 S + 25 A + 34 N | reservations, phase_5d_weather_cancellation | Native seasonal blocking exists; mounted emergency writer mutates Activity slots/bookings, not native reservation lifecycle. |

See the [backend audit](ECO_TOURISM_FINAL_BACKEND_AUDIT_2026-11-21.md) for the route manifest and test totals, and the [database gap report](ECO_TOURISM_DATABASE_INTEGRATION_GAP_REPORT_2026-11-21.md) for separately authorized checks.

## Phase 01 verification update

Branch `feature/eco-native-payment-phase01`, based on `180998f`. The historical inventory and scoring above are retained. **Current verified completion remains 34 complete, 15 partial, 0 missing and 1 blocked out of 50 (68.0%).** The gated code passed offline tests. SQL Server constraints and the PayHere sandbox have not been accepted.

| ID | Verified Phase 01 delta | Status and outstanding work |
|---|---|---|
| T08 | Native requests can supply matching participant ages. The minimum is checked, and permit/guide-required bookings fail closed. | Partial. A listing-specific maximum age and trusted permit/guide evidence workflow remain open. |
| T09 | Native owned checkout/status are registered. Shared PayHere callback dispatch reaches the native reservation handler. Offline tests cover captured, repeated, conflicting and late events. | Blocked. Checkout is disabled by default and requires SQL Server and gateway acceptance. |
| T12 | Native paid cancellation can request a full refund within the existing free window. Owner refund status and Admin decision routes are registered. | Partial. The paid cancellation flag stays disabled pending the separate constraint proposal. Verified refund execution and partial/zero refund policy are open. |
| T16 | Native capture, paid cancellation, and reconciliation record notification and history events. | Partial. Native emergency cascade and full schedule notification coverage remain open. |
| P14, A06, A08 | Verified native captures and refund requests now write to the shared ledger while enabled. | Partial. Native-specific earnings and Admin finance projections still require reconciliation and payout policy acceptance. |
| E06 | Existing weather/season rules still gate reservation and are checked again during checkout and capture. | Partial. The mounted emergency cascade still uses Activity-backed storage. |

See the [Phase 01 implementation report](../eco-tourism/ECO_PHASE01_NATIVE_PAYMENT_IMPLEMENTATION_REPORT.md).

## Phase 01B verification update

**Verified completion remains 34 complete, 15 partial, 0 missing and 1 blocked out of 50 (68.0%).** The existing requirement IDs and scoring are preserved.

| ID | Phase 01B evidence | Status |
|---|---|---|
| T08 | Native creation checks a configured maximum age and valid permit or guide clearance from trusted slot records. Acceptance, checkout, and capture recheck clearances. | Partial pending approval of schema and provisioning, plus any individual permit requirements. |
| T09 | Offline parallel callback replay exercises independent sessions with the schedule lock emulated. | Blocked pending SQL Server and gateway acceptance. |
| T12 | Full, partial and zero outcomes use the native cutoff and a trusted snapshot. Admin reconciliation transitions match merchant evidence to the capture. | Partial pending policy and SQL acceptance and production refund evidence procedures. |
| T16, E06 | Admin can apply an active seasonal or weather restriction to eligible future native slots. Replay leaves capacity and notifications unchanged. | Partial pending the captured-cash and in-progress weather paths and isolated SQL Server acceptance. |
| P14, A06, A08 | Successful manual reconciliation records the existing shared executed-refund state. | Partial pending finance projection and merchant reconciliation acceptance. |

## Phase 02 verification update

The scoped changes are verified offline. **Overall verified completion remains 34 complete, 15 partial, 0 missing and 1 blocked out of 50 (68.0%).**

| ID | Verified Phase 02 behavior | Status |
|---|---|---|
| T13 | The shared Eco review API uses the explicit native source, validates listing and traveler ownership and completion, and rejects repeat reviews on a canonical reference. The mixed fixture proves overlapping legacy/native IDs remain distinct. | Partial pending isolated SQL Server verification of concurrent submission and the transaction-owned lock. |
| P14 | The provider earnings summary adds native capture/refund/cash/commission breakdowns and provider-level settlement totals by currency. Mixed ledgers and provider isolation are covered offline. | Partial pending contractual commission and settlement attribution, and SQL Server acceptance. |
| A05 | An Admin-only native page filters and paginates reservations, including cancellations. | Partial because dispute tracking remains open. |
| A06, A08 | The native page and existing financial summaries expose captured revenue, outstanding and executed refunds, and reconciliation. Tests cover duplicate capture rows, mixed sources, separate settlement currency, and pagination. | Partial pending isolated SQL Server and merchant acceptance, including production-volume reporting and payout attribution. |

## Phase 03 verification update

| ID | Verified Phase 03 behavior | Status |
|---|---|---|
| T16 | Native reservation creation, slot closure and rescheduling, online payment, cancellation and refund approvals produce role-scoped in-app notifications. The schedule route records history, preserves holds, and handles immediate replays and stale expected times. | Partial pending isolated SQL Server lifecycle verification. |
| T17, P16 | Approved campaigns can issue an owned platform-merchant checkout. Verified callbacks activate eligible campaigns, and detail, list and search display eligible sponsorship within the date window. | Partial pending proposed schema and PayHere sandbox acceptance. |
| P18 | Signed impression and click receipts survive process-local cache reset when the ledger is enabled. A qualifying click can be credited to a native reservation once, visible in campaign analytics. | Partial pending authorized schema, isolated SQL Server acceptance and receipt retention policy. |
| A07 | Admins can persist expiry and inspect applied and reconciliation-required captures. | Partial pending operational reconciliation and schedule automation acceptance. |

The tracker remains **34 complete, 15 partial, 0 missing and 1 blocked out of 50 (68.0%)**.
