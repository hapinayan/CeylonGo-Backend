# Module 03 — Requirements B15 & B16 Implementation & Schema Assessment

- **Project:** CeylonGo Backend
- **Module:** Module 03 – Activities & Experiences
- **Requirements Reference:**
  - **B15 — Activity-Specific Discount Packages**
  - **B16 — Activity-Specific Sponsored Campaigns**
- **Phase:** 5E-3 / 5E-3A
- **Status:** **PARTIAL** (Validation, preview simulations, tenant isolation, and nonnegative pricing complete; durable activity-specific database FK targeting deferred pending physical database migration)
- **Date:** November 2026

---

## 1. Executive Summary & Safety Policy

In Phase 5E-3 and 5E-3A, the service, schema, and API layers for **B15** (Activity-Specific Discount Packages) and **B16** (Activity-Specific Sponsored Campaigns) were reviewed and aligned with SQL Server physical schema boundaries.

Because the underlying shared development database (`CeylonGoDB_Development`) has only applied migrations `001` through `029`:
1. `dbo.promotion_packages` physically lacks an `activity_id` column.
2. `dbo.promotion_campaigns` physically lacks a `listing_reference_id` column (defined in unapplied migration `030`).
3. Physical check constraints on `dbo.promotion_campaigns.status` permit only `'draft', 'pending_approval', 'active', 'paused', 'completed', 'cancelled'`.

### Strict Targeting Safety Rules Implemented:
1. **No False Persistence Claims:** An `activity_id` supplied to persistent endpoints (`POST /discounts`, `POST /promotions/campaigns`, `PATCH ...`) is **never** silently dropped, nor is it falsely represented as saved. Instead, persistent requests containing `activity_id` fail closed with an explicit `HTTP 422 Unprocessable Content` migration-pending error.
2. **Explicit Preview Simulation Distinction:** Activity-targeted pricing and reach simulations are served exclusively through dedicated preview endpoints (`POST /discounts/preview`, `POST /promotions/campaigns/preview`). Their responses explicitly identify the operation as `activity_targeting_status="PREVIEW_ONLY_MIGRATION_PENDING"` and `is_simulation=True`.
3. **No False Activity Association in Read Endpoints:** Activity-specific list endpoints (`GET /activities/{activity_id}/discounts`, `GET /activities/{activity_id}/promotions/campaigns`) return empty lists `[]` rather than falsely returning provider-wide promotions as if they were linked to that individual activity.
4. **Reach Estimates as Simulations:** Campaign impression and click projections are explicitly labeled as algorithmic simulations, not guaranteed delivery outcomes.

---

## 2. Requirement B15: Activity-Specific Discount Packages

### 2.1 Schema-Safe Execution Pattern
- **Request Models:**
  - `ActivityDiscountCreate`: Supports optional `activity_id: int`. If supplied, fails closed with HTTP 422 indicating persistent targeting is pending Migration 041.
  - `ActivityDiscountPreviewRequest`: Dedicated payload to simulate discount pricing.
  - `ActivityDiscountPreviewResponse`: Returns original price, calculated discounted price, validity dates, eligibility status, targeting status, and simulation notice.
- **Validation Rules:**
  1. **Provider Ownership:** Verifies `activity.provider_profile_id == profile.id`. Cross-tenant attempts return `403 Forbidden`. Nonexistent activities return `404 Not Found`.
  2. **Activity Eligibility:** Rejects draft, pending, or rejected activities (`approval_status != 'approved'`) with `422`. Rejects inactive activities with `422`.
  3. **Nonnegative Pricing Rule:**
     $$\text{discounted\_price} = \max(0, \text{activity.price} - \text{discount\_amount})$$
     If `discount_amount > activity.price`, rejected immediately with `422 Unprocessable Content`.
  4. **Percentage Boundaries:** $0 < \text{discount\_percent} \le 100$.
  5. **Date Window Rules:** `valid_to >= valid_from` and `valid_to >= date.today()`.
  6. **Conflict Detection:** Rejects overlapping active discounts with the same title for the provider.

---

## 3. Requirement B16: Activity-Specific Sponsored Campaigns

### 3.1 Schema-Safe Execution Pattern
- **Request Models:**
  - `ActivityCampaignCreate`: Supports optional `activity_id: int`. If supplied, fails closed with HTTP 422 indicating persistent targeting is pending Migration 030/041.
  - `ActivityCampaignPreviewRequest`: Simulates campaign burn rate, duration, and reach.
  - `ActivityCampaignPreviewResponse`: Returns daily budget, duration in days, estimated impressions (~10/LKR), estimated clicks (~3% CTR), and explicit simulation disclaimers.
- **Validation Rules:**
  1. **Provider Ownership:** Rejects unowned activities with `403 Forbidden`.
  2. **Activity Eligibility:** Requires `approval_status == 'approved'` and `is_active == True`.
  3. **Budget Boundaries:** Minimum budget threshold of LKR 500.00. Budgets $\le 0$ rejected with `422`.
  4. **Schedule Boundaries:** `ends_at > starts_at` and `ends_at > now_utc`.
  5. **Status Transition & Anti-Self-Activation:**
     - Initial submission: `pending_approval` (when `submit_for_review=True`) or `draft`.
     - Direct self-activation (`status='active'`) by providers is strictly rejected with `403 Forbidden`.

---

## 4. Endpoints Summary

| Method | Path | Summary | Scope |
| :--- | :--- | :--- | :---: |
| `POST` | `/api/v1/provider/activities/discounts` | Create provider-wide discount (rejects `activity_id` pending Migration 041) | Provider-wide |
| `POST` | `/api/v1/provider/activities/discounts/preview` | Preview activity discount pricing & eligibility | Activity Simulation |
| `GET` | `/api/v1/provider/activities/activities/{activity_id}/discounts` | List activity-linked discounts (returns `[]` pending Migration 041) | Activity List |
| `POST` | `/api/v1/provider/activities/promotions/campaigns` | Create provider-wide campaign (rejects `activity_id` pending Migration 030/041) | Provider-wide |
| `POST` | `/api/v1/provider/activities/promotions/campaigns/preview` | Preview activity campaign reach & daily budget | Activity Simulation |
| `GET` | `/api/v1/provider/activities/activities/{activity_id}/promotions/campaigns` | List activity-linked campaigns (returns `[]` pending Migration 030/041) | Activity List |

---

## 5. Physical Schema Blockers & Migration Roadmap

| Blocker | Affected Table | Physical Reality | Target Migration |
| :--- | :--- | :--- | :---: |
| Direct Activity FK on Packages | `dbo.promotion_packages` | No `activity_id` or `listing_reference_id` column. | Migration 041 |
| Direct Listing FK on Campaigns | `dbo.promotion_campaigns` | No `listing_reference_id` column (Migration 030 unapplied). | Migration 030 / 041 |
| Status Constraint Modernization | `dbo.promotion_campaigns` | `CK_promotion_campaigns_status` only permits 6 statuses. | Migration 031 / 041 |

Because physical foreign key columns cannot be queried or stored without database migration, Requirements **B15** and **B16** are accurately retained as **PARTIAL** in the requirements ledger until durable activity targeting is implemented and tested.
