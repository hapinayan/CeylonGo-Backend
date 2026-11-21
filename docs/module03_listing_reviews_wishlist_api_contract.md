# CeylonGo Module 03 – Phase 5A API Contract
## Central Listing Synchronization, Activity Reviews & Wishlist Integration

- **Module:** Module 03 – Activities & Experiences
- **Status:** Implemented & Verified
- **Branch:** `feature/m03-listing-reviews-wishlist`
- **Applicable Requirements:**
  - **C01:** Activity Approval & Central Listing Reference Synchronization (`dbo.listing_references`)
  - **A13:** Traveler Activity Ratings & Reviews (`dbo.reviews`)
  - **B17:** Provider Activity Reviews (`dbo.reviews`)
  - **A14:** Traveler Activity Wishlist (`dbo.wishlists`)

---

## 1. Architectural Overview & Central Storage Re-use

Phase 5A bridges Module 03 (Activities & Experiences) with the CeylonGo shared platform infrastructure. Zero new database tables, columns, or migrations were introduced. All operations integrate directly with existing SQL Server tables:

```
+-----------------------------------------------------------------------------------------+
|                                  SHARED DATABASE TABLES                                 |
+-----------------------------------------------------------------------------------------+
|  dbo.listing_references                                                                 |
|  - id (PK), module_type='activity', target_id (activity.id), provider_profile_id,       |
|    title, district, city, latitude, longitude, is_active, created_at                    |
+-----------------------------------------------------------------------------------------+
         ^                                           ^
         | (FK: listing_reference_id)                | (FK: listing_reference_id)
+---------------------------------+         +----------------------------------+
|  dbo.reviews                    |         |  dbo.wishlists                   |
|  - id (PK)                      |         |  - id (PK)                       |
|  - listing_reference_id (FK)    |         |  - user_id (FK -> users.id)      |
|  - booking_reference_id (FK)    |         |  - listing_reference_id (FK)     |
|  - user_id (FK -> users.id)     |         |  - notes, created_at             |
|  - rating (1.00 - 5.00)         |         |  UQ(user_id,                     |
|  - title, comment               |         |     listing_reference_id)        |
|  - is_verified_booking (bit)    |         +----------------------------------+
|  - is_published (bit)           |
|  - created_at, updated_at       |
+---------------------------------+
```

---

## 2. Central Listing Synchronization (C01)

### 2.1 Core Synchronization Engine
- **Implementation File:** [app/core/listing_sync.py](file:///c:/Users/komaruthan/Desktop/project%204/CeylonGo-Backend/app/core/listing_sync.py)
- **Functions:**
  - `sync_activity_listing_reference(db: Session, activity: Activity) -> ListingReference | None`
  - `sync_activity_booking_reference(db: Session, booking: ActivityBooking, activity: Activity) -> BookingReference | None`

### 2.2 Lifecycle Synchronization Triggers
| Event | Trigger Location | Action on `dbo.listing_references` |
|---|---|---|
| **Admin Activity Approval** | `POST /api/v1/admin/activities/{id}/approve` | Creates or activates row: `is_active = True`, updates metadata. |
| **Admin Activity Rejection** | `POST /api/v1/admin/activities/{id}/reject` | Sets `is_active = False` on existing reference. |
| **Provider Active Toggle** | `PATCH /api/v1/provider/activities/{id}/active` | Sets `is_active = payload.is_active` (`True` only if approved). |
| **Provider Activity Update** | `PATCH /api/v1/provider/activities/{id}` | Synchronizes `title`, `district`, `city`, `latitude`, `longitude`. |

---

## 3. Traveler Ratings & Reviews (A13)

### 3.1 Submit Verified Review
- **Endpoint:** `POST /api/v1/activities/{activity_id}/reviews`
- **Role:** Authenticated Traveler (`require_traveler`)
- **Status Codes:** `201 Created`, `400 Bad Request`, `404 Not Found`, `409 Conflict`, `422 Unprocessable Content`

#### Request Payload (`ActivityReviewCreateRequest`):
```json
{
  "booking_id": 142,
  "rating": 4.5,
  "title": "Breathtaking views and great guide!",
  "comment": "The trail was well marked and Alice was an extraordinary host."
}
```

#### Verification & Business Rules:
1. **Traveler Role Verification:** Caller must be an active traveler.
2. **Activity Verification:** Activity must exist, be `approved`, and currently `is_active=True`.
3. **Booking Verification:** `booking_id` must belong to the caller, be associated with `activity_id`, and have `booking_status == 'completed'`. Uncompleted bookings (`pending`, `confirmed`, `rejected`, `cancelled`) are rejected with `400 Bad Request`.
4. **Duplicate Prevention:** Exactly one review per booking reference is permitted. Subsequent attempts return `409 Conflict`.
5. **Rating Validation:** Rating must be between `1.0` and `5.0`.
6. **Provider Notification:** An in-app notification row is created in `dbo.notifications` for the activity provider.

#### Success Response (`201 Created`):
```json
{
  "id": 88,
  "listing_reference_id": 24,
  "booking_reference_id": 112,
  "user_id": 4,
  "reviewer_name": "Traveler Charlie",
  "rating": "4.50",
  "title": "Breathtaking views and great guide!",
  "comment": "The trail was well marked and Alice was an extraordinary host.",
  "is_verified_booking": true,
  "is_published": true,
  "created_at": "2026-11-21T08:30:00",
  "activity_id": 10,
  "activity_title": "Knuckles Mountain Trek"
}
```

---

### 3.2 Public Activity Reviews List
- **Endpoint:** `GET /api/v1/activities/{activity_id}/reviews`
- **Role:** Public (No authentication required)
- **Query Parameters:**
  - `page` (integer, default `1`, ge `1`)
  - `page_size` (integer, default `10`, ge `1`, le `50`)

#### Success Response (`200 OK`):
```json
{
  "items": [
    {
      "id": 88,
      "listing_reference_id": 24,
      "booking_reference_id": 112,
      "user_id": 4,
      "reviewer_name": "Traveler Charlie",
      "rating": "4.50",
      "title": "Breathtaking views and great guide!",
      "comment": "The trail was well marked and Alice was an extraordinary host.",
      "is_verified_booking": true,
      "is_published": true,
      "created_at": "2026-11-21T08:30:00",
      "activity_id": 10,
      "activity_title": "Knuckles Mountain Trek"
    }
  ],
  "total": 1,
  "average_rating": 4.5,
  "page": 1,
  "page_size": 10
}
```

---

## 4. Provider Activity Reviews (B17)

### 4.1 Provider Feedback Overview
- **Endpoints:**
  - Primary: `GET /api/v1/provider/activities/reviews`
  - Alternate Aliases: `GET /api/v1/provider/activity-reviews`, `GET /api/v1/activities/reviews/mine`
- **Role:** Approved Activity Provider (`require_activity_provider`)
- **Query Parameters:**
  - `activity_id` (optional integer: filter reviews by a specific owned activity)
  - `page` (integer, default `1`, ge `1`)
  - `page_size` (integer, default `20`, ge `1`, le `50`)

#### Security & Isolation:
1. Returns reviews across all activities owned by the provider's `provider_profile_id`.
2. If `activity_id` is supplied, ownership is strictly validated. Attempting to query another provider's activity returns `404 Not Found`.
3. Non-providers (travelers, unapproved providers) receive `403 Forbidden`.

---

## 5. Traveler Activity Wishlist (A14)

### 5.1 Add Activity to Wishlist
- **Endpoints:**
  - `POST /api/v1/activities/{activity_id}/wishlist`
  - `POST /api/v1/wishlists/activities/{activity_id}`
- **Role:** Authenticated Traveler (`require_traveler`)
- **Status Codes:** `201 Created`, `400 Bad Request`, `404 Not Found`, `409 Conflict`

#### Business Rules:
- Activity must exist and be `approved` (returns `404 Not Found` if missing or unapproved).
- Activity must be active (returns `400 Bad Request` if `is_active=False`).
- Duplicates return `409 Conflict` ("Activity is already in your wishlist").

#### Success Response (`201 Created`):
```json
{
  "message": "Activity added to wishlist",
  "activity_id": 10,
  "saved": true,
  "wishlist_id": 15
}
```

---

### 5.2 Remove Activity from Wishlist
- **Endpoints:**
  - `DELETE /api/v1/activities/{activity_id}/wishlist`
  - `DELETE /api/v1/wishlists/activities/{activity_id}`
- **Role:** Authenticated Traveler (`require_traveler`)
- **Status Codes:** `200 OK`, `404 Not Found`

#### Success Response (`200 OK`):
```json
{
  "message": "Activity removed from wishlist",
  "activity_id": 10,
  "saved": false,
  "wishlist_id": null
}
```

---

### 5.3 Check Wishlist Status
- **Endpoints:**
  - `GET /api/v1/activities/{activity_id}/wishlist/status`
  - `GET /api/v1/wishlists/activities/{activity_id}/status`
- **Role:** Authenticated Traveler (`require_traveler`)

#### Success Response (`200 OK`):
```json
{
  "activity_id": 10,
  "saved": true
}
```

---

### 5.4 List Traveler Saved Activities
- **Endpoints:**
  - `GET /api/v1/activities/wishlist/me`
  - `GET /api/v1/wishlists/activities`
- **Role:** Authenticated Traveler (`require_traveler`)
- **Query Parameters:**
  - `page` (integer, default `1`)
  - `page_size` (integer, default `20`)
  - `available_only` (boolean, default `false`)

#### Graceful Availability Handling:
- If a saved activity is active and approved: `is_available = true` with rich activity summary (price, duration, cover image, rating stats).
- If an activity was subsequently deactivated or suspended: `is_available = false` with `activity = null` (client UI can render unavailable gracefully without 500 errors).
- When `available_only=true`: Unavailable items are filtered from both results and total count.

#### Success Response (`200 OK`):
```json
{
  "items": [
    {
      "wishlist_id": 15,
      "saved_at": "2026-11-21T08:35:00",
      "is_available": true,
      "activity": {
        "id": 10,
        "title": "Knuckles Mountain Trek",
        "category": "adventure",
        "district": "Kandy",
        "city": "Teldeniya",
        "price": "5000.00",
        "currency": "LKR",
        "duration_minutes": 240,
        "minimum_age": 12,
        "cover_image_url": "https://img.test/knuckles_cover.jpg",
        "average_rating": 4.5,
        "total_reviews": 1
      }
    }
  ],
  "total": 1,
  "page": 1,
  "page_size": 20,
  "total_pages": 1
}
```

---

## 6. Security, Privacy & SQL Server Concurrency Protections

### 6.1 Traveler Review Eligibility & Concurrency Serialization
- **Role Guard:** Strictly restricted to authenticated users with `role == 'traveler'`.
- **Booking Match:** The booking must be owned by the caller (`user_id == current_user.id`) and match the activity slot (`slot.activity_id == activity_id`).
- **Completion Check:** Only bookings in `completed` status can be reviewed. Pending, confirmed, rejected, and cancelled bookings are rejected with `400 Bad Request`.
- **SQL Server Application Lock:** Review submission acquires an exclusive transaction-level lock (`sp_getapplock` on `activity_booking_{booking_id}`) via `lock_booking_resource()`. This serializes concurrent review attempts for the same booking, eliminating race conditions before duplicate checks.
- **Duplicate Prevention:** Exactly one review per booking reference. Concurrent duplicate requests return `409 Conflict`.
- **Privacy & Data Protection:** Review responses strictly expose only public review fields and reviewer name (`User.full_name` or fallback `"Verified Traveler"`). Sensitive booking data, participant emergency contacts, phone numbers, passport/NIC, and medical disclosures are never retrieved or exposed in review payloads.

### 6.2 Wishlist Isolation & Concurrent Save Hardening
- **User Isolation:** `user_id` is resolved strictly from the verified JWT token (`current_user.id`). Travelers can neither read nor delete items from other users' wishlists.
- **Activity Eligibility:** Wishlist addition requires the activity to exist, be approved (`approval_status == 'approved'`), and active (`is_active == True`). Deactivated activities return `400 Bad Request`; unapproved listings return `404 Not Found`.
- **Unique Constraint & Concurrency Guard:** The database unique constraint `UQ_wishlists_user_listing` prevents duplicate saves. Concurrent insertion conflicts catch `IntegrityError` and cleanly return `409 Conflict` rather than unhandled 500 server errors.
- **Graceful Deactivation Handling:** Historical wishlist items pointing to deactivated activities remain in the user's wishlist with `is_available = false` and `activity = null`, preventing client crashes while allowing travelers to view and remove their saved items.

### 6.3 Provider Review Authorization & Cross-Provider Isolation
- **Provider Role & Approval Guard:** Restricted to approved service providers with `provider_type == 'activity'`.
- **Ownership Scoping:** The endpoint scopes queries strictly to `ListingReference` rows where `provider_profile_id == provider_profile.id`.
- **Filter Guard:** If an optional `activity_id` query parameter is supplied, ownership of the activity is validated against the authenticated provider profile; unowned activity IDs return `404 Not Found`.

---

## 7. Verification and Test Results

Automated regression suite execution confirmed 100% test passing rate:

```
tests/test_activity_listing_reviews_wishlist.py ................... [ 19 passed]
tests/test_activity_booking_lifecycle.py ......................... [ 28 passed]
tests/test_provider_activity_management.py ...................... [ 16 passed]
tests/test_activities_public.py ................................. [ 12 passed]
tests/test_activity_booking_participant_orm.py .................. [  6 passed]
tests/test_activity_booking_management.py ....................... [  6 passed]
tests/test_m04_m03_integration_contract.py ....................... [ 10 passed]
tests/test_eco_experience_core.py ............................... [ 14 passed]
tests/test_eco_reservations.py .................................. [ 14 passed]
tests/test_eco_experiences_api.py ............................... [ 18 passed]
tests/test_provider_phase03.py .................................. [ 10 passed]

Total: 153 passed, 0 failed
```
