# CeylonGo Module 03 – Phase 5D-2A API Contract
## Admin Platform-Wide Activity Reporting (C07)

**Module:** Module 03 – Activities & Experiences
**Phase:** 5D-2A – Admin Platform-Wide Activity Reporting
**Requirement:** **C07 – Platform-Wide Activity Reporting**

---

## 1. Overview & Architectural Design

Canonical requirement **C07** empowers CeylonGo platform administrators to monitor health, inventory status, booking velocity, slot capacity utilization, regional demand, and multi-currency financials across all activities hosted on the platform.

### Core Architectural Guarantees
1. **Administrative Access Control (RBAC):** Strictly protected by `require_admin` dependency. Requests from unauthenticated clients, travelers, or activity providers are denied (`401 Unauthorized` / `403 Forbidden`).
2. **Zero Traveler PII Leakage:** Platform metrics calculate deterministic aggregates without exposing traveler personal details (no traveler names, emails, phone numbers, or individual booking records).
3. **Multi-Currency Segregation:** Financial statistics are partitioned strictly by ISO-4217 currency code (e.g., LKR vs USD). Currencies are NEVER blended or converted with implicit exchange rates.
4. **Cancelled/Rejected Revenue Exclusion:** Gross booking revenue includes only active, confirmed, and completed reservations. Cancelled or rejected bookings are excluded from gross revenue and tracked under dedicated refund metrics.
5. **High-Performance Query Aggregation (Zero N+1):** The entire report executes across exactly 3 targeted queries (activity inventory scalar projection, slot capacity SQL aggregation, and joined booking items) avoiding any N+1 loops regardless of listing or booking volume.
6. **Cross-Module Eco-Tourism Isolation:** Legacy Eco-Tourism slots and reservations (`eco_experience_id IS NOT NULL`) are strictly filtered out to prevent cross-module metric pollution.
7. **FastAPI Route Shadowing Prevention:** In `app/routers/admin_activities.py`, the static route `@router.get("/reports")` is registered **strictly before** dynamic parameter routes like `@router.get("/{activity_id}")` to prevent path parsing conflicts.
8. **Zero Database Alterations Invariant:** Implemented strictly against existing physical tables (`activities`, `activity_slots`, `activity_bookings`, `provider_profiles`) with zero migrations, zero DDL, and zero schema mutations.

---

## 2. API Endpoint Specification

### `GET /api/v1/admin/activities/reports`

- **HTTP Method:** `GET`
- **Route:** `/api/v1/admin/activities/reports`
- **Tags:** `admin-activities`
- **Authentication:** Bearer Token (JWT)
- **Authorization:** `require_admin` (`user.role == 'admin'` and `user.is_active == True`)

#### Query Parameters
| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `date_from` | `date` (YYYY-MM-DD) | No | `None` | Lower bound date filter for bookings created and slots scheduled |
| `date_to` | `date` (YYYY-MM-DD) | No | `None` | Upper bound date filter for bookings created and slots scheduled |
| `start_date` | `date` (YYYY-MM-DD) | No | `None` | Alias for `date_from` |
| `end_date` | `date` (YYYY-MM-DD) | No | `None` | Alias for `date_to` |

#### Validation & Filter Rules
- **Date Inversion:** If `eff_from > eff_to`, raises `422 Unprocessable Content` (`"date_from / start_date cannot be after date_to / end_date"`).
- **Max Time Window:** If `(eff_to - eff_from).days > 1826` (5 years), raises `422 Unprocessable Content` (`"Date range exceeds maximum allowed window of 5 years (1826 days)"`).
- **Slot Date Range:** Filtered on `ActivitySlot.starts_at >= combine(eff_from, min)` and `ActivitySlot.starts_at <= combine(eff_to, max)`.
- **Booking Date Range:** Filtered on `ActivityBooking.created_at >= combine(eff_from, min)` and `ActivityBooking.created_at <= combine(eff_to, max)`.
- **Activity Inventory:** Reflects cumulative platform inventory counts regardless of date filter to preserve catalog context.

---

## 3. Response Schema

### `AdminActivityPlatformReportResponse`

```json
{
  "generated_at": "2026-11-21T10:45:00Z",
  "date_from": "2026-01-01",
  "date_to": "2026-12-31",
  "activity_inventory": {
    "total_activities": 25,
    "approved_activities": 18,
    "pending_activities": 3,
    "draft_activities": 2,
    "suspended_activities": 1,
    "rejected_activities": 1,
    "active_published_activities": 17,
    "inactive_activities": 8
  },
  "booking_overview": {
    "total_bookings": 142,
    "confirmed_bookings": 98,
    "completed_bookings": 32,
    "pending_bookings": 4,
    "cancelled_bookings": 6,
    "rejected_bookings": 2,
    "total_participants": 384,
    "confirmed_participants": 352,
    "booking_conversion_rate": 91.55,
    "cancellation_rate": 4.23
  },
  "capacity_utilization": {
    "total_slots": 210,
    "active_slots": 204,
    "total_capacity": 1850,
    "total_reserved": 1280,
    "total_remaining": 570,
    "occupancy_rate": 69.19
  },
  "category_distribution": [
    {
      "category": "adventure",
      "activity_count": 10,
      "active_activity_count": 9,
      "booking_count": 75,
      "total_participants": 210,
      "gross_revenue": "750000.00",
      "currency": "LKR"
    },
    {
      "category": "cultural",
      "activity_count": 8,
      "active_activity_count": 7,
      "booking_count": 42,
      "total_participants": 110,
      "gross_revenue": "420000.00",
      "currency": "LKR"
    },
    {
      "category": "local",
      "activity_count": 4,
      "active_activity_count": 3,
      "booking_count": 15,
      "total_participants": 38,
      "gross_revenue": "150000.00",
      "currency": "LKR"
    },
    {
      "category": "community",
      "activity_count": 3,
      "active_activity_count": 2,
      "booking_count": 10,
      "total_participants": 26,
      "gross_revenue": "80000.00",
      "currency": "LKR"
    }
  ],
  "regional_distribution": [
    {
      "district": "Matara",
      "activity_count": 6,
      "booking_count": 52,
      "total_participants": 140
    },
    {
      "district": "Badulla",
      "activity_count": 5,
      "booking_count": 38,
      "total_participants": 98
    }
  ],
  "financial_overview": [
    {
      "currency": "LKR",
      "total_gross_revenue": "1450000.00",
      "paid_revenue": "1120000.00",
      "unpaid_revenue": "330000.00",
      "refunded_amount": "45000.00"
    },
    {
      "currency": "USD",
      "total_gross_revenue": "3200.00",
      "paid_revenue": "2800.00",
      "unpaid_revenue": "400.00",
      "refunded_amount": "120.00"
    }
  ],
  "top_activities": [
    {
      "activity_id": 10,
      "activity_title": "Weligama Beginner Surf Camp",
      "category": "adventure",
      "district": "Matara",
      "provider_profile_id": 1,
      "provider_business_name": "Silva Surf Academy",
      "booking_count": 42,
      "total_participants": 118,
      "gross_revenue": "420000.00",
      "currency": "LKR"
    }
  ],
  "top_providers": [
    {
      "provider_profile_id": 1,
      "business_name": "Silva Surf Academy",
      "total_activities": 4,
      "active_activities": 3,
      "booking_count": 65,
      "total_participants": 182,
      "gross_revenue": "650000.00",
      "currency": "LKR"
    }
  ]
}
```

---

## 4. Exact Metric Semantics & Mathematical Definitions

1. **Activity Inventory Overview:**
   - `total_activities`: Count of all activity records on platform.
   - `approved_activities`: Count where `approval_status == 'approved'`.
   - `pending_activities`: Count where `approval_status == 'pending'`.
   - `draft_activities`: Count where `approval_status == 'draft'`.
   - `suspended_activities`: Count where `approval_status == 'suspended'`.
   - `rejected_activities`: Count where `approval_status == 'rejected'`.
   - `active_published_activities`: Count where `approval_status == 'approved'` AND `is_active == True`.
   - `inactive_activities`: `total_activities - active_published_activities`.

2. **Booking Lifecycle Metrics:**
   - `booking_conversion_rate`: $\min\left(100.0, \max\left(0.0, \frac{\text{confirmed} + \text{completed}}{\text{total\_bookings}} \times 100\right)\right)$, or $0.0$ when $\text{total\_bookings} = 0$.
   - `cancellation_rate`: $\min\left(100.0, \max\left(0.0, \frac{\text{cancelled}}{\text{total\_bookings}} \times 100\right)\right)$, or $0.0$ when $\text{total\_bookings} = 0$.
   - `confirmed_participants`: Sum of participants for bookings with status in `("confirmed", "completed")`.

3. **Capacity Utilization (SQL Aggregation):**
   - `total_slots`: Direct SQL `COUNT(id)` on non-eco slots in window.
   - `active_slots`: SQL `SUM(CASE WHEN is_active=1 THEN 1 ELSE 0 END)`.
   - `total_capacity`: SQL `COALESCE(SUM(capacity), 0)`.
   - `total_reserved`: SQL `COALESCE(SUM(reserved_count), 0)`.
   - `total_remaining`: $\max(0, \text{total\_capacity} - \text{total\_reserved})$.
   - `occupancy_rate`: $\min\left(100.0, \max\left(0.0, \frac{\text{total\_reserved}}{\text{total\_capacity}} \times 100\right)\right)$, or $0.0$ when $\text{total\_capacity} = 0$.

4. **Multi-Currency Financials & Refund Semantics:**
   - Partitioned strictly by ISO-4217 currency.
   - `total_gross_revenue`: Sum of `total_price` for active bookings (`booking_status NOT IN ('cancelled', 'rejected')`).
   - `paid_revenue`: Sum of `total_price` for active bookings where `payment_status == 'paid'`.
   - `unpaid_revenue`: Sum of `total_price` for active bookings where `payment_status IN ('unpaid', 'pending')`.
   - `refunded_amount`: Aggregate refund liability representing both finalized/executed refunds (`payment_status == 'refunded'`) and initiated refunds awaiting payment processor settlement (`payment_status == 'refund_pending'`). Note: Disaggregated reporting between settled refunds versus pending gateway refund authorizations requires external merchant webhook reconciliation (Module 05 PayHere).

5. **Sorting & Determinism:**
   - Regional distribution sorted by `(-booking_count, -activity_count, district)`.
   - Top activities sorted by `(-booking_count, -gross_revenue, activity_id)`.
   - Top providers sorted by `(-booking_count, -gross_revenue, provider_profile_id)`.

---

## 5. SQL Server 2025 Express Compatibility Verification

Direct metadata inspection was conducted against `CeylonGoDB_Development` (Microsoft SQL Server 2025 RTM 17.0.1000.7):
- `activities`: 26 physical columns verified (`id`, `provider_profile_id`, `title`, `category`, `district`, `currency`, `approval_status`, `is_active`, etc.).
- `activity_slots`: 9 physical columns verified (`id`, `activity_id`, `starts_at`, `capacity`, `reserved_count`, `is_active`, `eco_experience_id`).
- `activity_bookings`: 14 physical columns verified (`id`, `activity_slot_id`, `user_id`, `participants`, `total_price`, `currency`, `payment_status`, `booking_status`, `created_at`, `eco_experience_id`).
- `provider_profiles`: 32 physical columns verified (`id`, `business_name`, `provider_type`, etc.).

All reporting queries execute with 100% ANSI-SQL compliance on SQL Server and SQLite.

---

## 6. Known Limitations & Architectural Boundaries

1. **No Implicit Currency Conversion:** If bookings exist in both LKR and USD, they are presented in separate breakdown rows. The endpoint never applies assumed foreign exchange conversion rates.
2. **Catalog Scope:** Activity inventory counts reflect cumulative platform inventory across the platform's lifetime; date range parameters filter scheduled slots and booking transactions.
3. **External Payment Independence & Refund Settlement:** The reporting engine reads internal transaction records without querying external merchant processors (Module 05 PayHere). Actual credited funds vs. in-flight refund requests are both aggregated into `refunded_amount` until gateway webhooks update payment status.
4. **In-Memory Aggregation for Bookings:** While slot capacity metrics are pushed directly to SQL Server via database aggregations (`func.count`, `func.sum`), booking lifecycle metrics, category distributions, regional distributions, and top entity rankings are aggregated in Python memory over scalar column projections returned by the date-filtered query (`ActivityBooking` + `ActivitySlot` + `Activity`). For exceptionally large transaction volumes without date filters, callers should provide bounded date ranges (`date_from`, `date_to`) to constrain memory usage.
