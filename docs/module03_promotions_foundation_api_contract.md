# CeylonGo Module 03 – Phase 5D-2B API Contract
## Promotions Foundation: Provider Discounts (B15) & Sponsored Campaign Applications (B16)

**Module:** Module 03 – Activities & Experiences
**Phase:** 5D-2B – Provider Discounts & Sponsored Campaign Applications
**Requirements Covered:**
- **B15 – Provider Discount Offers** (PARTIAL – Foundation complete at provider scope; activity-specific targeting BLOCKED_BY_SCHEMA)
- **B16 – Sponsored Promotion Applications** (PARTIAL – Foundation complete at provider scope; activity-specific targeting BLOCKED_BY_SCHEMA)

---

## 1. Executive & Architectural Summary

Phase 5D-2B implements the provider promotion and discount management foundations for Module 03 (Activities & Experiences) while upholding strict database invariants against the production Microsoft SQL Server 2025 Express schema (`CeylonGoDB_Development`).

### Core Architectural Decisions & Schema Constraints

1. **Physical Schema Compliance (Zero DDL Drift):**
   - **`dbo.promotion_packages` (11 physical columns):** Used for provider promotional discounts. Physically contains `provider_profile_id`, `title`, `description`, `discount_percent`, `discount_amount`, `currency`, `valid_from`, `valid_to`, `is_active`, and `created_at`.
   - **`dbo.promotion_campaigns` (11 physical columns):** Used for sponsored campaign applications. Physically contains `provider_profile_id`, `campaign_name`, `target_module`, `budget`, `spent_amount`, `currency`, `status`, `starts_at`, `ends_at`, and `created_at`.
   - **Unmigrated Column Isolation:** The unapplied migration column `listing_reference_id` (defined in unrun Migration 030) does NOT exist on `dbo.promotion_campaigns`. All queries and inserts use scalar column projections and SQLAlchemy Core expressions targeting strictly physical columns, preventing SQL Server error `207 (Invalid column name 'listing_reference_id')`.

2. **Resolution of Activity Targeting Design (Step 4 Audit):**
   - Neither `dbo.promotion_packages` nor `dbo.promotion_campaigns` contains an `activity_id` or physical `listing_reference_id` column.
   - Activity-specific targeting is explicitly marked as `BLOCKED_BY_SCHEMA` and exposed as such in responses (`targeting_scope="provider"`, `activity_targeting_status="BLOCKED_BY_SCHEMA"`).
   - In accordance with architectural safety rules:
     - Activity IDs are NOT encoded into free-text fields (campaign names or descriptions).
     - No hidden in-memory mapping is introduced.
     - Requirements B15 and B16 are tracked as **PARTIAL** until Migration 030 or an activity foreign key migration is officially applied.

3. **Multi-Tenant Ownership & Security Isolation:**
   - Every endpoint is guarded by `require_activity_provider` which verifies that the caller is an active, approved provider of type `activity`.
   - Provider profile ID is extracted directly from the authenticated session, preventing spoofing.
   - Cross-provider requests return `404 Not Found` (fail-closed, no info leakage).
   - Cross-module requests (`target_module != 'activity'`) return `404 Not Found`.

4. **Self-Approval & Editorial Workflow:**
   - Providers can only create campaigns in `draft` or `pending_approval` status.
   - Providers cannot activate, approve, or complete their own campaigns (returns `403 Forbidden`).
   - Editing is restricted strictly to `draft` campaigns; once submitted (`pending_approval`), fields cannot be modified without administrative rejection or cancellation.

5. **Route Shadowing Prevention:**
   - The router prefix `/api/v1/provider/activities` is mounted **before** `provider_activities_router` in `app/main.py`, preventing static paths like `/discounts` or `/promotions/campaigns` from colliding with dynamic path parameter `/{activity_id}`.

---

## 2. B15: Provider Discount Offers Endpoints

Prefix: `/api/v1/provider/activities/discounts`

### 2.1 Create Provider Discount Offer
- **Method & Route:** `POST /api/v1/provider/activities/discounts`
- **Status Code:** `201 Created`
- **Authentication:** Bearer JWT (`require_activity_provider`)
- **Request Payload (`ActivityDiscountCreate`):**
```json
{
  "title": "Early Bird Summer Special",
  "description": "Book 14 days in advance for a 15% discount on activity packages.",
  "discount_percent": 15.00,
  "discount_amount": null,
  "currency": "LKR",
  "valid_from": "2026-12-01",
  "valid_to": "2026-12-31",
  "is_active": true
}
```
- **Validation Rules:**
  - `valid_to >= valid_from` (if violated: `422 Unprocessable Content`).
  - At least one of `discount_percent` or `discount_amount` must be provided and `> 0` (if neither: `422 Unprocessable Content`).
  - `discount_percent`: `0.00 <= percent <= 100.00`.
  - `discount_amount`: `>= 0.00`.
  - `currency`: 3-character uppercase ISO code.
- **Response Payload (`ActivityDiscountResponse`):**
```json
{
  "id": 1,
  "provider_profile_id": 5,
  "title": "Early Bird Summer Special",
  "description": "Book 14 days in advance for a 15% discount on activity packages.",
  "discount_percent": 15.00,
  "discount_amount": null,
  "currency": "LKR",
  "valid_from": "2026-12-01",
  "valid_to": "2026-12-31",
  "is_active": true,
  "created_at": "2026-11-21T11:45:00Z",
  "targeting_scope": "provider",
  "activity_targeting_status": "BLOCKED_BY_SCHEMA"
}
```

### 2.2 List Provider Discount Offers
- **Method & Route:** `GET /api/v1/provider/activities/discounts`
- **Authentication:** Bearer JWT (`require_activity_provider`)
- **Query Parameters:**
  - `is_active` (`bool`, optional): Filter active/inactive discount packages
  - `page` (`int`, default: 1, `ge=1`): Page number
  - `page_size` (`int`, default: 20, `ge=1`, `le=100`): Items per page
- **Response Payload (`ActivityDiscountPagination`):**
```json
{
  "items": [
    {
      "id": 1,
      "provider_profile_id": 5,
      "title": "Early Bird Summer Special",
      "description": "Book 14 days in advance for a 15% discount on activity packages.",
      "discount_percent": 15.00,
      "discount_amount": null,
      "currency": "LKR",
      "valid_from": "2026-12-01",
      "valid_to": "2026-12-31",
      "is_active": true,
      "created_at": "2026-11-21T11:45:00Z",
      "targeting_scope": "provider",
      "activity_targeting_status": "BLOCKED_BY_SCHEMA"
    }
  ],
  "total": 1,
  "page": 1,
  "page_size": 20,
  "total_pages": 1
}
```

### 2.3 Get Provider Discount Detail
- **Method & Route:** `GET /api/v1/provider/activities/discounts/{discount_id}`
- **Authentication:** Bearer JWT (`require_activity_provider`)
- **Responses:** `200 OK` (`ActivityDiscountResponse`), `404 Not Found` if missing or non-owned.

### 2.4 Update Provider Discount Offer
- **Method & Route:** `PATCH /api/v1/provider/activities/discounts/{discount_id}`
- **Authentication:** Bearer JWT (`require_activity_provider`)
- **Request Payload (`ActivityDiscountUpdate`):**
```json
{
  "title": "Extended Summer Special",
  "valid_to": "2027-01-15"
}
```
- **Validation:** Enforces `eff_to >= eff_from` across updated fields.

### 2.5 Deactivate Provider Discount Offer
- **Method & Route:** `POST /api/v1/provider/activities/discounts/{discount_id}/deactivate`
- **Authentication:** Bearer JWT (`require_activity_provider`)
- **Action:** Sets `is_active = False`.

---

## 3. B16: Provider Sponsored Campaign Applications Endpoints

Prefix: `/api/v1/provider/activities/promotions/campaigns`

### 3.1 Submit Sponsored Campaign Application
- **Method & Route:** `POST /api/v1/provider/activities/promotions/campaigns`
- **Status Code:** `201 Created`
- **Authentication:** Bearer JWT (`require_activity_provider`)
- **Request Payload (`ActivityCampaignCreate`):**
```json
{
  "campaign_name": "Peak Season Surf Camp Boost",
  "budget": 25000.00,
  "currency": "LKR",
  "starts_at": "2026-12-01T08:00:00Z",
  "ends_at": "2026-12-25T18:00:00Z",
  "submit_for_review": true
}
```
- **Status Logic:**
  - If `submit_for_review == true`: Status is set to `"pending_approval"`.
  - If `submit_for_review == false`: Status is saved as `"draft"`.
  - Providers cannot specify or trigger `"active"`, `"approved"`, or `"completed"` status.
- **Validation Rules:**
  - `ends_at > starts_at` (if violated: `422 Unprocessable Content`).
  - `ends_at` must be strictly in the future (if past: `422 Unprocessable Content`).
  - `budget > 0.00` (if violated: `422 Unprocessable Content`).
  - `target_module` is permanently bound to `"activity"`.
  - `spent_amount` initializes to `Decimal("0.00")`.
- **Response Payload (`ActivityCampaignResponse`):**
```json
{
  "id": 10,
  "provider_profile_id": 5,
  "campaign_name": "Peak Season Surf Camp Boost",
  "target_module": "activity",
  "budget": 25000.00,
  "spent_amount": 0.00,
  "currency": "LKR",
  "status": "pending_approval",
  "starts_at": "2026-12-01T08:00:00",
  "ends_at": "2026-12-25T18:00:00",
  "created_at": "2026-11-21T11:45:00",
  "targeting_scope": "provider",
  "activity_targeting_status": "BLOCKED_BY_SCHEMA"
}
```

### 3.2 List Activity Sponsored Campaigns
- **Method & Route:** `GET /api/v1/provider/activities/promotions/campaigns`
- **Authentication:** Bearer JWT (`require_activity_provider`)
- **Query Parameters:**
  - `status` (`str`, optional): Filter by campaign status (`draft`, `pending_approval`, `active`, `completed`, `cancelled`)
  - `page` (`int`, default: 1, `ge=1`): Page number
  - `page_size` (`int`, default: 20, `ge=1`, `le=100`): Items per page
- **Isolation Filter:** Filters on `provider_profile_id == profile.id` AND `target_module == 'activity'`.

### 3.3 Get Activity Sponsored Campaign Detail
- **Method & Route:** `GET /api/v1/provider/activities/promotions/campaigns/{campaign_id}`
- **Authentication:** Bearer JWT (`require_activity_provider`)
- **Responses:** `200 OK` (`ActivityCampaignResponse`), `404 Not Found` if not found, belongs to another provider, or belongs to another module.

### 3.4 Update Draft Activity Sponsored Campaign
- **Method & Route:** `PATCH /api/v1/provider/activities/promotions/campaigns/{campaign_id}`
- **Authentication:** Bearer JWT (`require_activity_provider`)
- **Request Payload (`ActivityCampaignUpdate`):**
```json
{
  "campaign_name": "Revised Summer Push",
  "budget": 30000.00,
  "status": "pending_approval"
}
```
- **Rules:**
  - Editing fields (`campaign_name`, `budget`, dates) is permitted **ONLY** when `status == 'draft'`. If already submitted (`pending_approval`, `active`, `completed`, `cancelled`), returns `400 Bad Request` (`"Only draft campaigns can be modified"`).
  - Providers may transition status from `"draft"` to `"pending_approval"`.
  - Self-approval attempts to `"active"`, `"approved"`, or `"completed"` return `403 Forbidden` (`"Self-activation or self-approval is not permitted"`).

### 3.5 Cancel Activity Sponsored Campaign
- **Method & Route:** `POST /api/v1/provider/activities/promotions/campaigns/{campaign_id}/cancel`
- **Authentication:** Bearer JWT (`require_activity_provider`)
- **Action:** Sets `status = "cancelled"`.
- **Pre-Conditions:** Cannot cancel an already `"cancelled"` campaign (returns `400 Bad Request`) or a `"completed"` campaign (returns `400 Bad Request`).

---

## 4. Error Status Codes Reference

| HTTP Status | Trigger Condition | Example Error Response |
|---|---|---|
| `401 Unauthorized` | Missing or invalid authentication token | `{"detail": "Not authenticated"}` |
| `403 Forbidden` | User is a traveler or non-activity provider | `{"detail": "Activity provider access required"}` |
| `403 Forbidden` | Provider attempts to self-approve a campaign | `{"detail": "Self-activation or self-approval is not permitted"}` |
| `400 Bad Request` | Attempting to edit a non-draft campaign | `{"detail": "Only draft campaigns can be modified. Current status: pending_approval"}` |
| `400 Bad Request` | Cancelling an already cancelled campaign | `{"detail": "Campaign is already cancelled"}` |
| `404 Not Found` | Non-existent or other provider/module resource | `{"detail": "Promotion campaign not found"}` |
| `422 Unprocessable Content` | Date inversion (`valid_to < valid_from`, `ends_at <= starts_at`) | `{"detail": "Campaign ends_at must be strictly after starts_at"}` |
| `422 Unprocessable Content` | Invalid discounts (percent > 100, budget <= 0) | `{"detail": "Budget must be greater than zero"}` |

---

## 5. Architectural Gap Tracking & Remaining Limitations

- **B15 – Provider Discount Offers (PARTIAL):** Provider-level discount CRUD is operational on `dbo.promotion_packages`. Specific activity linking is marked `BLOCKED_BY_SCHEMA`. A separate schema design and migration (e.g. adding an `activity_id` foreign key column or an `activity_promotion_packages` link table to `dbo.promotion_packages`) is required to support activity-specific discount scoping, as `dbo.promotion_packages` currently only holds `provider_profile_id`.
- **B16 – Sponsored Promotion Applications (PARTIAL):** Provider-level activity campaign lifecycle management is operational on `dbo.promotion_campaigns`. Specific activity linking is marked `BLOCKED_BY_SCHEMA`. Unblocking activity-level targeting depends directly on Migration 030 (`030_extend_homestay_final_requirements.sql`), which adds `listing_reference_id` to `dbo.promotion_campaigns`.
- **No PayHere or Sponsored Activation Implementation:** Payment gateway integration (PayHere API, webhook verification, payment receipt capture) and automatic campaign activation upon payment are strictly non-goals for Phase 5D-2B. Sponsored campaign activation, fee payments, and financial settlements are deferred to Phase 6 (requirements A09, B14, C05).
- **A17 / A18 / B18 / C06:** Deferred to upcoming phases (Phase 5D-2C, Phase 5D-3, Phase 5D-4). Reusable foundation services and physical column access patterns are established.

---

## 6. Shared Database Safety Verification & Zero Mutation Audit

A strict database safety audit was conducted against Microsoft SQL Server 2025 Express (`CeylonGoDB_Development`) to verify that no state mutations or schema corruptions occurred:

1. **Table Row Counts and Identity State (`CeylonGoDB_Development`):**
   - `dbo.promotion_packages`: `row_count = 0`, `ident_current = 1`
   - `dbo.promotion_campaigns`: `row_count = 0`, `ident_current = 1`
   - `dbo.promotion_events`: `row_count = 0`, `ident_current = 1`
   - `dbo.activities`: `row_count = 0`, `ident_current = 1`
   - `dbo.provider_profiles`: `row_count = 0`, `ident_current = 1`
2. **SQL Server Transient Write-Test & Rollback Limitations:**
   - In Microsoft SQL Server, even inside uncommitted transactions that are subsequently rolled back, `IDENTITY` columns can consume sequence values or advance seed counters upon insert flushes.
   - Therefore, to preserve absolute purity of the shared development database (`CeylonGoDB_Development`), zero direct insert or write operations were executed against `CeylonGoDB_Development`. Only read-only metadata inspections (`sys.columns`, `INFORMATION_SCHEMA.COLUMNS`) and SQL query compilation checks were performed against SQL Server.
3. **Timezone Alignment for Regression Suite (`TZ=UTC`):**
   - The backend application utilizes UTC timestamps (`datetime.now(timezone.utc).date()`) for calendar date defaults.
   - On host environments running in negative UTC offset timezones (e.g. UTC-12), tests asserting default calendar ranges (`date.today()`) require timezone alignment to prevent local vs. UTC midnight divergence.
   - All regression suites must be executed with `TZ=UTC` (`$env:TZ = "UTC"` in PowerShell), ensuring 100% test reproducibility across all local time zones.
4. **Test Suite Isolation:**
   - All 276 unit and integration tests run against isolated in-memory SQLite fixtures with SQLite datetime mock functions, maintaining zero dependencies or writes to SQL Server.
