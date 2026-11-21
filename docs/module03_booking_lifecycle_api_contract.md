# CeylonGo Module 03 – Activities & Experiences
## Booking Lifecycle, Participant Safety, Cash Payments & Session Cancellation API Contract

**Module:** Module 03 – Activities & Experiences  
**API Endpoints:**
- `/api/v1/activity-bookings`
- `/api/v1/provider/bookings/{booking_id}/action`
- `/api/v1/provider/activities/{activity_id}/slots/{slot_id}/cancel-session`  
**Authentication:** Bearer JWT token (`role="traveler"` for bookings, `role="provider"` with `provider_type="activity"` for provider operations)  
**Specification Version:** 1.0.0  
**Phase:** Phase 4 – Booking Lifecycle, Participant Safety, Cash Payments & Session Cancellation  

---

### 1. Overview & Architecture

Phase 4 implements end-to-end booking lifecycle management and safety controls for activities:
1. **Traveler Booking Creation with Participants:** Travelers can book active future slots for approved activities, providing detailed participant records (full names, ages, emergency contact, waiver agreement, special requirements).
2. **Safety & Minimum Age Invariants:** Validates participant counts, enforces `activity.minimum_age` per participant, and enforces mandatory signed waivers and emergency contacts on adventure category activities.
3. **Pending Lifecycle & Provider Approval:** Bookings initialize in `"pending"` status (holding slot capacity) until explicitly accepted or rejected by the operating activity provider.
4. **Capacity Preservation & Release:** Accepting a booking transitions it from `"pending"` to `"confirmed"` while preserving reserved capacity. Rejecting or cancelling a booking transitions it to `"rejected"` or `"cancelled"` and releases reserved capacity exactly once.
5. **On-Site Cash Payment Recording:** Providers can record on-site cash payments for confirmed `pay_at_location` bookings, atomically creating a `PaymentTransaction` record in the shared ledger and updating `payment_status` to `"paid"`.
6. **Weather & Emergency Session Cancellation:** Allows providers to cancel an entire activity slot session (e.g., due to severe weather or safety alerts), cancelling all associated bookings, resetting slot reserved capacity, deactivating the slot, and notifying travelers.

---

### 2. Endpoints Summary

| HTTP Method | Route Path | Access / Role | Summary | Requirements |
|---|---|---|---|---|
| `POST` | `/api/v1/activity-bookings` | Traveler | Create new activity booking with optional participant records | A08, A12, D01 |
| `GET` | `/api/v1/activity-bookings/me` | Traveler | List all personal activity bookings including participant details | A09 |
| `POST` | `/api/v1/activity-bookings/{booking_id}/cancel` | Traveler | Cancel personal pending or confirmed activity booking | A10, D04 |
| `POST` | `/api/v1/provider/bookings/{booking_id}/action` | Activity Provider | Execute provider actions (`accept`, `reject`, `cash`, `complete`, `cancel`) | B09, B10, B11 |
| `POST` | `/api/v1/provider/activities/{activity_id}/slots/{slot_id}/cancel-session` | Activity Provider | Cancel activity session due to adverse weather or safety conditions | B08, D04 |

---

### 3. Detailed Endpoint Contracts

#### 3.1 Create Activity Booking (Traveler)

- **Method:** `POST`
- **Path:** `/api/v1/activity-bookings`
- **Auth:** Bearer JWT (`role="traveler"`)
- **Status Codes:**
  - `201 Created`: Booking successfully created in `pending` status.
  - `403 Forbidden`: Authenticated user is not a traveler.
  - `404 Not Found`: Activity or future active slot not found, or provider unapproved.
  - `409 Conflict`: Insufficient slot capacity or overbooking conflict.
  - `422 Unprocessable Content`: Validation failure (age restriction, missing waiver, emergency contact missing on adventure activity, participant count mismatch).

**Request Body (`ActivityBookingCreate`):**
```json
{
  "activity_slot_id": 42,
  "participants": 2,
  "payment_method": "pay_at_location",
  "participants_list": [
    {
      "full_name": "Alice Silva",
      "age": 28,
      "emergency_contact_name": "Kamal Silva",
      "emergency_contact_phone": "+94771234567",
      "special_requirements": "Vegetarian",
      "waiver_signed": true
    },
    {
      "full_name": "Nimal Silva",
      "age": 25,
      "emergency_contact_name": "Kamal Silva",
      "emergency_contact_phone": "+94771234567",
      "special_requirements": null,
      "waiver_signed": true
    }
  ]
}
```

*Safety & Backward Compatibility Rules:*
- **Adventure Activities (`category="adventure"`):** `participants_list` is strictly mandatory. Every participant must have `waiver_signed=true` and valid non-empty `emergency_contact_name` and `emergency_contact_phone`. Leaving `participants_list` omitted or incomplete is rejected with HTTP 422.
- **Age-Restricted Activities (`minimum_age > 0`):** `participants_list` is strictly mandatory. Every participant must have their `age` specified and `age >= activity.minimum_age`. Leaving `participants_list` or participant `age` omitted is rejected with HTTP 422.
- **Standard Activities (`category != "adventure"` and `minimum_age == 0`):** `participants_list` is optional. If omitted by legacy clients, `participants` is stored as an aggregate count without creating child participant records.

**Response Body (`ActivityBookingResponse`):**
```json
{
  "id": 105,
  "activity_slot_id": 42,
  "user_id": 12,
  "participants": 2,
  "total_price": "2000.00",
  "currency": "LKR",
  "payment_method": "pay_at_location",
  "payment_status": "unpaid",
  "booking_status": "pending",
  "cancellation_reason": null,
  "cancelled_at": null,
  "created_at": "2026-11-20T16:00:00Z",
  "updated_at": "2026-11-20T16:00:00Z",
  "participants_list": [
    {
      "id": 201,
      "activity_booking_id": 105,
      "full_name": "Alice Silva",
      "age": 28,
      "emergency_contact_name": "Kamal Silva",
      "emergency_contact_phone": "+94771234567",
      "special_requirements": "Vegetarian",
      "waiver_signed": true,
      "created_at": "2026-11-20T16:00:00Z"
    },
    {
      "id": 202,
      "activity_booking_id": 105,
      "full_name": "Nimal Silva",
      "age": 25,
      "emergency_contact_name": "Kamal Silva",
      "emergency_contact_phone": "+94771234567",
      "special_requirements": null,
      "waiver_signed": true,
      "created_at": "2026-11-20T16:00:00Z"
    }
  ]
}
```

---

#### 3.2 Provider Booking Actions

- **Method:** `POST`
- **Path:** `/api/v1/provider/bookings/{booking_id}/action`
- **Auth:** Bearer JWT (`role="provider"`, `provider_type="activity"`, `approval_status="approved"`)
- **Status Codes:**
  - `200 OK`: Action applied successfully; returns updated `BookingDetail`.
  - `403 Forbidden`: User is not an approved activity provider.
  - `404 Not Found`: Booking does not belong to provider's activities.
  - `409 Conflict`: Invalid status transition or business rule violation.

**Supported Actions:**

##### 1. `action: "accept"`
- **Precondition:** Booking is in `"pending"` status and slot start time is in the future.
- **Effect:** Sets `booking_status = "confirmed"`. Reserved capacity on slot is preserved.
- **Idempotency:** Repeated calls on already confirmed bookings succeed without side-effects.

```json
{
  "action": "accept"
}
```

##### 2. `action: "reject"`
- **Precondition:** Booking is in `"pending"` or `"confirmed"` status.
- **Effect:** Sets `booking_status = "rejected"`, records `cancellation_reason` and `cancelled_at`. Releases slot reserved capacity exactly once.
- **Idempotency:** Repeated calls on already rejected bookings succeed without releasing capacity below zero.

```json
{
  "action": "reject",
  "reason": "Guide unavailable due to severe weather alert"
}
```

##### 3. `action: "cash"` / `action: "record_cash"`
- **Precondition:** Booking is `"confirmed"`, `payment_method == "pay_at_location"`, and `payment_status == "unpaid"`.
- **Effect:**
  - Sets `payment_status = "paid"`.
  - Records a `PaymentTransaction` row in the shared database with `payment_method="cash"`, `payment_status="paid"`, and matching `total_price`.
  - Emits status change event to booking status history and notifications.
- **Idempotency:** Repeated calls when already `"paid"` succeed without creating duplicate payment transaction records.

```json
{
  "action": "cash",
  "reason": "Payment received in cash at arrival"
}
```

##### 4. `action: "complete"`
- **Precondition:** Booking is `"confirmed"` and slot `ends_at <= now`.
- **Effect:** Sets `booking_status = "completed"`.

##### 5. `action: "cancel"`
- **Precondition:** Booking is in `"pending"` or `"confirmed"`, `slot.starts_at > now`, `payment_status == "unpaid"`.
- **Effect:** Sets `booking_status = "cancelled"` and releases capacity.

---

#### 3.3 Activity Session Cancellation (Weather / Safety)

- **Method:** `POST`
- **Path:** `/api/v1/provider/activities/{activity_id}/slots/{slot_id}/cancel-session`
- **Auth:** Bearer JWT (`role="provider"`, `provider_type="activity"`, `approval_status="approved"`)
- **Status Codes:**
  - `200 OK`: Session cancelled successfully; returns `SessionCancellationResponse`.
  - `403 Forbidden`: Provider does not own activity.
  - `404 Not Found`: Activity or slot not found.
  - `409 Conflict`: Session has already concluded (`slot.ends_at <= now`).
  - `422 Unprocessable Content`: Reason is missing or shorter than 5 characters.

**Request Body (`SessionCancellationRequest`):**
```json
{
  "reason": "Severe rainfall and flash flood alert issued by Disaster Management Centre"
}
```

**Response Body (`SessionCancellationResponse`):**
```json
{
  "slot_id": 42,
  "activity_id": 10,
  "cancelled_bookings_count": 3,
  "released_capacity": 7,
  "is_active": false,
  "reason": "Severe rainfall and flash flood alert issued by Disaster Management Centre"
}
```

**Guarantees:**
- Slot is deactivated (`is_active = False`).
- Slot reserved capacity is reset to `0`.
- All pending and confirmed bookings on the slot are marked `"cancelled"`.
- Reason is recorded in `cancellation_reason = "Session cancelled by organizer: <reason>"`.
- Booking references and status histories are updated and travelers are notified.
- Idempotent: Repeated cancellation on an inactive slot with no active bookings returns `cancelled_bookings_count: 0, released_capacity: 0`.

---

### 4. Concurrency & Transaction Safety Invariants

1. **Capacity Protection:**
   - Reservation uses conditional SQL update (`UPDATE activity_slots SET reserved_count = reserved_count + :p WHERE id = :slot_id AND is_active = 1 AND reserved_count <= capacity - :p`) preventing overselling under concurrent requests.
   - Slot capacity release is called exactly once when transitioning to `"rejected"` or `"cancelled"`.
2. **Atomicity:**
   - Booking row and all `ActivityBookingParticipant` rows are added in the same transaction; on any exception, all changes are rolled back.
   - Cash recording updates `PaymentTransaction`, `ActivityBooking.payment_status`, and `BookingReference.payment_status` within one atomic transaction.
3. **Information Privacy:**
   - Participant emergency contact phone numbers and special requirements are strictly inaccessible from public discovery APIs and only accessible to the owning provider and the booking traveler.
4. **Notification Reality & In-App Persistence:**
   - Status change and cancellation notifications are persisted directly into the `dbo.notifications` table as in-app unread notifications (`is_read = 0`) and logged in `dbo.booking_status_history`.
   - Notifications are **not** external push notifications (SMS/Email), as external messaging services are not configured for activity session cancellations.
5. **Database Status Invariant:**
   - Implemented statuses (`pending`, `confirmed`, `rejected`, `cancelled`, `completed`) are fully validated and supported by the verified SQL Server check constraint `CK_activity_bookings_status`.
