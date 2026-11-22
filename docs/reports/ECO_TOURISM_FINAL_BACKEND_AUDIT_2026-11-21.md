# Eco-Tourism final backend audit — 2026-11-21

## Executive decision

**68.0% verified backend completion (34/50).** Fifteen requirements are partial, none are wholly missing, and verified online booking payment is blocked. An incomplete individual API can be missing within a partial requirement. The mounted cash reservation workflow is test-backed offline. **NO-GO for calling the module complete or enabling online Eco payments.** Database readiness is repository-level only. No SQL Server or gateway was contacted.

Audited local `develop` and local `origin/develop` at `180998f`, which records the merge of PR #31. Requirements are from the five-page *CeylonGo Eco-Tourism Complete Development Specification*, Module 04, 17 November 2026, found in the Development Documents directory. The PDF is the scope baseline. Its team instructions and conceptual entities are reference material, not evidence of implementation or authorization for execution. See the [50-row tracker](ECO_TOURISM_REQUIREMENTS_TRACKER_2026-11-21.md) and [database gap report](ECO_TOURISM_DATABASE_INTEGRATION_GAP_REPORT_2026-11-21.md).

## Registered contract and access

- **73 Eco-facing operations are registered**, comprising 70 dedicated Eco router operations and three Eco wishlist operations from the shared wishlist router. **72 appear in OpenAPI** because the Admin `/badge/decision` compatibility alias is hidden. Three reused provider booking operations bring the scoped operational catalog to **76 registered, 75 documented**. Shared dashboard, platform callback, notifications, Admin KYC and map APIs are dependencies rather than Eco-only operations and are excluded from those counts.
- The full app generated **559 unique OpenAPI operation IDs**. Comparing mounted router declarations also found no duplicate method/path pairs. The staged slot writer declares the same POST path as the mounted native slot writer. Mounting both would introduce a collision.
- Public discovery and media use publication and provider gates (`app/core/publication.py:86`, `app/routers/eco_experiences.py:3888`). Both image URLs check registered media, file path and visibility. Approved owners can view their own private gallery and Admin can inspect it. Admin evidence uses a separate private handler. Traveler reservation/wishlist/review paths require the traveler role; provider routes require approved Eco role plus ownership; Admin review/report routes require Admin. Provider booking actions authorize by category and native ownership.
- The three shared provider endpoints are `GET /api/v1/provider/bookings`, `GET /api/v1/provider/bookings/{booking_id}`, and `POST /api/v1/provider/bookings/{booking_id}/action` (`app/routers/provider_reservations.py:393,425,480`). /provider/dashboard reads native Eco cash and booking facts. Shared `GET /api/v1/notifications`, Admin provider KYC/approval and map endpoints also support Eco use cases. Their role and target predicates remain authoritative.
- Local OpenAPI generation verifies registration without starting a server. It does not prove a live release or production configuration.

### Unmounted and missing operations

| Surface | Status and effect |
|---|---|
| `app/routers/eco_slots.py` | Three staged operations: POST provider experience slots, GET experience slots, POST provider slot weather-cancel. POST slots overlaps the mounted native operation. Mutating staged paths have activation guards. |
| `app/routers/eco_bookings.py` | Seven unmounted routes: POST /eco-bookings, GET /eco-bookings/my-bookings, POST booking cancel, GET provider booking list, GET provider experience booking list, GET provider booking detail, POST provider decision. Uses Activity-backed storage and has guarded writes. Mounted native equivalents cover cash and basic lifecycle. |
| `app/routers/eco_payments_proposed.py:60` | POST /api/v1/eco-bookings/{booking_id}/payhere-checkout is unmounted. Without an injected persistence adapter it returns 503 even in a separately mounted test app. |
| Missing native integrations | Native Eco checkout/status, shared callback dispatch and eligible paid cancellation/refund. Native review eligibility and owner projections, Admin native booking/dispute monitoring, finance reconciliation and provider settlement acceptance. |
| Missing supporting integrations | Native slot/booking weather cascade and schedule notifications, sponsored checkout and activation, scheduled expiry notifications, durable booking conversions, and privacy-reviewed age/permit/guide enforcement. Extend existing contracts where practical. |

## Booking, transaction and payment findings

1. **Native cash flow is reachable.** `app/services/eco_reservations.py:225,331` locks the schedule, checks eligibility, computes the amount on the server, conditionally reserves capacity and writes a shared booking reference. New bookings are pending/unpaid. An owning provider can accept/reject and record cash separately. Completion requires elapsed slot and a paid positive charge. Cash receipt, native/shared state and history commit together. Unpaid cancellation checks the snapshotted window and releases held capacity once. Filtered uniqueness and transaction conditions are represented in repository DDL. Paid cancellations need an approved refund path.
2. **Legacy staged online code is substantive but unreachable from the public application.** `eco_payment_service.py` builds checkout from trusted booking data. `eco_payhere_webhook_security.py:146,194,268,394,439` validates merchant/signature, trusted order/amount/currency, event and replay. `eco_payment_application.py:62,102` and the coordinator at `eco_payment_transaction_coordinator.py:92,310` create/apply transition plans. The SQLAlchemy adapter at `eco_payment_sqlalchemy_adapter.py:165` flushes caller-owned writes to legacy booking/reference/ledger records. Unit and isolated SQLite tests exercise those layers. No merchant credential, live account destination, provider payout or bank transfer was verified.
3. **The shared callback rejects Eco booking types today.** `app/routers/payments.py:646,712–730` validates merchant/signature and dispatches ads, Rental, Guide and Homestay. An Eco discriminator reaches the unsupported-module response. No browser return can substitute for a trusted gateway callback. The adapter's read-based conflict checks and outer transaction still need native SQL Server race and rollback verification before activation.
4. **Prioritize durable idempotency and reconciliation.** Use shared capture locking and authoritative booking identity. Test exact replay, a different successful charge for the same reservation, mismatched ownership/amount/currency, late terminal captures and out-of-order events. Exact replay has one financial effect. A distinct or late charge needs an auditable reconciliation record rather than silently reusing an existing success response.
5. **Financial reads split across sources.** The deduplicated refund helper at `eco_experiences.py:319` limits shared finalized refunds to Activity references linked to the correct Eco-backed booking and buyer; it suppresses fallback only in the same scope. This protects against unrelated module leakage in that calculation. The helper and Eco-specific earnings/admin reporting still omit the native source, while the common dashboard reports native cash. The earnings estimate is not an approved commission or payout. Production financial approval and exact SQL catalog checks are in the [database report](ECO_TOURISM_DATABASE_INTEGRATION_GAP_REPORT_2026-11-21.md).
6. **Weather and participant eligibility need native parity.** The mounted weather emergency handler calls the older Activity slot/bookings service. Native slot creation/reservation use seasonal restrictions and safety acknowledgement, but existing native reservations need an owned, atomic emergency cascade with capacity release and notifications. Native booking input has no age/roster/permit/guide evidence even when listing metadata describes applicable restrictions.

## Security and test coverage

**Offline results:** **1,928 passed, 0 failed, 0 skipped in the selected runs**. The full top-level offline regression ran across 121 modules and reported **1,923 passed, 76 warnings in 380.73 seconds** under a 480-second cap. The five standalone mock-only safety tests in the nested SQL Server file passed separately, with nine unknown-marker warnings from collection. The top-level warnings were dependency deprecations (Starlette/TestClient, HTTP 422, SQLite datetime adaptation and datetime.utcnow).

The runner blocks ODBC and external socket connections and allows only Windows asyncio's internal loopback socket pair. Pytest cache writes were disabled and temporary files stayed outside the repository. Top-level SQL Server write suites and nested SQL Server execution cases were excluded, rather than counted as skips or passes. No production or integration database was used.

Relevant suites include listing/publication/authorization (`test_eco_experiences_api.py`), authorized file serving (`test_eco_experience_images_p05.py`), private evidence and badge checks (`test_eco_verification_p03.py`, `test_eco_badge_notifications_p08.py`), capacity, payment states and role isolation (`test_eco_reservations.py`), isolated legacy reviews/weather/finance (`test_eco_phase_5*.py`), promotions and tracking (`test_eco_promotions_*.py`), and staged PayHere security, persistence and replay tests. Unit tests that mount a staged router are evidence for its implementation, not application registration.

**Missing release tests:** real SQL Server two-session locking and rollback for native holds/cash/weather; native review and finance record coverage; cross-module numeric-ID collision and ownership; native eligibility for applicable age/permit/guide rules; signed gateway checkout/callback with distinct capture IDs and refund reconciliation; campaign payment-backed expiry, durable multi-worker tracking deduplication and booking attribution. SQL Server and sandbox tests require separate approval. ORM mapper configuration and static migration discovery passed.

## Priority-ordered remaining work

| Priority | Action | Acceptance |
|---|---|---|
| P0 | Approve the native reservation payment, cancellation and refund state machine, merchant account, commission and payout rules. Verify authorized target ledger/catalog when permitted. | Document the authoritative native identity and additive schema changes, without applying the archived Eco 030 proposal. |
| P0 | Reuse shared PayHere initiation, callback, transaction locks and ledger for native Eco. Add scoped status and durable reconciliation. | Verified server-side callback only, exact replay once, distinct/late captures tracked and no cross-module mutation. SQL and sandbox tests pass on authorized isolated targets. |
| P0 | Close native safety and lifecycle gaps. Enforce applicable age/permit/guide rules, implement paid cancellation/eligible refunds and native weather cascade. | Eligibility preflight, reason and policy snapshot, ownership, exactly-once release, coordinated notifications and rollback. |
| P1 | Move Eco review writes, native booking/admin monitoring, and earnings/refund/reporting projections to authoritative native joins. Preserve legacy reads deliberately. | Same-ID isolation, one review per completed booking, reconciled cash/online/refund/settlement reports and authorized dispute tracking. |
| P1 | Finish sponsored campaign payment, activation, expiry and distinct booking attribution. | No unpaid or expired ranked placement; durable event deduplication; owned views/clicks/conversions and expiry notification. |
| P2 | Finish notification delivery and run contract acceptance for Angular/Flutter and deployment. | Booking, schedule, badge and campaign event coverage; approved isolated SQL evidence, documented payloads and release runbook. |

## Generated route manifest

Counts are unique method/path pairs. Source lines identify decorators as generated from the router functions at the audit commit. The direct route manifest lists the 73 Eco-facing operations plus the three shared booking operations. The Auth column is derived from direct dependency calls, so shared Readers also enforce their nested authorization predicates.

### Eco listing, provider and gallery

| Method | Path | Handler | Auth / gate | OpenAPI |
|---|---|---|---|---|
| GET | `/api/v1/eco-experiences` | [`list_eco_experiences`](../../app/routers/eco_experiences.py#L385) | Public / handler rules | Yes |
| GET | `/api/v1/eco-experiences/categories` | [`get_eco_categories`](../../app/routers/eco_experiences.py#L735) | Public / handler rules | Yes |
| GET | `/api/v1/eco-experiences/districts` | [`get_eco_districts`](../../app/routers/eco_experiences.py#L747) | Public / handler rules | Yes |
| GET | `/api/v1/eco-experiences/{experience_id}` | [`get_eco_experience_detail`](../../app/routers/eco_experiences.py#L769) | Public / handler rules | Yes |
| GET | `/api/v1/eco-experiences/me/listings` | [`list_my_eco_experiences`](../../app/routers/eco_experiences.py#L819) | require_approved_eco_provider | Yes |
| GET | `/api/v1/eco-experiences/me/summary` | [`get_my_eco_experiences_summary`](../../app/routers/eco_experiences.py#L846) | require_approved_eco_provider | Yes |
| GET | `/api/v1/eco-experiences/me/earnings/summary` | [`get_my_eco_earnings_summary`](../../app/routers/eco_experiences.py#L1003) | require_approved_eco_provider | Yes |
| GET | `/api/v1/eco-experiences/me/earnings/settlements` | [`get_my_eco_settlements`](../../app/routers/eco_experiences.py#L1121) | require_approved_eco_provider | Yes |
| GET | `/api/v1/eco-experiences/me/reviews` | [`get_my_eco_reviews`](../../app/routers/eco_experiences.py#L1243) | require_approved_eco_provider | Yes |
| POST | `/api/v1/eco-experiences/{experience_id}/reviews` | [`submit_eco_experience_review`](../../app/routers/eco_experiences.py#L1396) | require_traveler | Yes |
| GET | `/api/v1/eco-experiences/{experience_id}/reviews` | [`list_eco_experience_reviews`](../../app/routers/eco_experiences.py#L1540) | Public / handler rules | Yes |
| POST | `/api/v1/eco-experiences` | [`create_eco_experience`](../../app/routers/eco_experiences.py#L1660) | require_approved_eco_provider | Yes |
| PUT | `/api/v1/eco-experiences/{experience_id}` | [`update_eco_experience`](../../app/routers/eco_experiences.py#L1779) | require_approved_eco_provider | Yes |
| POST | `/api/v1/eco-experiences/{experience_id}/submit` | [`submit_eco_experience_for_approval`](../../app/routers/eco_experiences.py#L1885) | require_approved_eco_provider | Yes |
| PATCH | `/api/v1/eco-experiences/{experience_id}/publish` | [`toggle_eco_experience_publication`](../../app/routers/eco_experiences.py#L1940) | require_approved_eco_provider | Yes |
| POST | `/api/v1/eco-experiences/{experience_id}/safety-rules` | [`add_eco_safety_rule`](../../app/routers/eco_experiences.py#L2755) | require_approved_eco_provider | Yes |
| GET | `/api/v1/eco-experiences/{experience_id}/safety-rules` | [`get_eco_safety_rules`](../../app/routers/eco_experiences.py#L2803) | Public / handler rules | Yes |
| DELETE | `/api/v1/eco-experiences/{experience_id}/safety-rules/{rule_id}` | [`delete_eco_safety_rule`](../../app/routers/eco_experiences.py#L2841) | require_approved_eco_provider | Yes |
| POST | `/api/v1/eco-experiences/{experience_id}/practices` | [`add_eco_practice`](../../app/routers/eco_experiences.py#L2897) | require_approved_eco_provider | Yes |
| GET | `/api/v1/eco-experiences/{experience_id}/practices` | [`list_eco_practices`](../../app/routers/eco_experiences.py#L2944) | require_approved_eco_provider | Yes |
| DELETE | `/api/v1/eco-experiences/{experience_id}/practices/{practice_id}` | [`delete_eco_practice`](../../app/routers/eco_experiences.py#L2988) | require_approved_eco_provider | Yes |
| POST | `/api/v1/eco-experiences/{experience_id}/practices/{practice_id}/evidence` | [`upload_eco_evidence`](../../app/routers/eco_experiences.py#L3049) | require_approved_eco_provider | Yes |
| DELETE | `/api/v1/eco-experiences/{experience_id}/practices/{practice_id}/evidence/{evidence_id}` | [`delete_eco_evidence`](../../app/routers/eco_experiences.py#L3160) | require_approved_eco_provider | Yes |
| POST | `/api/v1/eco-experiences/{experience_id}/submit-verification` | [`submit_eco_verification_request`](../../app/routers/eco_experiences.py#L3234) | require_approved_eco_provider | Yes |
| POST | `/api/v1/eco-experiences/{experience_id}/weather-emergency` | [`declare_weather_emergency`](../../app/routers/eco_experiences.py#L3315) | require_approved_eco_provider | Yes |
| POST | `/api/v1/eco-experiences/{experience_id}/images` | [`upload_eco_experience_image`](../../app/routers/eco_experiences.py#L3621) | require_approved_eco_provider | Yes |
| DELETE | `/api/v1/eco-experiences/{experience_id}/images/{image_id}` | [`delete_eco_experience_image`](../../app/routers/eco_experiences.py#L3807) | require_approved_eco_provider | Yes |
| GET | `/api/v1/eco-experiences/{experience_id}/images` | [`list_eco_experience_images`](../../app/routers/eco_experiences.py#L3909) | eco_media_visibility | Yes |
| GET | `/api/v1/eco-experiences/images/{filename}` | [`get_eco_experience_image`](../../app/routers/eco_experiences.py#L3928) | eco_media_visibility | Yes |

### Admin Eco

| Method | Path | Handler | Auth / gate | OpenAPI |
|---|---|---|---|---|
| GET | `/api/v1/admin/eco-experiences` | [`admin_list_eco_experiences`](../../app/routers/eco_experiences.py#L2003) | require_admin | Yes |
| GET | `/api/v1/admin/eco-experiences/overview` | [`get_admin_eco_experiences_overview`](../../app/routers/eco_experiences.py#L2067) | require_admin, require_admin | Yes |
| GET | `/api/v1/admin/eco-experiences/bookings` | [`list_admin_eco_bookings`](../../app/routers/eco_experiences.py#L2321) | require_admin, require_admin | Yes |
| GET | `/api/v1/admin/eco-experiences/payments/audit` | [`audit_admin_eco_payments`](../../app/routers/eco_experiences.py#L2427) | require_admin, require_admin | Yes |
| GET | `/api/v1/admin/eco-experiences/{experience_id}` | [`admin_get_eco_experience`](../../app/routers/eco_experiences.py#L2625) | require_admin | Yes |
| POST | `/api/v1/admin/eco-experiences/{experience_id}/approve` | [`admin_approve_eco_experience`](../../app/routers/eco_experiences.py#L2647) | require_admin, require_admin | Yes |
| POST | `/api/v1/admin/eco-experiences/{experience_id}/reject` | [`admin_reject_eco_experience`](../../app/routers/eco_experiences.py#L2699) | require_admin, require_admin | Yes |
| GET | `/api/v1/admin/eco-experiences/{experience_id}/evidence/{evidence_id}` | [`admin_get_eco_evidence_file`](../../app/routers/eco_experiences.py#L3388) | require_admin, require_admin | Yes |
| POST | `/api/v1/admin/eco-experiences/{experience_id}/badge/decision` | [`admin_badge_decision`](../../app/routers/eco_experiences.py#L3466) | require_admin, require_admin | Hidden alias |
| POST | `/api/v1/admin/eco-experiences/{experience_id}/badge-decision` | [`admin_badge_decision`](../../app/routers/eco_experiences.py#L3466) | require_admin, require_admin | Yes |
| GET | `/api/v1/admin/eco-experiences/{experience_id}/verifications` | [`admin_get_verification_history`](../../app/routers/eco_experiences.py#L3579) | require_admin, require_admin | Yes |

### Direct image alias

| Method | Path | Handler | Auth / gate | OpenAPI |
|---|---|---|---|---|
| GET | `/uploads/eco_experiences/{filename}` | [`get_eco_experience_image`](../../app/routers/eco_experiences.py#L3928) | eco_media_visibility | Yes |

### Eco promotions

| Method | Path | Handler | Auth / gate | OpenAPI |
|---|---|---|---|---|
| GET | `/api/v1/eco-promotions/packages` | [`list_public_promotion_packages`](../../app/routers/eco_promotions.py#L385) | Public / handler rules | Yes |
| POST | `/api/v1/eco-promotions/campaigns` | [`create_campaign_request`](../../app/routers/eco_promotions.py#L412) | require_approved_eco_provider | Yes |
| GET | `/api/v1/eco-promotions/me/campaigns` | [`list_my_campaigns`](../../app/routers/eco_promotions.py#L515) | require_approved_eco_provider | Yes |
| GET | `/api/v1/eco-promotions/me/campaigns/{campaign_id}` | [`get_my_campaign`](../../app/routers/eco_promotions.py#L572) | require_approved_eco_provider | Yes |
| POST | `/api/v1/eco-promotions/me/campaigns/{campaign_id}/cancel` | [`cancel_my_campaign`](../../app/routers/eco_promotions.py#L608) | require_approved_eco_provider | Yes |
| POST | `/api/v1/eco-promotions/track/impression` | [`track_impression`](../../app/routers/eco_promotions.py#L889) | Public / handler rules | Yes |
| POST | `/api/v1/eco-promotions/track/click` | [`track_click`](../../app/routers/eco_promotions.py#L907) | Public / handler rules | Yes |
| POST | `/api/v1/eco-promotions/campaigns/{campaign_id}/track/impression` | [`track_campaign_impression`](../../app/routers/eco_promotions.py#L925) | Public / handler rules | Yes |
| POST | `/api/v1/eco-promotions/campaigns/{campaign_id}/track/click` | [`track_campaign_click`](../../app/routers/eco_promotions.py#L944) | Public / handler rules | Yes |
| GET | `/api/v1/eco-promotions/me/analytics` | [`get_provider_promotions_analytics`](../../app/routers/eco_promotions.py#L968) | require_approved_eco_provider | Yes |
| GET | `/api/v1/eco-promotions/me/campaigns/{campaign_id}/analytics` | [`get_single_campaign_analytics`](../../app/routers/eco_promotions.py#L1099) | require_approved_eco_provider | Yes |

### Admin Eco promotions

| Method | Path | Handler | Auth / gate | OpenAPI |
|---|---|---|---|---|
| GET | `/api/v1/admin/eco-promotions/packages` | [`admin_list_promotion_packages`](../../app/routers/eco_promotions.py#L176) | require_admin | Yes |
| GET | `/api/v1/admin/eco-promotions/packages/{package_id}` | [`admin_get_promotion_package`](../../app/routers/eco_promotions.py#L215) | require_admin | Yes |
| POST | `/api/v1/admin/eco-promotions/packages` | [`admin_create_promotion_package`](../../app/routers/eco_promotions.py#L238) | require_admin, require_admin | Yes |
| PUT | `/api/v1/admin/eco-promotions/packages/{package_id}` | [`admin_update_promotion_package`](../../app/routers/eco_promotions.py#L278) | require_admin, require_admin | Yes |
| PATCH | `/api/v1/admin/eco-promotions/packages/{package_id}/toggle` | [`admin_toggle_promotion_package`](../../app/routers/eco_promotions.py#L345) | require_admin, require_admin | Yes |
| GET | `/api/v1/admin/eco-promotions/campaigns` | [`admin_list_campaigns`](../../app/routers/eco_promotions.py#L1198) | require_admin | Yes |
| GET | `/api/v1/admin/eco-promotions/campaigns/{campaign_id}` | [`admin_get_campaign`](../../app/routers/eco_promotions.py#L1257) | require_admin | Yes |
| POST | `/api/v1/admin/eco-promotions/campaigns/{campaign_id}/approve` | [`admin_approve_campaign`](../../app/routers/eco_promotions.py#L1286) | require_admin, require_admin | Yes |
| POST | `/api/v1/admin/eco-promotions/campaigns/{campaign_id}/reject` | [`admin_reject_campaign`](../../app/routers/eco_promotions.py#L1352) | require_admin, require_admin | Yes |
| GET | `/api/v1/admin/eco-promotions/analytics` | [`get_admin_promotions_analytics`](../../app/routers/eco_promotions.py#L1413) | require_admin, require_admin | Yes |

### Native Eco booking and availability

| Method | Path | Handler | Auth / gate | OpenAPI |
|---|---|---|---|---|
| GET | `/api/v1/eco-experiences/{experience_id}/availability` | [`public_availability`](../../app/routers/eco_reservations.py#L29) | Public / handler rules | Yes |
| GET | `/api/v1/provider/eco-experiences/{experience_id}/slots` | [`provider_slots`](../../app/routers/eco_reservations.py#L43) | require_approved_eco_provider | Yes |
| POST | `/api/v1/provider/eco-experiences/{experience_id}/slots` | [`create_slot`](../../app/routers/eco_reservations.py#L57) | require_approved_eco_provider | Yes |
| PATCH | `/api/v1/provider/eco-experiences/{experience_id}/slots/{slot_id}/close` | [`close_slot`](../../app/routers/eco_reservations.py#L68) | require_approved_eco_provider | Yes |
| POST | `/api/v1/eco-reservations` | [`reserve`](../../app/routers/eco_reservations.py#L78) | require_traveler | Yes |
| GET | `/api/v1/eco-reservations/me` | [`traveler_reservations`](../../app/routers/eco_reservations.py#L85) | require_traveler | Yes |
| GET | `/api/v1/eco-reservations/{booking_id}` | [`reservation_detail`](../../app/routers/eco_reservations.py#L98) | require_traveler | Yes |
| POST | `/api/v1/eco-reservations/{booking_id}/cancel` | [`cancel`](../../app/routers/eco_reservations.py#L110) | require_traveler | Yes |

### Eco wishlist operations

| Method | Path | Handler | Auth / gate | OpenAPI |
|---|---|---|---|---|
| POST | `/api/v1/wishlists/eco/{experience_id}` | [`add_to_eco_wishlist`](../../app/routers/wishlists.py#L38) | require_traveler | Yes |
| DELETE | `/api/v1/wishlists/eco/{experience_id}` | [`remove_from_eco_wishlist`](../../app/routers/wishlists.py#L130) | require_traveler | Yes |
| GET | `/api/v1/wishlists/me` | [`list_my_wishlist`](../../app/routers/wishlists.py#L163) | require_traveler | Yes |

### Reused provider booking operations

| Method | Path | Handler | Auth / gate | OpenAPI |
|---|---|---|---|---|
| POST | `/api/v1/provider/bookings/{booking_id}/action` | [`provider_reservation_action`](../../app/routers/provider_reservations.py#L389) | workspace_reader | Yes |
| GET | `/api/v1/provider/bookings` | [`provider_bookings`](../../app/routers/provider_reservations.py#L424) | workspace_reader | Yes |
| GET | `/api/v1/provider/bookings/{booking_id}` | [`provider_booking_detail`](../../app/routers/provider_reservations.py#L479) | workspace_reader | Yes |
