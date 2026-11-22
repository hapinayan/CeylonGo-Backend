# CeylonGo Module 03 – Phase 5D-2D API Contract
## Admin Promotion Management Foundation (C06)

**Module:** Module 03 – Activities & Experiences<br/>
**Phase:** 5D-2D – Admin Promotion Management Foundation<br/>
**Requirement Covered:** **C06 – Admin Promotion Management** (PARTIAL Foundation – Read-Only Listing, Detail Inspection, and Reusable Decision Schemas; Write Endpoints Deferred to Migration 031 Readiness)<br/>
**Document Version:** 1.0<br/>
**Date:** 21 November 2026<br/>
**Repository Branch:** `feature/m03-promotions-platform-reporting`<br/>
**Current HEAD:** `cd87f9a`<br/>
**Regression Baseline:** 300 passed (276 existing + 24 new tests in `tests/test_admin_activity_promotions.py`), 0 failed, 0 skipped (`TZ=UTC`)<br/>
**Shared Database Inspected:** `CeylonGoDB_Development` (Microsoft SQL Server 2025 Express RTM 17.0.1000.7)

---

## 1. Executive & Architectural Summary

Phase 5D-2D establishes the administrative foundation for **C06 (Admin Promotion Management)**. It enables platform administrators to discover, monitor, inspect, and review activity sponsored campaigns submitted by providers across the entire platform.

### Core Architectural Guarantees & Schema Compliance

1. **Physical SQL Server 2025 Express Compliance (Zero DDL Drift):**
   - **`dbo.promotion_campaigns` (11 physical columns):** The physical table in `CeylonGoDB_Development` contains strictly 11 columns: `id`, `provider_profile_id`, `campaign_name`, `target_module`, `budget`, `spent_amount`, `currency`, `status`, `starts_at`, `ends_at`, and `created_at`.
   - **Unmigrated Column Isolation:** Column `listing_reference_id` (defined in unapplied Migration 030) and column `booking_reference_id` (defined in unapplied Migration 031) do NOT physically exist on the shared SQL Server database. All queries use explicit SQLAlchemy Core scalar projections (`CAMPAIGN_PHYSICAL_COLUMNS`) matching strictly the physical schema, preventing SQL Server error `207 (Invalid column name 'listing_reference_id')`.
2. **Server-Side Module Isolation (`target_module = 'activity'`):**
   - The query unconditionally filters on `PromotionCampaign.target_module == 'activity'`.
   - The client cannot override or bypass this constraint.
   - Campaigns belonging to other platform modules (e.g. `homestay`, `artisan`, `tour_guide`) are strictly filtered out of listings.
   - Detail requests for non-activity campaign IDs return `404 Not Found`, completely preventing cross-module metadata leakage.
3. **Role-Based Access Control (RBAC):**
   - All endpoints are strictly guarded by `require_admin` dependency (`user.role == 'admin'` and `user.is_active == True`).
   - Anonymous callers receive `401 Unauthorized`.
   - Authenticated travelers and service providers receive `403 Forbidden` (`"Access forbidden: admin role required"`).
4. **Targeting Transparency:**
   - In accordance with Phase 5D-2B and 5D-2C architectural findings, activity-specific targeting is constrained by the current physical schema.
   - Responses transparently emit:
     ```json
     {
       "targeting_scope": "provider",
       "activity_targeting_status": "BLOCKED_BY_SCHEMA"
     }
     ```
   - No fake or fabricated `activity_id` or `listing_reference_id` values are generated.
5. **Read-Only Invariant (Zero State Mutations):**
   - The physical check constraint `CK_promotion_campaigns_status` on `CeylonGoDB_Development` permits only: `'draft', 'pending_approval', 'active', 'paused', 'completed', 'cancelled'`. Setting status to `'approved'` or `'rejected'` triggers check constraint violation `547`.
   - Therefore, write endpoints (campaign approval and rejection) are strictly deferred to Phase 5D-4 after Migration 031 is applied. Zero database writes, status transitions, or campaign mutations occur in Phase 5D-2D.
6. **Route Shadowing Prevention:**
   - In `app/routers/admin_activities.py`, the endpoints `/promotions/campaigns` and `/promotions/campaigns/{campaign_id}` are mounted **before** dynamic path route `/{activity_id}`, ensuring deterministic URL routing without parsing conflicts.

---

## 2. API Endpoints Specification

### 2.1 Admin List Activity Sponsored Campaigns
- **HTTP Method:** `GET`
- **Route:** `/api/v1/admin/activities/promotions/campaigns`
- **Authentication:** Bearer Token (JWT)
- **Authorization:** `require_admin` (`user.role == 'admin'` and `user.is_active == True`)
- **Tags:** `Admin Activities`

#### Query Parameters
| Parameter | Type | Required | Default | Validation & Constraints | Description |
|---|---|:---:|---|---|---|
| `status` | `string` | No | `None` | Must be one of: `draft`, `pending_approval`, `active`, `paused`, `completed`, `cancelled`. Case-insensitive. | Filter by campaign status. Returns `422` if status is invalid. |
| `provider_profile_id` | `integer` | No | `None` | `ge=1` | Filter by owning provider profile ID. Returns `422` if $\le 0$. |
| `search` | `string` | No | `None` | `max_length=150` | Case-insensitive substring search matching `campaign_name`. |
| `page` | `integer` | No | `1` | `ge=1` | Page number for pagination. Returns `422` if $< 1$. |
| `page_size` | `integer` | No | `20` | `ge=1, le=100` | Items per page. Returns `422` if $< 1$ or $> 100$. |

#### Ordering Semantics
Stable, deterministic ordering:
$$\text{ORDER BY } \text{PromotionCampaign.created\_at DESC}, \text{PromotionCampaign.id DESC}$$

#### Success Response (`200 OK`) – `ActivityCampaignPagination`
```json
{
  "items": [
    {
      "id": 101,
      "provider_profile_id": 10,
      "campaign_name": "Rafting Summer Special",
      "target_module": "activity",
      "budget": 50000.00,
      "spent_amount": 0.00,
      "currency": "LKR",
      "status": "pending_approval",
      "starts_at": "2026-12-01T08:00:00Z",
      "ends_at": "2026-12-31T18:00:00Z",
      "created_at": "2026-11-20T14:30:00Z",
      "targeting_scope": "provider",
      "activity_targeting_status": "BLOCKED_BY_SCHEMA"
    },
    {
      "id": 102,
      "provider_profile_id": 10,
      "campaign_name": "Kitulgala Adventure Promo",
      "target_module": "activity",
      "budget": 75000.00,
      "spent_amount": 12500.00,
      "currency": "LKR",
      "status": "active",
      "starts_at": "2026-11-15T00:00:00Z",
      "ends_at": "2026-12-15T23:59:59Z",
      "created_at": "2026-11-10T09:15:00Z",
      "targeting_scope": "provider",
      "activity_targeting_status": "BLOCKED_BY_SCHEMA"
    }
  ],
  "total": 2,
  "page": 1,
  "page_size": 20,
  "total_pages": 1
}
```

---

### 2.2 Admin Get Activity Sponsored Campaign Details
- **HTTP Method:** `GET`
- **Route:** `/api/v1/admin/activities/promotions/campaigns/{campaign_id}`
- **Authentication:** Bearer Token (JWT)
- **Authorization:** `require_admin` (`user.role == 'admin'` and `user.is_active == True`)
- **Tags:** `Admin Activities`

#### Path Parameters
| Parameter | Type | Required | Description |
|---|---|:---:|---|
| `campaign_id` | `integer` | Yes | Primary key ID of the sponsored campaign |

#### Success Response (`200 OK`) – `ActivityCampaignResponse`
```json
{
  "id": 101,
  "provider_profile_id": 10,
  "campaign_name": "Rafting Summer Special",
  "target_module": "activity",
  "budget": 50000.00,
  "spent_amount": 0.00,
  "currency": "LKR",
  "status": "pending_approval",
  "starts_at": "2026-12-01T08:00:00Z",
  "ends_at": "2026-12-31T18:00:00Z",
  "created_at": "2026-11-20T14:30:00Z",
  "targeting_scope": "provider",
  "activity_targeting_status": "BLOCKED_BY_SCHEMA"
}
```

---

## 3. Error Responses & Handling Matrix

| HTTP Status | Condition | Example Response Body |
|---|---|---|
| **`401 Unauthorized`** | Missing, invalid, or expired JWT Bearer token | `{"detail": "Could not validate credentials"}` |
| **`403 Forbidden`** | Authenticated user has role `traveler`, `provider`, or is inactive | `{"detail": "Access forbidden: admin role required"}` |
| **`404 Not Found`** | Campaign ID does not exist in `dbo.promotion_campaigns` | `{"detail": "Promotion campaign not found"}` |
| **`404 Not Found`** | Campaign ID exists but `target_module != 'activity'` (homestay, artisan) | `{"detail": "Promotion campaign not found"}` |
| **`422 Unprocessable Content`** | `status` parameter is not in allowed physical statuses | `{"detail": "Invalid status filter 'foo'. Allowed statuses: active, cancelled, completed, draft, paused, pending_approval"}` |
| **`422 Unprocessable Content`** | `provider_profile_id` $< 1$ | `{"detail": "provider_profile_id must be a positive integer"}` |
| **`422 Unprocessable Content`** | `page` $< 1$ or `page_size` $< 1$ or `page_size` $> 100$ | Standard FastAPI parameter validation error |

---

## 4. Reusable Review Validation Schemas (C06 Preparation)

In `app/schemas/activity_admin.py`, reusable Pydantic validation models have been prepared for future administrative decisions (Phase 5D-4):

### Schema: `AdminCampaignReviewRequest`
```python
class AdminCampaignReviewRequest(BaseModel):
    action: str  # One of: "approve", "reject", "pause", "resume"
    reason: str | None = None  # Mandatory (min 10, max 500 chars) when action == "reject"
    notes: str | None = None  # Optional internal administrative notes (max 1000 chars)
```

### Validation Invariants Enforced:
1. **Supported Actions:** Strictly validated to `("approve", "reject", "pause", "resume")`. Unrecognized action strings raise `ValueError`.
2. **Mandatory Rejection Reason:** If `action == "reject"`, `reason` is strictly required and must contain $\ge 10$ characters.
3. **Status Eligibility Rules (`validate_status_eligibility`):**
   - `approve`: Eligible only when current status is `pending_approval`.
   - `reject`: Eligible only when current status is `pending_approval`.
   - `pause`: Eligible only when current status is `active`.
   - `resume`: Eligible only when current status is `paused`.
   - Any attempt to approve a `draft`, pause a `pending_approval` campaign, or modify a `cancelled`/`completed` campaign raises `ValueError`.
4. **Extra Fields Forbidden:** `model_config = ConfigDict(extra="forbid")` prevents injection of unauthorized attributes.

---

## 5. SQL Server 2025 Express Compatibility & Column Safety

Direct metadata audit confirmed the exact 11 physical columns in `dbo.promotion_campaigns`:

```sql
SELECT column_name, data_type, is_nullable
FROM INFORMATION_SCHEMA.COLUMNS
WHERE table_name = 'promotion_campaigns';
```

| Physical Column | Data Type | Nullable | Notes |
|---|---|:---:|---|
| `id` | `INT IDENTITY` | No | Primary Key (Clustered) |
| `provider_profile_id` | `INT` | No | FK -> `provider_profiles(id)` ON DELETE CASCADE |
| `campaign_name` | `NVARCHAR(150)` | No | Campaign title |
| `target_module` | `NVARCHAR(50)` | No | Enforced to `'activity'` |
| `budget` | `DECIMAL(12,2)` | No | Total budget |
| `spent_amount` | `DECIMAL(12,2)` | No | Current spend |
| `currency` | `NVARCHAR(3)` | No | ISO currency code (`LKR`) |
| `status` | `NVARCHAR(50)` | No | Check constraint: `draft, pending_approval, active, paused, completed, cancelled` |
| `starts_at` | `DATETIME2` | No | Start timestamp |
| `ends_at` | `DATETIME2` | No | End timestamp |
| `created_at` | `DATETIME2` | No | Creation timestamp |

### Prevention of SQL Server Error 207 & Error 547
- **Error 207 Prevention:** SQLAlchemy ORM model `PromotionCampaign` in `app/models/shared.py` defines `listing_reference_id = Column(...)`. If queried with `db.query(PromotionCampaign)`, SQLAlchemy generates `SELECT ... promotion_campaigns.listing_reference_id ...` which triggers `Error 207: Invalid column name 'listing_reference_id'`. Phase 5D-2D queries exclusively use `CAMPAIGN_PHYSICAL_COLUMNS` scalar projections, completely bypassing the missing column.
- **Error 547 Prevention:** Check constraint `CK_promotion_campaigns_status` permits only 6 status values. Phase 5D-2D introduces zero write endpoints and executes zero status updates, guaranteeing zero constraint violations.

---

## 6. QA Testing & Regression Verification

### 6.1 Focused Test Suite: `tests/test_admin_activity_promotions.py`
A dedicated test suite containing **24 comprehensive unit and integration tests** was implemented:

| Test Case | Scenario Tested | Result |
|---|---|:---:|
| `test_admin_list_campaigns_anonymous_unauthorized` | Anonymous request to listing endpoint returns 401 | **PASSED** |
| `test_admin_get_campaign_detail_anonymous_unauthorized` | Anonymous request to detail endpoint returns 401 | **PASSED** |
| `test_admin_list_campaigns_traveler_forbidden` | Authenticated traveler to listing endpoint returns 403 | **PASSED** |
| `test_admin_get_campaign_detail_traveler_forbidden` | Authenticated traveler to detail endpoint returns 403 | **PASSED** |
| `test_admin_list_campaigns_provider_forbidden` | Authenticated provider to listing endpoint returns 403 | **PASSED** |
| `test_admin_get_campaign_detail_provider_forbidden` | Authenticated provider to detail endpoint returns 403 | **PASSED** |
| `test_admin_list_campaigns_empty` | Empty database returns `total=0`, `items=[]`, `total_pages=0` | **PASSED** |
| `test_admin_list_campaigns_success` | Admin lists campaigns; verifies sorting & exclusion of cross-module homestays | **PASSED** |
| `test_admin_list_campaigns_pagination` | Verifies `page=1`, `page=2`, `page_size=2`, disjoint pages | **PASSED** |
| `test_admin_list_campaigns_status_filtering` | Filtering by `pending_approval`, `active`, `paused` | **PASSED** |
| `test_admin_list_campaigns_provider_filtering` | Filtering by `provider_profile_id` | **PASSED** |
| `test_admin_list_campaigns_name_search` | Case-insensitive substring search on campaign title | **PASSED** |
| `test_admin_list_campaigns_combined_filtering` | Combines status + provider + search criteria | **PASSED** |
| `test_admin_list_campaigns_invalid_parameters` | Validation for invalid status (422), provider $\le 0$ (422), page/size (422) | **PASSED** |
| `test_admin_get_campaign_detail_success` | Valid detail fetch with targeting metadata | **PASSED** |
| `test_admin_get_campaign_detail_nonexistent` | Non-existent ID returns 404 | **PASSED** |
| `test_admin_get_campaign_cross_module_leakage_prevented` | Requesting homestay campaign ID via activity admin returns 404 | **PASSED** |
| `test_admin_campaign_decimal_serialization` | Verifies budget and spent_amount decimal precision | **PASSED** |
| `test_admin_campaign_read_only_invariance` | Verifies database row count and states remain 100% unaltered | **PASSED** |
| `test_admin_review_schema_approve_action` | Schema validation for approve action & status eligibility | **PASSED** |
| `test_admin_review_schema_reject_action` | Schema validation requiring min 10-char reason on reject | **PASSED** |
| `test_admin_review_schema_pause_and_resume` | Schema validation for pause and resume status transitions | **PASSED** |
| `test_admin_review_schema_unsupported_action` | Unrecognized actions fail validation | **PASSED** |
| `test_admin_review_schema_extra_fields_forbidden` | Extra unpermitted payload fields raise validation error | **PASSED** |

### 6.2 Full Regression Suite Baseline
Executing the complete 18-file regression suite under `TZ=UTC`:
```
300 passed, 19 warnings in 117.57s (0:01:57)
```
- Existing baseline: 276 passed
- Phase 5D-2D added: 24 passed
- **New Regression Baseline: 300 passed, 0 failed, 0 skipped.**

---

## 7. Remaining C06 Gaps & Subsequent Phased Roadmap

| Requirement | Current Status | Capabilities Operational | Remaining Gaps / Blockers | Target Phase |
|---|:---:|---|---|:---:|
| **C06 – Admin Promotion Management** | **PARTIAL** | Admin campaign listing, multi-criteria filtering (status, provider, search), pagination, detail inspection, metadata transparency, review request validation schemas | Write endpoints (`POST .../approve`, `POST .../reject`, `POST .../pause`, `POST .../resume`); state transitions blocked by physical SQL Server check constraint until Migration 031; audit trail logging to `dbo.admin_audit_logs` | **Phase 5D-4** (Post-Migration 031) |

### Transition to Phase 5D-3 & Phase 5D-4:
1. **Phase 5D-3 (Controlled Migration Execution):**
   - Apply unapplied migrations `030` through `040` sequentially via `scripts/migrate.py upgrade`.
   - Apply Migration `041_extend_activity_promotions_and_discounts.sql` (`dbo.activity_promotion_packages`).
2. **Phase 5D-4 (Promotion Engine & Admin Decisions):**
   - Wire admin write endpoints using the validated `AdminCampaignReviewRequest` schema.
   - Record decision justification, admin ID, and timestamp in `dbo.admin_audit_logs`.
   - Transition C06 to **COMPLETE**.
