# CeylonGo Module 03 – Phase 5C API Contract
## Availability Calendar & Provider Activity Analytics

**Module:** Module 03 – Activities & Experiences
**Phase:** 5C – Availability Calendar & Provider Analytics
**Requirements:**
- **A07 – Traveler Activity Availability Calendar**
- **B01 – Provider Activity Analytics**

---

## 1. Overview & Architectural Design

Phase 5C completes the remaining functional requirements for Module 03:
1. **A07 – Traveler Activity Availability Calendar:** Allows travelers to query forward-looking availability across calendar date ranges for published, active activities, receiving daily capacity summaries and individual slot availability states (`available`, `limited`, `sold_out`).
2. **B01 – Provider Activity Analytics:** Provides activity providers with comprehensive performance intelligence across all owned activities (or scoped to a specific activity), including activity inventory states, booking status counts, participant volume, revenue metrics (gross, paid, unpaid), slot capacity utilization (occupancy rate), and per-activity performance breakdowns with review ratings—engineered with zero N+1 queries.

### Zero Database Alterations Invariant
Phase 5C requires zero database migrations or DDL schema alterations. All operations utilize existing SQL Server tables:
- `dbo.activities`
- `dbo.activity_slots`
- `dbo.activity_bookings`
- `dbo.listing_references`
- `dbo.reviews`
- `dbo.provider_profiles`

---

## 2. API Endpoint Specifications

### 2.1 Public Activity Availability Calendar (A07)

- **HTTP Method:** `GET`
- **Route:** `/api/v1/activities/{activity_id}/availability`
- **Authentication:** Public (No authentication required)
- **Publication Guard:** Activity must satisfy `public_activity()` (`approval_status == 'approved'` AND `is_active == True`). Non-public, draft, suspended, or inactive activities return `404 Not Found`.

#### Query Parameters
| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `date_from` | `date` (YYYY-MM-DD) | No | Today (`date.today()`) | Calendar window start date |
| `start_date` | `date` (YYYY-MM-DD) | No | None | Alias for `date_from` |
| `date_to` | `date` (YYYY-MM-DD) | No | `date_from + 30 days` | Calendar window end date |
| `end_date` | `date` (YYYY-MM-DD) | No | None | Alias for `date_to` |

#### Validation & Date Range Semantics
- **Date Inversion:** If `eff_from > eff_to`, returns `422 Unprocessable Entity` (`"start_date / date_from cannot be after end_date / date_to"`).
- **Max Window:** If `(eff_to - eff_from).days > 180`, returns `422 Unprocessable Entity` (`"Date range exceeds maximum allowed window of 180 days"`).
- **Date Boundaries:** Boundary checks are inclusive:
  - `window_start = max(now_utc, datetime.combine(eff_from, 00:00:00))`
  - `window_end = datetime.combine(eff_to, 23:59:59.999999)`
- **Past Slot Handling:** Slots with `starts_at < now_utc` are excluded from public availability to align strictly with Phase 4 booking rules which reject past-slot reservations (`starts_at <= now` -> 400).

#### Slot Availability Status Logic
- `remaining_capacity = max(0, slot.capacity - slot.reserved_count)`
- **`sold_out`**: `remaining_capacity == 0` or `not slot.is_active`
- **`limited`**: `remaining_capacity <= 2` OR `(slot.reserved_count / slot.capacity) >= 0.8`
- **`available`**: All other active slots with remaining capacity
- **Overbooked Slot Defense:** `remaining_capacity` is clamped to $\ge 0$ (`max(0, capacity - reserved_count)`), ensuring no negative remaining capacity is ever returned.

#### Response Schema (`ActivityAvailabilityResponse`)
```json
{
  "activity_id": 1,
  "activity_title": "Knuckles Mountain Trek",
  "date_from": "2026-06-01",
  "date_to": "2026-06-30",
  "total_slots": 12,
  "available_slots": 10,
  "days": [
    {
      "date": "2026-06-05",
      "total_slots": 2,
      "available_slots": 2,
      "total_capacity": 20,
      "total_reserved": 4,
      "total_remaining": 16,
      "has_availability": true
    }
  ],
  "slots": [
    {
      "id": 101,
      "activity_id": 1,
      "starts_at": "2026-06-05T08:00:00",
      "ends_at": "2026-06-05T12:00:00",
      "capacity": 10,
      "reserved_count": 2,
      "remaining_capacity": 8,
      "availability_status": "available",
      "is_available": true
    }
  ]
}
```

---

### 2.2 Public Activity Slots Listing – Date Filters (A07 Backward Compatibility)

- **HTTP Method:** `GET`
- **Route:** `/api/v1/activities/{activity_id}/slots`
- **Authentication:** Public
- **Description:** Enhanced existing public slot endpoint with optional date filtering while maintaining full backward compatibility.

#### Query Parameters
| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `date_from` | `date` | No | None | Filter slots on or after this date |
| `start_date` | `date` | No | None | Alias for `date_from` |
| `date_to` | `date` | No | None | Filter slots on or before this date |
| `end_date` | `date` | No | None | Alias for `date_to` |

#### Validation Rules
- `eff_from > eff_to` returns `422 Unprocessable Entity`.

---

### 2.3 Provider Activity Analytics (B01)

- **HTTP Method:** `GET`
- **Route:** `/api/v1/provider/activities/analytics`
- **Authentication:** Authenticated Provider with `provider_type == "activity"` and `approval_status == "approved"` (`require_activity_provider`).
- **Route Precedence Invariant:** Registered in `app/routers/provider_activities.py` strictly **before** `GET /{activity_id}` to prevent route collision (`422 Unprocessable Entity` attempting to parse string `"analytics"` as integer ID).

#### Query Parameters
| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `activity_id` | `int` | No | None | Optional filter to scope metrics to a single owned activity |
| `date_from` | `date` | No | None | Filter bookings and slot metrics from this date |
| `start_date` | `date` | No | None | Alias for `date_from` |
| `date_to` | `date` | No | None | Filter bookings and slot metrics up to this date |
| `end_date` | `date` | No | None | Alias for `date_to` |

#### Security & Access Control
- Unauthenticated requests return `401 Unauthorized`.
- Non-activity providers (e.g. `hotel`, `artisan`) or travelers return `403 Forbidden` (`"Activity provider access required"`).
- Providing an `activity_id` that belongs to another provider returns `404 Not Found` (`"Activity not found"`), enforcing tenant isolation without information leakage.
- **Privacy Enforcement:** Responses contain zero traveler Personally Identifiable Information (PII) — no traveler names, emails, phone numbers, or user IDs are exposed.

#### Date Semantics
- **Bookings:** Filtered by `ActivityBooking.created_at` within `[datetime.combine(eff_from, 00:00:00), datetime.combine(eff_to, 23:59:59.999999)]`.
- **Capacity Slots:** Filtered by `ActivitySlot.starts_at` within `[datetime.combine(eff_from, 00:00:00), datetime.combine(eff_to, 23:59:59.999999)]`.
- Both boundary checks are inclusive of midnight (`00:00:00`) and end-of-day (`23:59:59.999999`).

#### Financial Metrics & Currency Handling
- **Gross Booking Value (`total_gross_revenue`):** Sum of `total_price` for valid bookings (`booking_status NOT IN ('cancelled', 'rejected')` AND `payment_status IN ('paid', 'unpaid', 'pending')`).
- **Collected / Realized Value (`paid_revenue`):** Sum of `total_price` for bookings with `payment_status == 'paid'` and non-cancelled/rejected status.
- **Uncollected / Pending Value (`unpaid_revenue`):** Sum of `total_price` for non-cancelled/rejected bookings with `payment_status IN ('unpaid', 'pending')`.
- **Refunded Amount (`refunded_amount`):** Explicitly tracks reversed or refund-pending funds (`payment_status IN ('refunded', 'refund_pending')`). These are strictly excluded from `paid_revenue` and `total_gross_revenue`.
- **Failed Payments:** Bookings with `payment_status == 'failed'` are excluded from gross, paid, and unpaid revenue.
- **Multi-Currency Safety:** Financials are grouped by currency in the `by_currency` array (`CurrencyRevenueBreakdown`), preventing distinct currencies (e.g. LKR and USD) from being summed together.

#### Capacity Utilization & Cancellation Formulas
- **Cancellation Rate:** Calculated as `(cancelled_bookings / total_bookings) * 100.0` if `total_bookings > 0` else `0.0`, clamped between `0.0` and `100.0`.
- **Occupancy Rate:** Calculated as `(total_capacity_reserved / total_capacity_offered) * 100.0` if `total_capacity_offered > 0` else `0.0`, clamped between `0.0` and `100.0`.
- **Zero Division Safety:** When a provider has zero slots or zero bookings, rates default to `0.0`.
- **Zero N+1 Query Aggregation:** Reviews are aggregated in a single SQL query joining `dbo.listing_references` and `dbo.reviews` grouped by `target_id` for all provider activities.

#### Response Schema (`ProviderActivityAnalyticsResponse`)
```json
{
  "provider_profile_id": 1,
  "business_name": "Alice Outdoor Adventures",
  "date_from": "2026-06-01",
  "date_to": "2026-06-30",
  "activity_filter_id": null,
  "activities": {
    "total_activities": 3,
    "active_activities": 2,
    "approved_activities": 2,
    "pending_activities": 1,
    "draft_activities": 0,
    "suspended_activities": 0,
    "rejected_activities": 0
  },
  "bookings": {
    "total_bookings": 24,
    "confirmed_bookings": 18,
    "completed_bookings": 2,
    "pending_bookings": 2,
    "cancelled_bookings": 2,
    "rejected_bookings": 0,
    "total_participants": 68,
    "confirmed_participants": 56,
    "cancellation_rate": 8.33
  },
  "financials": {
    "total_gross_revenue": "280000.00",
    "paid_revenue": "200000.00",
    "unpaid_revenue": "80000.00",
    "refunded_amount": "0.00",
    "currency": "LKR",
    "by_currency": [
      {
        "currency": "LKR",
        "total_gross_revenue": "280000.00",
        "paid_revenue": "200000.00",
        "unpaid_revenue": "80000.00",
        "refunded_amount": "0.00"
      }
    ]
  },
  "capacity": {
    "total_slots_offered": 15,
    "total_capacity_offered": 150,
    "total_capacity_reserved": 68,
    "occupancy_rate": 45.33
  },
  "breakdown": [
    {
      "activity_id": 1,
      "title": "Knuckles Mountain Trek",
      "category": "adventure",
      "approval_status": "approved",
      "is_active": true,
      "total_bookings": 16,
      "confirmed_bookings": 14,
      "total_participants": 44,
      "gross_revenue": "220000.00",
      "average_rating": 4.85,
      "total_reviews": 12
    }
  ]
}
```

---

## 3. Test Verification & Suite Summary

Comprehensive automated tests are implemented in `tests/test_activity_calendar_analytics.py` (23 tests total):

| Test Name | Requirement | Coverage |
|---|---|---|
| `test_activity_availability_calendar_defaults` | A07 | 30-day default window, day summaries, slot items, remaining capacity |
| `test_activity_availability_date_filtering` | A07 | Custom date range filtering and `start_date`/`end_date` aliases |
| `test_activity_availability_inverted_dates_422` | A07 | Inverted dates validation (`date_from > date_to` -> 422) |
| `test_activity_availability_exceeds_180_days_422` | A07 | Window validation (>180 days -> 422) |
| `test_activity_availability_status_transitions` | A07 | `available`, `limited`, `sold_out`, and inactive slot filtering |
| `test_activity_availability_unapproved_or_inactive_404` | A07 | `public_activity()` publication guard enforcement |
| `test_activity_slots_public_listing_date_filters` | A07 | Backward compatibility on `/slots` with date filters |
| `test_provider_analytics_basic_success` | B01 | Full provider metrics overview and breakdown |
| `test_provider_analytics_route_precedence_no_collision` | B01 | Precedence over `GET /{activity_id}` dynamic path |
| `test_provider_analytics_scoped_to_activity_id` | B01 | Single-activity scoping via `activity_id` query param |
| `test_provider_analytics_cross_provider_isolation` | B01 | Cross-provider isolation check returning 404 |
| `test_provider_analytics_auth_and_rbac_enforcement` | B01 | Role access control: 401 unauth, 403 traveler/non-activity |
| `test_provider_analytics_date_filtering_and_validation` | B01 | Booking and capacity date range filtering |
| `test_provider_analytics_metric_calculations_accuracy` | B01 | Cancellation rate, `gross = paid + unpaid`, occupancy rate |
| `test_provider_analytics_reviews_aggregated_breakdown` | B01 | Reviews aggregation with zero N+1 queries |
| `test_provider_analytics_empty_state_no_division_by_zero` | B01 | Zero-count safety without division by zero errors |
| `test_activity_availability_exact_boundaries_and_past_slots` | A07 | Midnight and 23:59:59 boundaries, past slot exclusion |
| `test_activity_availability_overbooked_slot_no_negative` | A07 | Clamping remaining capacity to 0 without negative output |
| `test_provider_analytics_date_boundary_midnight_and_eod` | B01 | Midnight and EOD timestamp boundary inclusion in analytics |
| `test_provider_analytics_multiple_currencies_and_refunds` | B01 | Multi-currency `by_currency` breakdown and refund tracking |
| `test_provider_analytics_cross_provider_reviews_and_bookings_isolation` | B01 | Complete isolation of reviews, bookings, and revenue between providers |
| `test_provider_analytics_response_privacy` | B01 | Verification of zero traveler PII exposure in analytics responses |
| `test_provider_analytics_suspended_activity_historical_bookings` | B01 | Historical bookings preservation for suspended activities |

---

## 4. Known Limitations & Non-Functional Boundaries

1. **Payment Settlement Provenance:** The `dbo.activity_bookings` table stores `payment_status` (`unpaid`, `pending`, `paid`, `refund_pending`, `refunded`, `failed`) and `payment_method` (`online`, `pay_at_location`). The actual final bank settlement reference (e.g. bank payout transfer) is managed in `dbo.provider_settlements` and is out of scope for Module 03 (managed in Shared Finance/Admin Settlement modules). Consequently, `paid_revenue` reflects collected payment value rather than net bank payout after platform fee deductions.
2. **Calendar Forward Horizon:** The availability calendar enforces a maximum query window of 180 days to protect SQL Server buffer cache and prevent denial-of-service from unbounded date range sweeps.
