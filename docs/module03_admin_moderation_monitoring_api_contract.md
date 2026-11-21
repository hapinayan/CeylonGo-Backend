# CeylonGo Module 03 – Phase 5B API Contract
## Admin Activity Moderation & Platform-Wide Booking Monitoring

- **Module:** Module 03 – Activities & Experiences
- **Status:** Implemented & Verified
- **Branch:** `feature/m03-admin-moderation-monitoring`
- **Applicable Requirements:**
  - **C03:** Admin Activity Moderation (`POST /api/v1/admin/activities/{activity_id}/suspend`, `POST /api/v1/admin/activities/{activity_id}/unsuspend`)
  - **C04:** Admin Booking Monitoring (`GET /api/v1/admin/activity-bookings`, `GET /api/v1/admin/activity-bookings/{booking_id}`)

---

## 1. Architectural Overview & Storage Integration

Phase 5B completes the administrative oversight requirements for Module 03 without adding new tables or modifying database schemas. All operations integrate directly with existing Microsoft SQL Server and SQLAlchemy data models:

```
+---------------------------------------------------------------------------------------------------+
|                                      ADMIN OPERATIONS (PHASE 5B)                                  |
+---------------------------------------------------------------------------------------------------+
|                                                                                                   |
|  [Admin User (role='admin')]                                                                      |
|             |                                                                                     |
|             +---> POST /api/v1/admin/activities/{id}/suspend                                      |
|             |     - Sets approval_status='suspended', is_active=False                             |
|             |     - Atomic sync with dbo.listing_references (is_active=False)                     |
|             |     - In-app notification to provider (dbo.notifications)                           |
|             |     - Audit log recorded (dbo.admin_audit_logs)                                     |
|             |                                                                                     |
|             +---> POST /api/v1/admin/activities/{id}/unsuspend                                    |
|             |     - Sets approval_status='approved', is_active=True                               |
|             |     - Atomic sync with dbo.listing_references (is_active=True)                      |
|             |     - In-app notification to provider (dbo.notifications)                           |
|             |     - Audit log recorded (dbo.admin_audit_logs)                                     |
|             |                                                                                     |
|             +---> GET /api/v1/admin/activity-bookings                                             |
|             |     - Platform-wide listing with filtering (status, provider, activity, dates)      |
|             |     - Deterministic pagination (page, page_size, total, total_pages)                |
|             |     - Joined eager loading (ActivitySlot, Activity, ProviderProfile, User)          |
|             |                                                                                     |
|             +---> GET /api/v1/admin/activity-bookings/{id}                                        |
|                   - Detailed view with central booking_references & booking_status_history        |
|                   - STRICT PARTICIPANT PRIVACY: emergency contacts & medical notes excluded       |
|                                                                                                   |
+---------------------------------------------------------------------------------------------------+
```

---

## 2. Database Schema & Check Constraint Compatibility

Metadata inspection verified against `CeylonGoDB_Development` confirmed native database support:

| Table | Constraint Name | Definition / Verified Status Values | Result |
|---|---|---|---|
| `dbo.activities` | `CK_activities_status` | `approval_status IN ('draft', 'pending', 'approved', 'rejected', 'suspended')` | Supported natively |
| `dbo.activity_bookings` | `CK_activity_bookings_status` | `booking_status IN ('pending', 'confirmed', 'rejected', 'cancelled', 'completed')` | Supported natively |
| `dbo.activity_bookings` | `CK_activity_bookings_payment` | `payment_status IN ('unpaid', 'pending', 'paid', 'refund_pending', 'refunded', 'failed')` | Supported natively |
| `dbo.activity_bookings` | `CK_activity_bookings_method` | `payment_method IN ('pay_at_location', 'online')` | Supported natively |
| `dbo.listing_references` | `CK_listing_references_type` | `module_type IN ('rental_vehicle', 'artisan', 'eco_experience', 'activity', 'tour_guide', 'homestay')` | Supported natively |

Zero database migrations, schema alterations, or shared development database writes were introduced.

---

## 3. Admin Activity Moderation (C03)

### 3.1 Activity Suspension

- **Endpoint:** `POST /api/v1/admin/activities/{activity_id}/suspend`
- **Authorization:** Authenticated User with `role == 'admin'` (`require_admin`).
- **Request Headers:** `Authorization: Bearer <token>`
- **Request Body:**
```json
{
  "reason": "Safety audit flagged missing life jackets at venue."
}
```

#### Validation & Guards:
1. `activity_id` must reference an existing activity (`404 Not Found` if missing).
2. `reason` must have at least 10 non-whitespace characters (`422 Unprocessable Entity` if < 10 chars).
3. If activity is already `suspended`: returns `409 Conflict` (`"Activity is already suspended"`).
4. If activity is not currently `approved`: returns `409 Conflict` (`"Only approved activities can be suspended"`).
5. Non-admin roles (traveler, provider) receive `403 Forbidden`.

#### State Mutations:
- `Activity.approval_status = "suspended"`
- `Activity.is_active = False`
- `Activity.rejection_reason = payload.reason`
- `Activity.reviewed_by = admin.id`
- `Activity.reviewed_at = utc_now()`
- `ListingReference.is_active = False` (synchronized atomically via `sync_activity_listing_reference`)
- Notification dispatched to provider: `Notification(user_id=provider.user_id, notification_type="activity_suspended")`
- Admin audit log created: `AdminAuditLog(admin_user_id=admin.id, action="activity_suspend", entity_name="activity", entity_id=activity_id)`

#### Response (`200 OK`):
Returns the updated `ActivityResponse` schema with `approval_status: "suspended"` and `is_active: false`.

---

### 3.2 Activity Unsuspension / Reinstatement

- **Endpoint:** `POST /api/v1/admin/activities/{activity_id}/unsuspend`
- **Authorization:** Authenticated User with `role == 'admin'` (`require_admin`).
- **Request Headers:** `Authorization: Bearer <token>`
- **Request Body (Optional):**
```json
{
  "reason": "Provider verified safety compliance certificates."
}
```
*(Accepts empty body `{}` or null reason)*

#### Validation & Guards:
1. `activity_id` must reference an existing activity (`404 Not Found` if missing).
2. If activity is not currently `suspended`: returns `409 Conflict` (`"Only suspended activities can be unsuspended"`).
3. Provider profile must still be in `approved` status. If provider was revoked/rejected, returns `409 Conflict` (`"Cannot unsuspend activity because provider profile is not approved"`).
4. Non-admin roles receive `403 Forbidden`.

#### State Mutations:
- `Activity.approval_status = "approved"`
- `Activity.is_active = True`
- `Activity.rejection_reason = None`
- `Activity.reviewed_by = admin.id`
- `Activity.reviewed_at = utc_now()`
- `ListingReference.is_active = True` (synchronized atomically via `sync_activity_listing_reference`)
- Notification dispatched to provider: `Notification(user_id=provider.user_id, notification_type="activity_unsuspended")`
- Admin audit log created: `AdminAuditLog(admin_user_id=admin.id, action="activity_unsuspend", entity_name="activity", entity_id=activity_id)`

#### Response (`200 OK`):
Returns the updated `ActivityResponse` schema with `approval_status: "approved"` and `is_active: true`.

---

## 4. Platform-Wide Admin Booking Monitoring (C04)

### 4.1 Global Booking Listing

- **Endpoint:** `GET /api/v1/admin/activity-bookings`
- **Authorization:** Authenticated User with `role == 'admin'` (`require_admin`).
- **Query Parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `booking_status` | string | `None` | Filter by: `pending`, `confirmed`, `rejected`, `cancelled`, `completed` |
| `payment_status` | string | `None` | Filter by: `unpaid`, `pending`, `paid`, `refund_pending`, `refunded`, `failed` |
| `payment_method` | string | `None` | Filter by: `pay_at_location`, `online` |
| `provider_profile_id` | integer | `None` | Filter by specific provider profile ID (>= 1) |
| `activity_id` | integer | `None` | Filter by specific activity ID (>= 1) |
| `date_from` / `start_date` | date | `None` | Filter bookings created on or after date (`YYYY-MM-DD`) |
| `date_to` / `end_date` | date | `None` | Filter bookings created on or before date (`YYYY-MM-DD`) |
| `page` | integer | `1` | 1-based page number (>= 1) |
| `page_size` | integer | `20` | Items per page (1 to 100) |

#### Validation & Guards:
- Invalid `booking_status` or `payment_status` returns `422 Unprocessable Entity`.
- Inverted date range (`date_from > date_to`) returns `422 Unprocessable Entity`.
- Non-admin callers receive `403 Forbidden`.

#### Response (`200 OK`):
```json
{
  "items": [
    {
      "id": 501,
      "booking_reference": "BK-ACT-501-A1B2C3D4",
      "activity_id": 10,
      "activity_title": "Weligama Beginner Surf Camp",
      "provider_profile_id": 1,
      "provider_business_name": "Silva Surf Academy",
      "traveler_id": 4,
      "traveler_name": "Jane Doe Traveler",
      "traveler_email": "traveler@ceylongo.test",
      "booking_status": "confirmed",
      "created_at": "2026-11-20T10:00:00",
      "slot_id": 101,
      "starts_at": "2026-11-23T08:00:00",
      "ends_at": "2026-11-23T10:00:00",
      "participants": 3,
      "total_price": "13500.00",
      "currency": "LKR",
      "payment_status": "unpaid",
      "payment_method": "pay_at_location",
      "cancellation_reason": null,
      "cancelled_at": null
    }
  ],
  "pagination": {
    "page": 1,
    "page_size": 20,
    "total": 1,
    "total_pages": 1,
    "has_next": false,
    "has_prev": false
  }
}
```

---

### 4.2 Detailed Booking Inspection & Privacy Guarantees

- **Endpoint:** `GET /api/v1/admin/activity-bookings/{booking_id}`
- **Authorization:** Authenticated User with `role == 'admin'` (`require_admin`).
- **Path Parameter:** `booking_id: int`

#### Strict Privacy & Security Invariant:
To uphold the CeylonGo Data Privacy & Protection Policy:
1. **Excluded Sensitive Data:** Emergency contact names, emergency contact telephone numbers, medical/special dietary requirements, and signed waiver flags are **strictly scrubbed and excluded** from the response.
2. **Permitted Participant Data:** Only `id`, `full_name`, and `age` are exposed.
3. **Excluded Financial Secrets:** Raw gateway responses, authorization tokens, and payment secrets are omitted.
4. **Lifecycle Audit Trail:** Full transition history is pulled from `dbo.booking_status_history`.

#### Response (`200 OK`):
```json
{
  "id": 501,
  "booking_reference": "BK-ACT-501-A1B2C3D4",
  "activity_id": 10,
  "activity_title": "Weligama Beginner Surf Camp",
  "activity_category": "adventure",
  "district": "Matara",
  "city": "Weligama",
  "provider_profile_id": 1,
  "provider_business_name": "Silva Surf Academy",
  "provider_phone": "+94771112233",
  "traveler_id": 4,
  "traveler_name": "Jane Doe Traveler",
  "traveler_email": "traveler@ceylongo.test",
  "slot_id": 101,
  "starts_at": "2026-11-23T08:00:00",
  "ends_at": "2026-11-23T10:00:00",
  "participants": 3,
  "participants_list": [
    {
      "id": 1,
      "full_name": "Jane Doe",
      "age": 28
    },
    {
      "id": 2,
      "full_name": "Bob Smith",
      "age": 30
    },
    {
      "id": 3,
      "full_name": "Charlie Brown",
      "age": 12
    }
  ],
  "total_price": "13500.00",
  "currency": "LKR",
  "payment_status": "unpaid",
  "payment_method": "pay_at_location",
  "booking_status": "confirmed",
  "created_at": "2026-11-20T10:00:00",
  "updated_at": "2026-11-20T10:15:00",
  "cancellation_reason": null,
  "cancelled_at": null,
  "status_history": [
    {
      "id": 10,
      "from_status": "pending",
      "to_status": "confirmed",
      "changed_by": 2,
      "notes": "Provider confirmed booking on pay_at_location",
      "created_at": "2026-11-20T10:15:00"
    }
  ]
}
```

---

## 5. System-Wide Suspension Isolation & Invariants

| Component | Behavior When Activity Is Suspended | Verification Status |
|---|---|---|
| **Public Search** (`GET /api/v1/activities`) | Activity excluded via `public_activity()` predicate (`is_active == True and approval_status == 'approved'`). | Verified |
| **Public Detail** (`GET /api/v1/activities/{id}`) | Returns `404 Not Found`. | Verified |
| **Public Slot Listing** (`GET /api/v1/activities/{id}/slots`) | Returns `404 Not Found`. | Verified |
| **New Traveler Bookings** (`POST /api/v1/activity-bookings`) | Returns `404 Not Found` (`"Approved activity not found"`). | Verified |
| **New Wishlist Saves** (`POST /api/v1/activities/{id}/wishlist`) | Returns `404 Not Found` (requires approved status). | Verified |
| **Central Listing Visibility** (`dbo.listing_references`) | Synchronized atomically to `is_active = False`. | Verified |
| **Existing Bookings** | Preserved with existing status; **no silent cancellations**. | Verified |
| **Slot Capacities** | Existing reserved capacity (`reserved_count`) **preserved**; not released. | Verified |
| **Payment Records & Refunds** | Preserved intact; suspension does **not** trigger automatic payment refunds. | Verified |
| **Audit Log Trail** | Admin actions logged to `dbo.admin_audit_logs`. | Verified |

---

## 6. Transaction Safety & Rollback Verification (Step 3)

All state updates and side effects within `suspend_activity` and `unsuspend_activity` execute within a unified atomic transaction boundary:
1. `write_review_snapshot()` performs optimistic locking checks on `ACTIVITY_REVIEW_STATE`.
2. `sync_activity_listing_reference()` updates `dbo.listing_references`.
3. `create_notification()` dispatches in-app notification to `dbo.notifications`.
4. `AdminAuditLog` record added to `dbo.admin_audit_logs`.
5. Atomic `db.commit()` commits all mutations together; on any error, `db.rollback()` reverts all mutations completely.

Automated tests in `tests/test_admin_activity_moderation_monitoring.py` verify zero partial state remains if any of the following fail:
- Failure during listing synchronization (`test_suspend_rollback_on_listing_sync_failure`).
- Failure during notification dispatch (`test_suspend_rollback_on_notification_failure`).
- Failure during audit log creation (`test_suspend_rollback_on_audit_log_failure`).
- Failure during listing synchronization on unsuspension (`test_unsuspend_rollback_on_sync_failure`).

---

## 7. Route Precedence & Collision Verification (Step 6)

Route registrations were verified with live FastAPI request matching:
- `GET /api/v1/admin/activities` -> `list_pending_activities`
- `GET /api/v1/admin/activities/{activity_id}` -> `get_activity_review_details`
- `POST /api/v1/admin/activities/{activity_id}/suspend` -> `suspend_activity`
- `POST /api/v1/admin/activities/{activity_id}/unsuspend` -> `unsuspend_activity`
- `GET /api/v1/admin/activity-bookings` -> `list_admin_activity_bookings`
- `GET /api/v1/admin/activity-bookings/{booking_id}` -> `get_admin_activity_booking_detail`

No collisions, routing shadows, or intercepting handlers exist between static and dynamic paths.

---

## 8. Automated Test Suite Reconciliation (Step 8)

| Suite File | Scope | Exact Tests Collected |
|---|---|---|
| `tests/test_admin_activity_moderation_monitoring.py` | Phase 5B Moderation & Monitoring | **24** |
| `tests/test_activity_listing_reviews_wishlist.py` | Phase 5A Sync, Reviews & Wishlist | **19** |
| `tests/test_activity_booking_lifecycle.py` | Phase 4 Booking Lifecycle | **18** |
| `tests/test_provider_activity_management.py` | Phase 3 Provider Activity Management | **16** |
| `tests/test_activities_public.py` | Phase 2 Public Search & Discovery | **12** |
| `tests/test_activity_booking_participant_orm.py` | Phase 1/4 Participant Safety & Constraints | **8** |
| `tests/test_activity_booking_management.py` | Activity Booking Management | **4** |
| `tests/test_m04_m03_integration_contract.py` | Module 03/04 Shared Integration Contract | **7** |
| `tests/test_eco_experience_core.py` | Eco-Tourism Core Domain Tests | **45** |
| `tests/test_eco_reservations.py` | Eco-Tourism Shared Reservations | **3** |
| `tests/test_eco_experiences_api.py` | Eco-Tourism Discovery API | **7** |
| `tests/test_provider_phase03.py` | Provider Profile Verification Baseline | **14** |
| `tests/test_eco_wishlists_p06.py` | Shared Eco Wishlist Baseline | **12** |
| `tests/test_artisan_reviews.py` | Shared Artisan Reviews Baseline | **24** |
| **TOTAL** | **Full Regression Suite** | **213 passed, 0 failed** |
