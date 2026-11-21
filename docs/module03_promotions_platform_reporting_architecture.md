# CEYLONGO MODULE 03: ACTIVITIES & EXPERIENCES
## PHASE 5D-1: ARCHITECTURAL DESIGN, PROMOTIONS SCHEMA & SQL SERVER SAFETY SPECIFICATION

**Auditor & Architect Roles:** Senior Backend Architect, SQL Server Database Specialist, Security Engineer, QA Lead
**Specification Date:** 21 November 2026
**Project Workspace:** `C:\Users\komaruthan\Desktop\project 4\CeylonGo-Backend`
**Target Branch:** `feature/m03-promotions-platform-reporting`
**Base Commit:** `9dbc88c` (`docs(m03): add post-phase-5c canonical requirements gap audit`)
**Baseline Test Status:** 236 passed, 0 failed, 0 skipped, 14 warnings
**Database Guard:** Read-Only Schema Inspection; Zero Live Database Modifications on `CeylonGoDB_Development`

---

## 1. EXECUTIVE SUMMARY & OBJECTIVES

Following the successful execution, verification, and local commitment of Phases 1 through 5C (culminating in commit `9dbc88c`), this document establishes the production-grade architectural blueprint for **Phase 5D** of Module 03 (Activities & Experiences).

Phase 5D focuses exclusively on the **seven remaining non-payment requirements** identified in the Post-Phase 5C Requirements Gap Analysis:
* **A17 – Sponsored Activities** (Public Discovery & Sponsored Ranking Boost)
* **A18 – Special Offers & Discounts** (Promotional Discount Display & Price Evaluation)
* **B15 – Provider Discount Offers** (Provider Discount Campaign Management)
* **B16 – Sponsored Promotion Applications** (Provider Sponsored Placement Workflows)
* **B18 – Promotion Statistics** (Campaign Views, Clicks, CTR & Conversion Analytics)
* **C06 – Admin Promotion Management** (Admin Campaign Review, Approval & Oversight)
* **C07 – Platform-Wide Activity Reporting** (Admin Executive Reporting, Geographics, Categories & GMV)

### Strictly Observed Constraints
1. **Zero Feature Implementation in Review Turn:** This document is purely architectural design and schema verification.
2. **Strict Read-Only Database Safety:** No migrations executed, no tables altered/created, no records inserted/updated/deleted in `CeylonGoDB_Development`.
3. **No External Payment Gateway Dependencies:** All Phase 5D capabilities operate independently of the blocked PayHere merchant gateway (Module 05).

---

## 2. CANONICAL REQUIREMENTS SPECIFICATION & SCOPE MAPPING

Every targeted requirement is derived directly from the primary project authority: *CeylonGo Activities Complete Development Specification* (`CeylonGo_Activities_Complete_Development_Specification.pdf`, 17 Nov 2026).

| Req ID | Canonical Title | Canonical Specification | Target Consumer Layer | Backend Responsibility & Implementation Scope |
|---|---|---|:---:|---|
| **A17** | Sponsored Activities | View clearly marked sponsored listings | Traveler (Flutter / Web) | Query active campaigns in `dbo.promotion_campaigns` (`target_module = 'activity'`, `status = 'active'`, active date window). Flag `is_sponsored = True` in public activity cards and details. Prioritize sponsored items in discovery sorting. |
| **A18** | Special Offers | See valid promotional discounts | Traveler (Flutter / Web) | Retrieve active provider discounts from `dbo.promotion_packages`. Compute server-side discounted prices (`discount_percent` or `discount_amount`). Return `has_discount`, `original_price`, `discounted_price`, and `discount_label`. Enforce discount bounds on booking checkout. |
| **B15** | Discount Offers | Manage discounts and validity | Service Provider (Web) | Provider endpoints to create, list, inspect, toggle active status, and deactivate promotional discount campaigns in `dbo.promotion_packages`. Validate date ranges (`valid_to >= valid_from`), percent limits (0–100%), and non-negative currency amounts. |
| **B16** | Sponsored Promotions | Purchase promotional packages | Service Provider (Web) | Provider endpoints to configure and submit sponsored campaign requests targeting owned activities. Creates row in `dbo.promotion_campaigns` (`target_module = 'activity'`, `status = 'pending_approval'`). Supports budget tracking and campaign cancellation. |
| **B18** | Promotion Statistics | Views, clicks and booking conversions | Service Provider (Web) | Provider analytics endpoint aggregating event counts from `dbo.promotion_events` (`impression`, `click`, `booking`) for owned campaigns. Computes click-through rate (CTR), conversion rate, and budget burn. Public event-recording endpoint with client IP rate-limiting. |
| **C06** | Promotion Management | Manage sponsored campaigns and packages | Admin Portal (Web) | Admin endpoints to review, inspect, approve, reject (with mandatory justification), pause, or resume activity promotion campaigns. Persists immutable action history to `dbo.admin_audit_logs`. |
| **C07** | Reports | Basic activity, booking and revenue reporting | Admin Portal (Web) | Platform-wide administrative reporting endpoint (`GET /api/v1/admin/activities/reports`). Aggregates activity inventory by approval status, category distribution, regional district heatmaps, booking volume, cancellation statistics, and multi-currency gross financial performance. |

---

## 3. LIVE SQL SERVER SCHEMA AUDIT & CRITICAL DRIFT FINDINGS

A metadata-only inspection was conducted against the live Microsoft SQL Server 2025 instance (`CeylonGoDB_Development`) via `INFORMATION_SCHEMA` and `sys.*` catalog views.

### Live Table Schema Summary

```
------------------------------------------------------------------------------------------------------------------------
Table Name                Physical Columns                                      Primary Key   Foreign Key Constraints
------------------------------------------------------------------------------------------------------------------------
dbo.promotion_packages    id, provider_profile_id, title, description,          id            FK_promotion_packages_provider
                          discount_percent, discount_amount, currency,                        -> provider_profiles(id) [CASCADE]
                          valid_from, valid_to, is_active, created_at
------------------------------------------------------------------------------------------------------------------------
dbo.promotion_campaigns   id, provider_profile_id, campaign_name,               id            FK_promotion_campaigns_provider
                          target_module, budget, spent_amount, currency,                      -> provider_profiles(id) [CASCADE]
                          status, starts_at, ends_at, created_at
------------------------------------------------------------------------------------------------------------------------
dbo.promotion_events      id, campaign_id, event_type, user_id,                 id            FK_promotion_events_campaign
                          ip_address, created_at                                              -> promotion_campaigns(id) [CASCADE]
                                                                                              FK_promotion_events_user
                                                                                              -> users(id) [NO ACTION]
------------------------------------------------------------------------------------------------------------------------
dbo.activities            id, provider_profile_id, title, description,          id            FK_activities_provider
                          category, district, city, meeting_point,                            -> provider_profiles(id)
                          latitude, longitude, duration_minutes,
                          price, pricing_type, currency, max_participants,
                          min_participants, minimum_age, safety_rules,
                          requirements, inclusions, exclusions,
                          cancellation_policy, is_active,
                          approval_status, created_at, updated_at
------------------------------------------------------------------------------------------------------------------------
dbo.activity_slots        id, activity_id, starts_at, ends_at, capacity,        id            FK_activity_slots_activity
                          reserved_count, is_active, created_at,                              -> activities(id)
                          eco_experience_id (legacy nullable)
------------------------------------------------------------------------------------------------------------------------
dbo.activity_bookings     id, activity_slot_id, user_id, participants,          id            FK_activity_bookings_slot
                          total_price, currency, payment_method,                              -> activity_slots(id)
                          payment_status, booking_status,                                     FK_activity_bookings_user
                          cancellation_reason, cancelled_at,                                  -> users(id)
                          created_at, updated_at, eco_experience_id
------------------------------------------------------------------------------------------------------------------------
dbo.listing_references    id, module_type, target_id, provider_profile_id,      id            FK_listing_references_provider
                          title, district, city, latitude, longitude,                         -> provider_profiles(id)
                          is_active, created_at
------------------------------------------------------------------------------------------------------------------------
dbo.admin_audit_logs      id, admin_user_id, action_type, entity_type,          id            FK_admin_audit_logs_user
                          entity_id, details, ip_address, created_at                          -> users(id)
------------------------------------------------------------------------------------------------------------------------
```

### Critical Discovery: Schema Drift vs. SQLAlchemy ORM Models

An empirical test query against `CeylonGoDB_Development` revealed a crucial schema mismatch:

1. **Applied Database Migration Baseline:**
   * `dbo.schema_migrations` records that **migrations 001 through 029** are applied.
   * This perfectly reflects the baseline established in the *CeylonGo Master Database Documentation V1.0*.
2. **Missing Physical Column in `dbo.promotion_campaigns`:**
   * In `app/models/shared.py` (line 215), `PromotionCampaign` declares `listing_reference_id: Mapped[int | None]`.
   * This column was scripted in `migrations/030_extend_homestay_final_requirements.sql` (Batch 7), but migration 030 has **never been executed on `CeylonGoDB_Development`**.
   * Consequently, running `db.query(PromotionCampaign).all()` against `CeylonGoDB_Development` raises:
     ```
     (pyodbc.ProgrammingError) ('42S22', "[42S22] [Microsoft][ODBC Driver 18 for SQL Server][SQL Server]Invalid column name 'listing_reference_id'. (207)")
     ```
3. **Missing Physical Column in `dbo.promotion_events`:**
   * In `app/models/shared.py` (line 253), `PromotionEvent` declares `booking_reference_id: Mapped[int | None]`.
   * This column was scripted in `migrations/031_extend_promotion_booking_attribution.sql`, which has also **not been executed on `CeylonGoDB_Development`**.
   * Consequently, querying `db.query(PromotionEvent).all()` raises:
     ```
     (pyodbc.ProgrammingError) ('42S22', "[42S22] [Microsoft][ODBC Driver 18 for SQL Server][SQL Server]Invalid column name 'booking_reference_id'. (207)")
     ```

### Dual-Compatibility Architectural Solution (Non-Destructive & Safe)

Because modifying the live development database or running ad-hoc SQL migrations is strictly prohibited by our instructions, the Phase 5D architecture adopts a **Zero-Drift Column-Safe Pattern**:

1. **ORM Query Deferral / Projection:**
   * When querying `PromotionCampaign` against `CeylonGoDB_Development`, queries must either use `options(defer(PromotionCampaign.listing_reference_id))` or construct explicit column projections (`load_only(...)`).
   * When querying `PromotionEvent`, queries must use `options(defer(PromotionEvent.booking_reference_id))`.
   * Unit and integration test suites running on SQLite (`sqlite:///:memory:`) will continue to operate transparently because SQLite generates the in-memory schema directly from the full ORM model definitions.
2. **Activity Linking Mechanism:**
   * A campaign is bound to the activity provider via `provider_profile_id` and identified by `target_module = "activity"`.
   * To associate a campaign with a specific activity without depending on the unmigrated column:
     * Store the structured identifier in `campaign_name`: `"[Activity:{id}] {title}"`.
     * If the database environment contains `listing_reference_id` (detected dynamically or in SQLite tests), populate `listing_reference_id = activity_listing_ref.id`.
     * This provides 100% resilience across unmigrated development databases, SQLite test suites, and future fully-migrated production clusters.

---

## 4. ARCHITECTURAL COMPONENT DESIGN

```
+---------------------------------------------------------------------------------------------------+
|                                          FASTAPI ROUTERS                                          |
|                                                                                                   |
|  +----------------------------+  +----------------------------+  +-----------------------------+  |
|  |   app.routers.activities   |  |   app.routers.provider_    |  |     app.routers.admin_      |  |
|  |        (Public)            |  |         activities         |  |         activities          |  |
|  |                            |  |                            |  |                             |  |
|  | GET /activities (A17/A18)  |  | POST/GET /discounts  (B15) |  | GET /promotions/campaigns   |  |
|  | GET /activities/{id}       |  | POST/GET /promotions (B16) |  | POST /campaigns/{id}/action |  |
|  | POST /{id}/events   (B18)  |  | GET /promotions/{id}/stats |  | GET /reports/overview (C07) |  |
|  +--------------+-------------+  +--------------+-------------+  +--------------+--------------+  |
+-----------------|-------------------------------|-------------------------------|-----------------+
                  |                               |                               |
                  v                               v                               v
+---------------------------------------------------------------------------------------------------+
|                                           SERVICE LAYER                                           |
|                                                                                                   |
|     app.services.activity_promotions                     app.services.activity_reports            |
|     - check_active_promotions()                          - generate_platform_activity_report()    |
|     - apply_activity_discount()                          - aggregate_inventory_metrics()          |
|     - record_promotion_event()                           - aggregate_booking_conversions()        |
|     - calculate_campaign_analytics()                     - aggregate_financial_performance()      |
+---------------------------------------------------------------------------------------------------+
                                                  |
                                                  v
+---------------------------------------------------------------------------------------------------+
|                                     SQL SERVER DATABASE SCHEMA                                    |
|                                                                                                   |
|   dbo.promotion_packages     dbo.promotion_campaigns     dbo.promotion_events     dbo.activities  |
|   dbo.activity_slots         dbo.activity_bookings       dbo.listing_references   dbo.audit_logs  |
+---------------------------------------------------------------------------------------------------+
```

### Component 1: `app.services.activity_promotions`
Encapsulates all campaign evaluation, discount arithmetic, event recording, and metrics computation:
* `get_active_sponsored_activity_ids(db: Session) -> set[int]`: Returns set of activity IDs currently running active, approved campaigns within valid `[starts_at, ends_at]` UTC windows.
* `get_activity_discount(activity: Activity, db: Session) -> tuple[Decimal, Decimal, str | None]`: Resolves the best active discount from the provider's active packages, returning `(original_price, discounted_price, discount_label)`.
* `record_campaign_event(campaign_id: int, event_type: str, user_id: int | None, ip_address: str | None, db: Session)`: Idempotently registers impression or click events.
* `get_campaign_statistics(campaign: PromotionCampaign, db: Session) -> CampaignStats`: Aggregates impression count, click count, booking count, CTR, and conversion rate.

### Component 2: `app.services.activity_reports`
Encapsulates executive reporting aggregations for Admin Requirement C07:
* Executes high-performance group-by and aggregate queries on `dbo.activities` and `dbo.activity_bookings`.
* Calculates booking conversion rates:
  $$\text{conversion\_rate} = \frac{\text{confirmed\_bookings} + \text{completed\_bookings}}{\text{total\_bookings}} \times 100\%$$
* Calculates platform cancellation rates:
  $$\text{cancellation\_rate} = \frac{\text{cancelled\_bookings}}{\text{total\_bookings}} \times 100\%$$
* Partitions financial volume by currency (`LKR`, `USD`) to guarantee financial accuracy.

---

## 5. REST API CONTRACT SPECIFICATIONS

### A. Traveler Endpoints (A17, A18, B18)

#### 1. Public Discovery with Sponsored Boost & Discounts
* **Route:** `GET /api/v1/activities`
* **Query Parameters:** Existing filters (`category`, `district`, `min_price`, `max_price`, `duration`, `search`) + pagination (`page`, `limit`).
* **Enhanced Response Model (`ActivityResponse`):**
  ```json
  {
    "id": 101,
    "title": "Kithulgala White Water Rafting",
    "category": "adventure",
    "district": "Kegalle",
    "city": "Kithulgala",
    "duration_minutes": 180,
    "price": 6000.00,
    "pricing_type": "per_person",
    "currency": "LKR",
    "is_sponsored": true,
    "has_discount": true,
    "original_price": 6000.00,
    "discounted_price": 4800.00,
    "discount_label": "20% OFF Early Bird Promotion",
    "cover_image_url": "/uploads/activities/rafting_cover.jpg",
    "average_rating": 4.85,
    "review_count": 28
  }
  ```
* **Ordering Behavior:** Sponsored listings (`is_sponsored == true`) appear at the top of default public discovery rankings, followed by rating and creation recency.

#### 2. Public Promotion Event Tracking
* **Route:** `POST /api/v1/activities/{activity_id}/promotions/events`
* **Request Payload:**
  ```json
  {
    "event_type": "click"  // "impression" or "click"
  }
  ```
* **Status Code:** `204 No Content` (Fast fail-silent delivery).

---

### B. Service Provider Endpoints (B15, B16, B18)

#### 1. Provider Discount Management (B15)
* `POST /api/v1/provider/activities/discounts` (Create promotional discount package)
* `GET /api/v1/provider/activities/discounts` (List owned discount packages)
* `GET /api/v1/provider/activities/discounts/{id}` (Get discount detail)
* `PATCH /api/v1/provider/activities/discounts/{id}` (Update discount details / toggle active status)
* `DELETE /api/v1/provider/activities/discounts/{id}` (Deactivate package)

**Discount Creation Payload (`ActivityDiscountCreate`):**
```json
{
  "title": "Summer Adventure Special",
  "description": "20% discount on weekday adventure bookings",
  "discount_percent": 20.0,
  "discount_amount": null,
  "currency": "LKR",
  "valid_from": "2026-12-01",
  "valid_to": "2026-12-31",
  "is_active": true
}
```

#### 2. Provider Sponsored Campaign Applications (B16)
* `POST /api/v1/provider/activities/promotions/campaigns` (Apply for sponsored placement)
* `GET /api/v1/provider/activities/promotions/campaigns` (List owned campaigns with status filter)
* `GET /api/v1/provider/activities/promotions/campaigns/{id}` (Get campaign detail)
* `POST /api/v1/provider/activities/promotions/campaigns/{id}/cancel` (Cancel pending/approved campaign)

**Campaign Application Payload (`ActivityCampaignCreate`):**
```json
{
  "activity_id": 101,
  "campaign_name": "Peak Season Rafting Boost",
  "budget": 5000.00,
  "currency": "LKR",
  "starts_at": "2026-12-01T00:00:00Z",
  "ends_at": "2026-12-15T23:59:59Z"
}
```

#### 3. Provider Promotion Statistics (B18)
* `GET /api/v1/provider/activities/promotions/campaigns/{id}/analytics`
* **Response Model (`ActivityCampaignAnalyticsResponse`):**
  ```json
  {
    "campaign_id": 12,
    "activity_id": 101,
    "campaign_name": "Peak Season Rafting Boost",
    "status": "active",
    "budget": 5000.00,
    "spent_amount": 1250.00,
    "currency": "LKR",
    "starts_at": "2026-12-01T00:00:00Z",
    "ends_at": "2026-12-15T23:59:59Z",
    "impressions_count": 4500,
    "clicks_count": 360,
    "booking_conversions_count": 28,
    "click_through_rate": 8.00,
    "conversion_rate": 7.78
  }
  ```

---

### C. Admin Endpoints (C06, C07)

#### 1. Admin Promotion Moderation (C06)
* `GET /api/v1/admin/activities/promotions/campaigns` (List cross-provider campaigns)
* `GET /api/v1/admin/activities/promotions/campaigns/{id}` (View campaign with provider & activity details)
* `POST /api/v1/admin/activities/promotions/campaigns/{id}/approve` (Approve campaign)
* `POST /api/v1/admin/activities/promotions/campaigns/{id}/reject` (Reject with mandatory reason)
* `POST /api/v1/admin/activities/promotions/campaigns/{id}/pause` (Pause active campaign)
* `POST /api/v1/admin/activities/promotions/campaigns/{id}/resume` (Resume paused campaign)

#### 2. Platform-Wide Activity Reporting (C07)
* `GET /api/v1/admin/activities/reports`
* **Query Parameters:** `date_from`, `date_to`, `start_date`, `end_date` (optional date window).
* **Response Model (`AdminActivityPlatformReportResponse`):**
  ```json
  {
    "generated_at": "2026-11-21T10:30:00Z",
    "period_from": null,
    "period_to": null,
    "activity_inventory": {
      "total_activities": 45,
      "approved_activities": 38,
      "pending_activities": 4,
      "draft_activities": 2,
      "suspended_activities": 1,
      "rejected_activities": 0,
      "active_published_activities": 36
    },
    "category_distribution": [
      { "category": "adventure", "activity_count": 18, "booking_count": 142, "gross_revenue": 852000.00 },
      { "category": "cultural", "activity_count": 14, "booking_count": 98, "gross_revenue": 392000.00 },
      { "category": "local", "activity_count": 8, "booking_count": 45, "gross_revenue": 135000.00 },
      { "category": "community", "activity_count": 5, "booking_count": 22, "gross_revenue": 66000.00 }
    ],
    "regional_distribution": [
      { "district": "Kandy", "activity_count": 12, "booking_count": 110 },
      { "district": "Kegalle", "activity_count": 10, "booking_count": 95 },
      { "district": "Galle", "activity_count": 8, "booking_count": 42 }
    ],
    "booking_overview": {
      "total_bookings": 307,
      "confirmed_bookings": 215,
      "completed_bookings": 62,
      "pending_bookings": 18,
      "cancelled_bookings": 10,
      "rejected_bookings": 2,
      "total_participants": 784,
      "conversion_rate": 90.23,
      "cancellation_rate": 3.26
    },
    "financial_overview": [
      {
        "currency": "LKR",
        "total_gross_revenue": 1445000.00,
        "paid_revenue": 1280000.00,
        "unpaid_revenue": 165000.00,
        "refunded_amount": 24000.00
      }
    ],
    "top_activities": [
      { "activity_id": 101, "title": "White Water Rafting", "booking_count": 84, "gross_revenue": 504000.00 }
    ],
    "top_providers": [
      { "provider_profile_id": 5, "business_name": "Lanka Adventures", "active_listings": 4, "booking_count": 120 }
    ]
  }
  ```

---

## 6. SECURITY, MULTI-TENANCY & CONCURRENCY CONTROLS

1. **Multi-Tenant Ownership Isolation:**
   * Every provider promotion query validates `Activity.provider_profile_id == provider_profile.id`.
   * Providers cannot view, inspect, or manage campaigns or discounts owned by other providers (HTTP 404/403 fail-closed).
2. **Admin-Only Guard:**
   * Moderation and platform-wide reporting endpoints require `require_admin` dependency.
   * Review actions record admin ID, UTC timestamp, and justification to `dbo.admin_audit_logs`.
3. **Discount Price Safety & Constraints:**
   * Discounts are calculated server-side; client cannot dictate discounted totals.
   * Negative prices are prevented via `max(Decimal("0.00"), computed_price)`.
   * Valid date windows (`valid_from <= today <= valid_to`) and active flags are strictly enforced.
4. **Promotion Event Rate Limiting & Cooldown:**
   * Click/impression spam is mitigated via in-memory or database cooldown windows based on IP hash / user ID.
5. **SQL Server Concurrency:**
   * Campaign approvals and budget updates utilize transaction blocks with safe row commits.
   * Read-only reporting queries utilize optimal joins and aggregate functions, avoiding table locks.

---

## 7. QA TESTING & REGRESSION VERIFICATION STRATEGY

To maintain the verified baseline of **236 passed tests**, Phase 5D will add dedicated test suites:
* `tests/test_activity_promotions_discounts.py` (Unit & contract tests for A17, A18, B15, B16, B18, C06).
* `tests/test_admin_activity_reporting.py` (Unit & contract tests for C07 platform-wide reporting).
* Verification against existing integration test suites (`test_activity_calendar_analytics.py`, `test_m04_m03_integration_contract.py`).

---

## 8. SUMMARY CONCLUSION & HANDOFF READY

Phase 5D-1 architectural analysis and schema audit are complete.
* All 7 missing requirements are fully specified with strict adherence to the canonical PDF specification.
* Live SQL Server inspection on `CeylonGoDB_Development` identified the critical schema drift in `dbo.promotion_campaigns` and `dbo.promotion_events`, and a Zero-Drift Column-Safe architecture has been designed to guarantee complete stability across both unmigrated development databases and SQLite test environments.
* The repository is prepared on branch `feature/m03-promotions-platform-reporting` at commit `9dbc88c`.

---

## 9. PHASE 5D-2A VERIFICATION & READINESS RECORD (C07)

1. **Implementation Completed:**
   - Endpoint `GET /api/v1/admin/activities/reports` added to `app/routers/admin_activities.py` before `/{activity_id}`.
   - Pydantic schemas added to `app/schemas/activity_admin.py`.
   - Core service `generate_platform_activity_report` implemented in `app/services/activity_reports.py`.
2. **Performance Optimizations:**
   - Catalog inventory selects only 8 scalar attributes (`id`, `title`, `category`, `district`, `provider_profile_id`, `currency`, `approval_status`, `is_active`), avoiding large text columns (`description`, `itinerary`, `safety_rules`).
   - Slot capacity utilization uses direct database SQL aggregation (`func.count`, `func.sum`, `func.coalesce`, `case`), completely avoiding in-memory slot loading.
   - Deterministic multi-attribute tie-breaking implemented for top activities, top providers, and regional distributions.
3. **Live SQL Server 2025 Verification:**
   - Verified physical columns and read-only query execution on `CeylonGoDB_Development` with zero DDL or data changes.
4. **Test Suite Baseline:**
   - `tests/test_admin_activity_reporting.py` (18 passed).
   - Full 16-file regression suite: **254 passed, 0 failed, 17 warnings**.
