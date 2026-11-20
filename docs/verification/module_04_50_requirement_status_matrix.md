# CeylonGo Module 04 — 50 Requirement Status Matrix
**Reference Document:** `CeylonGo_Eco_Tourism_Complete_Development_Specification.pdf`  
**Classification Rules (Strict):**
- **COMPLETE:** Fully implemented, mounted on active router, accessible via backend API, backed by passing automated test evidence.
- **STAGED:** Implementation exists, but router is unmounted in `main.py`, guarded by HTTP 503, or awaiting formal activation approval.
- **PARTIAL:** Implemented at service/mock level, but important behavior, cascade wiring, or live verification is missing.
- **BLOCKED:** Cannot proceed due to a confirmed shared dependency, external API integration, or governance blocker.
- **NOT STARTED:** No meaningful code or test implementation.

---

## 1. Complete 50-Requirement Verification Matrix

### A. Traveler Features (T01–T18)

| ID | Exact Feature Name | PDF Required Behavior | Status | Implementation File | Endpoint & Method | Mounted? | 503 Guard? | Auth / Role | Test Evidence | Missing Functionality / Notes |
| :---: | :--- | :--- | :---: | :--- | :--- | :---: | :---: | :--- | :--- | :--- |
| **T01** | Eco-Tourism Discovery | Browse approved eco experiences | **COMPLETE** | `app/routers/eco_experiences.py` | `GET /api/v1/eco-experiences` | **YES** | NO | Public | `test_eco_experience_core.py`, `test_eco_experiences_api.py` | None. Filters `is_published=True` & `approved`. |
| **T02** | Search & Filters | Location, category, price, duration | **COMPLETE** | `app/routers/eco_experiences.py` | `GET /api/v1/eco-experiences` | **YES** | NO | Public | `test_eco_experiences_api.py` | None. Filters by search, category, district, price, duration, badge. |
| **T03** | Eco Categories | Forest, wildlife, village, conservation | **COMPLETE** | `app/routers/eco_experiences.py` | `GET /api/v1/eco-experiences/categories` | **YES** | NO | Public | `test_eco_experience_core.py` | None. Returns 6 approved categories with metadata. |
| **T04** | Eco Experience Details | Description, itinerary and sustainability info | **COMPLETE** | `app/routers/eco_experiences.py` | `GET /api/v1/eco-experiences/{id}` | **YES** | NO | Public | `test_eco_experiences_api.py` | None. Returns itinerary, guidelines, safety rules, badge, practices. |
| **T05** | Photo Gallery | View approved experience images | **COMPLETE** | `app/routers/eco_experiences.py` | `GET /api/v1/eco-experiences/{id}/images` | **YES** | NO | Public | `test_eco_experience_images_p05.py` | None. Multi-image gallery with thumbnail/display order. |
| **T06** | Location & Maps | Meeting point and external directions | **COMPLETE** | `app/routers/eco_experiences.py` | `GET /api/v1/eco-experiences/{id}` | **YES** | NO | Public | `test_eco_experience_core.py` | None. Coordinates (lat/lng), meeting point, district data. |
| **T07** | Availability Calendar | Available dates and slots | **STAGED** | `app/routers/eco_slots.py` | `GET /api/v1/eco-experiences/{id}/slots` | **NO** | NO | Public | `test_eco_slots_router_staged.py` | Router unmounted in `app/main.py`. Awaiting Gate 4. |
| **T08** | Eco Experience Booking | Reserve date and participants | **STAGED** | `app/routers/eco_bookings.py` | `POST /api/v1/eco-bookings` | **NO** | **YES (503)** | `traveler` | `test_eco_bookings_router_staged.py`, `test_eco_booking_service.py` | Blocked by Gate 1 (ORM patch), Gate 2 (Migration 030), Gate 4 (Mount). |
| **T09** | Online Payment | Verified payment to CeylonGo account | **BLOCKED** | `app/services/eco_payment_service.py` | `None` (Standalone Service) | **NO** | N/A | `traveler` | `test_eco_payhere_integration.py` | Blocked by shared `payments.py` router refactor, DB transactions, IPN. |
| **T10** | Pay at Location | Provider-approved reservation with cash at venue | **STAGED** | `app/routers/eco_bookings.py` | `POST /api/v1/eco-bookings` (`payment_method='pay_at_location'`) | **NO** | **YES (503)** | `traveler` | `test_eco_bookings_router_staged.py` | 503 guard blocks execution. Awaiting Gate 1, 2, 4. |
| **T11** | My Bookings | Own upcoming and past bookings | **STAGED** | `app/routers/eco_bookings.py` | `GET /api/v1/eco-bookings/my-bookings` | **NO** | NO | `traveler` | `test_eco_bookings_router_staged.py` | Router unmounted in `app/main.py`. Awaiting Gate 4. |
| **T12** | Booking Cancellation | Policy-based cancellation | **STAGED** | `app/routers/eco_bookings.py` | `POST /api/v1/eco-bookings/{id}/cancel` | **NO** | **YES (503)** | `traveler` | `test_eco_bookings_router_staged.py`, `test_eco_booking_service.py` | 503 guard blocks status update & capacity release. Awaiting Gate 4. |
| **T13** | Ratings & Reviews | Reviews after eligible completed bookings | **COMPLETE** | `app/routers/eco_experiences.py` | `POST /api/v1/eco-experiences/{id}/reviews` | **YES** | NO | `traveler` | `test_eco_phase_5b_reviews.py` | None. Enforces verified booking completion requirement. |
| **T14** | Wishlist | Save favorite eco-experiences | **COMPLETE** | `app/routers/wishlists.py` | `POST /api/v1/wishlists/eco/{id}`, `GET /api/v1/wishlists/me` | **YES** | NO | `traveler` | `test_eco_wishlists_p06.py` | None. Saved favorites with duplicate prevention. |
| **T15** | Contact Provider | Approved organizer contact details | **COMPLETE** | `app/routers/eco_experiences.py` | `GET /api/v1/eco-experiences/{id}` | **YES** | NO | Public | `test_eco_experiences_api.py` | None. Exposes verified provider contact info on approved listings. |
| **T16** | Booking Notifications | Booking and schedule status updates | **PARTIAL** | `app/services/eco_booking_service.py` | In-memory notification service | N/A | N/A | System | `test_eco_phase_5c_notifications.py` | Service triggers tested (12 passed); live production E2E pending router mount. |
| **T17** | Sponsored Eco Listings | Clearly labeled paid promotion placements | **COMPLETE** | `app/routers/eco_promotions.py`, `app/routers/eco_experiences.py` | `POST /track/impression`, `GET /api/v1/eco-experiences` | **YES** | NO | Public | `test_eco_promotions_discovery_p11d.py` | None. Boosts ranking, displays `is_sponsored=True` and tracking token. |
| **T18** | Eco-Friendly Badge | Display only admin-verified badge | **COMPLETE** | `app/routers/eco_experiences.py` | `GET /api/v1/eco-experiences/{id}` | **YES** | NO | Public | `test_eco_badge_notifications_p08.py` | None. Strict guard ensures unverified/revoked badges stay hidden. |

---

### B. Service Provider Features (P01–P18)

| ID | Exact Feature Name | PDF Required Behavior | Status | Implementation File | Endpoint & Method | Mounted? | 503 Guard? | Auth / Role | Test Evidence | Missing Functionality / Notes |
| :---: | :--- | :--- | :---: | :--- | :--- | :---: | :---: | :--- | :--- | :--- |
| **P01** | Eco Provider Dashboard | Bookings, revenue and listing overview | **COMPLETE** | `app/routers/eco_experiences.py` | `GET /api/v1/eco-experiences/me/summary` | **YES** | NO | `provider` | `test_eco_dashboard_p07.py`, `test_eco_phase_5a_metrics_and_earnings.py` | None. Aggregates listings, booking statuses, GMV, cash, refunds. |
| **P02** | Add Eco Experience | Create draft listing | **COMPLETE** | `app/routers/eco_experiences.py` | `POST /api/v1/eco-experiences` | **YES** | NO | `provider` | `test_eco_experience_core.py` | None. Draft listing creation with input validation. |
| **P03** | Manage Eco Experiences | Edit, publish or deactivate owned listings | **COMPLETE** | `app/routers/eco_experiences.py` | `PUT /{id}`, `POST /{id}/submit`, `PATCH /{id}/publish` | **YES** | NO | `provider` | `test_eco_experience_core.py`, `test_eco_experiences_api.py` | None. Full lifecycle management with ownership checks. |
| **P04** | Photo Management | Upload and remove images | **COMPLETE** | `app/routers/eco_experiences.py` | `POST /{id}/images`, `DELETE /{id}/images/{img_id}` | **YES** | NO | `provider` | `test_eco_experience_images_p05.py` | None. Image upload, validation (size/ext), ordering, deletion. |
| **P05** | Eco Categories | Assign approved categories | **COMPLETE** | `app/routers/eco_experiences.py` | `POST /api/v1/eco-experiences` | **YES** | NO | `provider` | `test_eco_experience_eligibility.py` | None. Validates category against official taxonomy. |
| **P06** | Location Management | Address, coordinates and meeting point | **COMPLETE** | `app/routers/eco_experiences.py` | `POST /api/v1/eco-experiences`, `PUT /{id}` | **YES** | NO | `provider` | `test_eco_experience_core.py` | None. Location coordinates, district, meeting point instructions. |
| **P07** | Pricing Management | Per-person or group price | **COMPLETE** | `app/routers/eco_experiences.py` | `POST /api/v1/eco-experiences`, `PUT /{id}` | **YES** | NO | `provider` | `test_eco_booking_pricing.py` | None. Decimal pricing, group discounts, LKR currency. |
| **P08** | Schedule Management | Dates and time slots | **STAGED** | `app/routers/eco_slots.py` | `POST /api/v1/provider/eco-experiences/{id}/slots` | **NO** | **YES (503)** | `provider` | `test_eco_slots_router_staged.py`, `test_eco_slot_validation.py` | Blocked by Gate 2 (Migration 030: `activity_slots.activity_id` NULL). |
| **P09** | Capacity Management | Per-slot participant limits | **COMPLETE** | `app/services/eco_booking_service.py`, `app/routers/eco_experiences.py` | `min_participants`, `max_group_size`, `validate_slot_capacity` | **YES** | NO | System | `test_eco_slot_validation.py`, `test_eco_booking_service.py` | None. Atomic checks prevent overbooking; bounds enforced. |
| **P10** | Booking Requests | View incoming requests | **STAGED** | `app/routers/eco_bookings.py` | `GET /api/v1/provider/eco-bookings?status=pending` | **NO** | NO | `provider` | `test_eco_bookings_router_staged.py` | Router unmounted in `main.py`. Awaiting Gate 4. |
| **P11** | Booking Approval | Accept or reject requests | **STAGED** | `app/routers/eco_bookings.py` | `POST /api/v1/provider/eco-bookings/{id}/decision` | **NO** | **YES (503)** | `provider` | `test_eco_bookings_router_staged.py`, `test_eco_booking_service.py` | 503 guard blocks decision execution. Awaiting Gate 4. |
| **P12** | Booking History | Upcoming, past and cancelled bookings | **STAGED** | `app/routers/eco_bookings.py` | `GET /api/v1/provider/eco-bookings` | **NO** | NO | `provider` | `test_eco_bookings_router_staged.py` | Router unmounted in `main.py`. Awaiting Gate 4. |
| **P13** | Cash Payment Updates | Record cash received at venue | **STAGED** | `app/routers/eco_bookings.py` | `POST /api/v1/provider/eco-bookings/{id}/decision` (`record_cash`) | **NO** | **YES (503)** | `provider` | `test_eco_bookings_router_staged.py` | 503 guard blocks cash status transition. Awaiting Gate 4. |
| **P14** | Online Earnings | View payment and settlement records | **COMPLETE** | `app/routers/eco_experiences.py` | `GET /me/earnings/summary`, `GET /me/earnings/settlements` | **YES** | NO | `provider` | `test_eco_phase_5a_metrics_and_earnings.py` | None. Aggregates gross earnings, platform commission, net payouts. |
| **P15** | Eco-Friendly Practices | Describe practices and submit evidence | **COMPLETE** | `app/routers/eco_experiences.py` | `POST /{id}/practices`, `POST /{id}/practices/{pid}/evidence` | **YES** | NO | `provider` | `test_eco_verification_p03.py` | None. Practice CRUD, document uploads (PDF/img), audit log. |
| **P16** | Sponsored Promotions | Purchase paid visibility packages | **COMPLETE** | `app/routers/eco_promotions.py` | `GET /packages`, `POST /campaigns`, `GET /me/campaigns` | **YES** | NO | `provider` | `test_eco_promotions_campaigns_p11c.py` | None. Package selection, campaign creation, duration tracking. |
| **P17** | Ratings & Reviews | View traveler feedback | **COMPLETE** | `app/routers/eco_experiences.py` | `GET /api/v1/eco-experiences/me/reviews` | **YES** | NO | `provider` | `test_eco_phase_5b_reviews.py` | None. Provider review list with rating distribution and filters. |
| **P18** | Promotion Statistics | Views, clicks and booking conversions | **COMPLETE** | `app/routers/eco_promotions.py` | `GET /me/analytics`, `GET /me/campaigns/{id}/analytics` | **YES** | NO | `provider` | `test_eco_promotions_analytics_p11e.py` | None. CTR, daily impression/click counts, conversion metrics. |

---

### C. Admin Features (A01–A08)

| ID | Exact Feature Name | PDF Required Behavior | Status | Implementation File | Endpoint & Method | Mounted? | 503 Guard? | Auth / Role | Test Evidence | Missing Functionality / Notes |
| :---: | :--- | :--- | :---: | :--- | :--- | :---: | :---: | :--- | :--- | :--- |
| **A01** | Eco Listing Approval | Approve or reject submitted listings | **COMPLETE** | `app/routers/eco_experiences.py` | `POST /api/v1/admin/eco-experiences/{id}/approve`, `reject` | **YES** | NO | `admin` | `test_eco_experiences_api.py` | None. Listing status transitions with required rejection reason. |
| **A02** | Provider Verification | Verify eco-tourism organizers | **COMPLETE** | `app/routers/admin_providers.py`, `app/routers/eco_experiences.py` | `POST /api/v1/admin/providers/{id}/verify` | **YES** | NO | `admin` | `test_admin_providers.py` | None. Organizer verification status check and KYC workflow. |
| **A03** | Eco-Friendly Verification | Review evidence of sustainability claims | **COMPLETE** | `app/routers/eco_experiences.py` | `GET /api/v1/admin/eco-experiences/{id}/evidence/{eid}` | **YES** | NO | `admin` | `test_eco_verification_p03.py` | None. Private evidence document retrieval and review trail. |
| **A04** | Eco Badge Management | Grant, revoke and audit eco badges | **COMPLETE** | `app/routers/eco_experiences.py` | `POST /api/v1/admin/eco-experiences/{id}/badge-decision` | **YES** | NO | `admin` | `test_eco_badge_notifications_p08.py` | None. Full grant, reject, revoke actions with decision audit log. |
| **A05** | Booking Monitoring | Monitor booking statuses and disputes | **COMPLETE** | `app/routers/eco_experiences.py` | `GET /api/v1/admin/eco-experiences/bookings` | **YES** | NO | `admin` | `test_eco_phase_5a_metrics_and_earnings.py` | None. Admin booking table with status, date, and provider filters. |
| **A06** | Payment Monitoring | Review online payments, refunds and settlements | **COMPLETE** | `app/routers/eco_experiences.py` | `GET /api/v1/admin/eco-experiences/payments/audit` | **YES** | NO | `admin` | `test_eco_phase_5a_metrics_and_earnings.py` | None. Payment reconciliation across transactions and refunds. |
| **A07** | Promotion Management | Manage packages and sponsored listings | **COMPLETE** | `app/routers/eco_promotions.py` | `POST /admin/eco-promotions/packages`, `POST /campaigns/{id}/approve` | **YES** | NO | `admin` | `test_eco_promotions_packages_p11b.py` | None. Package creation, toggle, campaign approval/rejection. |
| **A08** | Reports | Basic eco-tourism, booking and revenue reporting | **COMPLETE** | `app/routers/eco_experiences.py`, `app/routers/eco_promotions.py` | `GET /admin/eco-experiences/overview`, `GET /admin/eco-promotions/analytics` | **YES** | NO | `admin` | `test_eco_dashboard_p07.py`, `test_eco_phase_5a_metrics_and_earnings.py` | None. Comprehensive GMV, listings, status, and district reporting. |

---

### D. Six Eco-Specific Requirements (E01–E06)

| ID | Exact Feature Name | PDF Required Behavior | Status | Implementation File | Endpoint & Method | Mounted? | 503 Guard? | Auth / Role | Test Evidence | Missing Functionality / Notes |
| :---: | :--- | :--- | :---: | :--- | :--- | :---: | :---: | :--- | :--- | :--- |
| **E01** | Environmental Guidelines | Show site-specific responsible travel instructions | **COMPLETE** | `app/routers/eco_experiences.py` | `environmental_guidelines` on experience CRUD | **YES** | NO | Public/Provider | `test_eco_experience_core.py` | None. Displayed on public listing; enforced on creation. |
| **E02** | Wildlife Safety Rules | Document safety precautions, no-feeding rules | **COMPLETE** | `app/routers/eco_experiences.py` | `POST /{id}/safety-rules`, `GET /{id}/safety-rules` | **YES** | NO | Public/Provider | `test_eco_experience_core.py` | None. Rule types (`wildlife`, `safety`), severity levels, descriptions. |
| **E03** | Group Size Limits | Enforce environmentally appropriate max per slot | **COMPLETE** | `app/routers/eco_experiences.py`, `app/services/eco_slot_validation.py` | `min_participants`, `max_group_size` | **YES** | NO | System | `test_eco_slot_validation.py` | None. Validates slot capacity does not exceed listing max group size. |
| **E04** | Conservation Contribution | Explain conservation benefit; factual claims | **COMPLETE** | `app/routers/eco_experiences.py` | `conservation_contribution`, `conservation_partner` | **YES** | NO | Public/Provider | `test_eco_experience_core.py` | None. Text and partner fields distinguish claims from reality. |
| **E05** | Eco-Friendly Practices | Store sustainability practices & evidence for admin | **COMPLETE** | `app/routers/eco_experiences.py` | `EcoPractice` and `EcoEvidence` models and endpoints | **YES** | NO | Provider/Admin | `test_eco_verification_p03.py` | None. Category mapping, document uploads, admin review trail. |
| **E06** | Weather & Seasonal Restrictions | Block unsuitable seasons; enable cancellation with reason | **PARTIAL** | `app/routers/eco_experiences.py`, `app/services/eco_slot_validation.py` | `POST /{id}/weather-emergency` | **YES** | NO | `provider` | `test_eco_phase_5d_weather_cancellation.py` | Pre-booking check & experience-level endpoint complete (13 tests pass); slot-level cascade and live cancellation pending Gate 4. |

---

## 2. Status Summary Counts

| Category | Requirements | COMPLETE | STAGED | PARTIAL | BLOCKED | NOT STARTED |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **Traveler (T01–T18)** | 18 | 11 | 5 | 1 | 1 | 0 |
| **Provider (P01–P18)** | 18 | 13 | 5 | 0 | 0 | 0 |
| **Admin (A01–A08)** | 8 | 8 | 0 | 0 | 0 | 0 |
| **Eco-Specific (E01–E06)** | 6 | 5 | 0 | 1 | 0 | 0 |
| **TOTAL** | **50** | **37** | **10** | **2** | **1** | **0** |
| **Percentage** | **100.0%** | **74.0%** | **20.0%** | **4.0%** | **2.0%** | **0.0%** |
