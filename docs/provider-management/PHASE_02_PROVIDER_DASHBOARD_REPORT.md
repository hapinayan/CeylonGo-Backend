# Phase 02 Provider Dashboard Report

Phase 02 adds one read-only workspace and dashboard for the six approved provider categories. It reuses the existing roles, provider profiles, listing publication predicates, native booking tables, shared Homestay payment and settlement records, reviews, notifications and Eco-Tourism campaign analytics.

## API contract

Send the active provider's access token as `Authorization: Bearer <access_token>`. The account and approved category come from the database. All four endpoints return `200` for an authorized request. The existing authentication and approval dependencies handle missing or invalid credentials, inactive accounts, non-provider roles, pending or rejected applications and missing provider profiles. Invalid dates, preview limits and pagination return `422`.

| Method and endpoint | Inputs | Response |
| --- | --- | --- |
| GET `/api/v1/provider/workspace` | None | Provider category, own personal and business summary, approval and recorded review, permissions, API navigation and unavailable metrics |
| GET `/api/v1/provider/dashboard` | Optional date pair, `activity_limit` and `reservation_limit` from 1 to 20, each defaulting to 5 | Dashboard metrics, recent activity and upcoming reservations |
| GET `/api/v1/provider/dashboard/analytics` | Optional date pair | Monthly booking counts, revenue breakdowns and sponsored impression totals where supported |
| GET `/api/v1/provider/dashboard/recent-activity` | Optional date pair, `page` from 1 to 1000, `page_size` from 1 to 50, defaults 1 and 20 | Deterministically ordered events with `has_more` |

`start_date` and `end_date` are inclusive ISO dates and must be supplied together in order. A filter can span up to 366 calendar days. The dashboard and activity feed use all available records when dates are omitted. Analytics defaults to the current UTC month through today and the preceding five months. All endpoints are scoped to the authenticated provider. No provider ID is needed in requests.

### Example requests and response fields

```http
GET /api/v1/provider/workspace
GET /api/v1/provider/dashboard?start_date=2026-11-01&end_date=2026-11-30&reservation_limit=5
GET /api/v1/provider/dashboard/analytics?start_date=2026-09-01&end_date=2026-11-30
GET /api/v1/provider/dashboard/recent-activity?page=2&page_size=10
```

The following JSON snippets show selected response fields.

```json
{
  "provider_id": 24,
  "category": "homestay",
  "account_active": true,
  "approval_status": "approved",
  "verification_status": "approved",
  "reviewed_at": "2026-11-09T06:20:00Z",
  "permissions": ["workspace:read", "dashboard:read", "finance:read"],
  "navigation": [{"id":"finance:read","method":"GET","path":"/api/v1/payments/provider/earnings"}]
}
```

```json
{
  "listings": {"total":4,"active":2,"pending_approval":1},
  "bookings": {"total":8,"pending":2,"upcoming":1,"active":1,"completed":3,"cancelled":1},
  "finance": [
    {
      "currency": "LKR",
      "verified_revenue": "1000.00",
      "cash_collections": "300.00",
      "verified_online_refunds": "200.00",
      "verified_cash_refunds": "0.00",
      "commission": "80.00",
      "provider_earnings": "720.00"
    }
  ],
  "settlements": [{"currency":"LKR","pending_count":1,"pending_amount":"720.00","paid_count":0,"paid_amount":"0.00"}],
  "reviews": {"count":2,"average_rating":"4.50"},
  "unread_notifications": 1,
  "upcoming_reservations": [{"booking_id":52,"listing_id":4,"status":"confirmed","starts_on":"2026-11-24","ends_on":"2026-11-26","starts_at":null,"ends_at":null}]
}
```

```json
{
  "period": {"start_date":"2026-11-01","end_date":"2026-11-30"},
  "months": [{"month":"2026-11-01","bookings":8,"revenue":[],"sponsored_impressions":null}],
  "unavailable_metrics": {"profile_views":"No verified provider profile view counter exists."}
}
```

```json
{
  "period": {"start_date":null,"end_date":null},
  "page": 2,
  "page_size": 10,
  "has_more": false,
  "items": [{"kind":"booking_created","occurred_at":"2026-11-03T09:30:00Z","listing_id":4,"booking_id":52,"status":null}]
}
```

For Angular and Flutter, use `permissions` and template paths to display available actions. The underlying routes still authorize each operation. Use `unavailable_metrics` to explain disabled panels. For a supported dataset, a zero count or empty list means no matching records. A null section means the category lacks a usable source. Monetary values and average ratings are decimals serialized as strings. A null average with count zero means there are no published ratings.

## Category capabilities and authorization

| Category | Owned listing source | Booking source | Additional management and metrics |
| --- | --- | --- | --- |
| Homestay | `homestays` | `homestay_bookings` | Existing calendar, reservations, campaigns, reviews, finance and settlement links |
| Artisan and Small Business | `artisan_listings` | Unavailable | Shops, showcases and promotions |
| Culture, Adventure and Activities | `activities` | `activity_bookings` joined through the owned slot and activity | Listing and slot management, reservation preview |
| Eco-Tourism | `eco_experiences` | Unavailable | Experience management and signed sponsored campaign impressions |
| Local Tour Guide | `tour_guides` | Unavailable | Guide profile and submission. Dedicated guide bookings have no operational workflow |
| Rental | `rental_vehicles` | `rental_bookings` joined through the owned vehicle | Vehicle creation, pickup locations and existing status transition |

Each listing, reservation, review and finance query joins back to the provider's source records. Homestay checks its profile and compatible owner association, including legacy owner-only listings. Ledger and history links also match the provider and booking buyer. The feed returns event kind, timestamp, listing ID, optional booking ID and status. It omits customer identity, notes and payment payloads. The workspace includes the provider's own name, email and business summary. KYC files, reviewer identity and registration internals are excluded.

## Metric calculation rules

- Listings reflect the current owned inventory. Active means the category's approved, published or publicly eligible state. Pending means the category's submitted approval state. Drafts remain in the total. The date filter leaves listing counts unchanged.
- Bookings and upcoming previews use native booking rows once, filtered by booking creation time when dates are supplied. Shared references provide links for the ledger and history. Pending, completed and cancelled use the recorded status. Upcoming means confirmed and starts in the future. Active means in progress or confirmed and within its reservation interval. Homestay compares Sri Lankan calendar dates and treats checkout as exclusive. Activity and Rental use UTC timestamps.
- Finance uses verified Homestay ledger receipts with a recorded capture timestamp. `verified_revenue` is gross verified online collections and excludes cash. `cash_collections` is the sum of recorded cash receipts. Online and cash refunds are separate, using processed timestamps for completed reimbursements. Existing legacy and current terminal ledger statuses are supported. Pending or failed transactions do not contribute. A currency mismatch excludes the refund, and different currencies never share a total.
- Commission and provider earnings accrue on completed online bookings using stored splits, falling back to the current 10% commission rule. Completed online refunds reverse the corresponding share at the current commission rate. Monthly receipts appear in the capture month, refunds in the processing month. A refund-only period can therefore have negative commission or provider earnings. These dashboard values are reporting figures. Use the existing settlement endpoint for payable balance and payout eligibility.
- Settlement amounts are current recorded pending/approved and paid totals by currency. They remain independent of the dashboard date filter. Unread notifications count current unread records for the authenticated user.
- Published reviews use owned shared listing references and filter by review creation date. Homestay supports an empty review collection. Other categories expose review totals only when their owned shared references exist.
- Activity uses creation and recorded Homestay booking status events. Listing and booking events filter by their own timestamps. Ties are ordered by event kind and stable source ID, with one extra row fetched to calculate `has_more`.
- Only Eco-Tourism's existing signed sponsored impressions are exposed, bucketed by metric date and scoped to the provider's campaign and listing. General listing and profile view counts are unavailable.

## Existing routes and storage

The existing module routes and administrator permissions remain available. `GET /api/v1/homestays/provider/dashboard` keeps its response shape and now reads actual LKR receipts and the mapped settlement amount. This prevents an exception on pending settlements or cash bookings, excludes unpaid online bookings from legacy earnings and applies the existing ownership rule when listing owner and provider profile conflict. Rental's ORM now maps the existing SQL Server `pickup_at` and `return_at` columns, matching the current Rental API and migrations 019 and 026.

No migration or schema change is required. All test writes use isolated fixtures. The primary database is outside the verification scope.

## Verification

The isolated SQL Server suite passed **8 tests on `CeylonGoDB_IntegrationTest`**. It executed workspace, dashboard and analytics queries for all six categories and checked populated Homestay finance, review and activity data plus Rental timestamp mappings and upcoming reservations. The fixture verified the actual database with `DB_NAME()` before writes. Transactions rolled back, and the six category cases checked fixture removal from a fresh connection.

The focused local runs passed **22 Phase 02 tests**, **248 Phase 01 regressions** and **14 existing Homestay commission, refund and settlement tests**. The suites cover authorization, listing and booking totals, isolated bookings and ledger data, receipt and refund dates, publication, unread counts, sponsored impressions, paging and the existing Homestay and Rental response contracts.

All **4 existing Phase 01 SQL Server cases** passed across two bounded runs. The grouped `pytest -q tests/test_provider_phase01_sqlserver.py` command reported three passing cases before the 115-second wall timeout. The remaining `test_onboarding_lock_conflicts_and_releases_on_sqlserver` case passed in an isolated rerun with a 60-second wall limit. Phase 02 SQL tests live in `tests/test_provider_workspace_sqlserver.py` and are enabled explicitly with `PROVIDER_WORKSPACE_SQLSERVER=1`. Local Phase 02 tests use in-memory SQLite in `tests/test_provider_workspace.py`.

## Remaining dependencies

Angular and Flutter still need navigation, charts and integration acceptance. General profile/listing views have no reliable source. Eco-Tourism, Guide and Artisan need operational reservation workflows before their booking metrics are available. Non-Homestay categories need verified financial integration before earnings and settlements are available.

Existing writer gaps remain. The Homestay cash action references an unmapped `cash_received_amount` attribute. Migration 027 constraints use terminal payment/refund statuses `completed` and `processed`, while current writers use `paid` and `completed` respectively. The dashboard reader supports both sets of recorded statuses. During isolated SQL Server fixture setup, a review write that passed NULL for `reviews.updated_at` failed the column's NOT NULL constraint. The fixture supplied the timestamp and the dashboard then read and aggregated the reviews successfully. The existing review creation handler also relies on that timestamp default. These write paths were outside the Phase 02 reporting scope.
