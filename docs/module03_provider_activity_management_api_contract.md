# CeylonGo Module 03 – Activities & Experiences
## Provider Activity Management, Images & Slot Scheduling API Contract

**Module:** Module 03 – Activities & Experiences  
**API Prefix:** `/api/v1/provider/activities` and `/api/v1/activities`  
**Authentication:** Bearer JWT token (`role="provider"`, `provider_type="activity"`, `approval_status="approved"`)  
**Specification Version:** 1.0.0  
**Phase:** Phase 3 – Provider Management & Media  

---

### 1. Overview & Security Architecture

Phase 3 implements provider-side administrative controls for activities, photo galleries, and slot scheduling:
1. **Activity Activation & Deactivation:** Allows activity owners to toggle active/inactive status on approved activities.
2. **Photo Gallery Management:** Secure multi-part upload, listing, metadata editing (reordering, captioning), cover photo management, and safe deletion.
3. **Slot Scheduling & Rescheduling:** Allows rescheduling and capacity adjustments on unbooked slots while safeguarding slots with existing reservations or bookings.
4. **Capacity & Concurrency Protection:** Atomic database updates prevent slot capacity from falling below reserved occupancy.

#### Security & Access Control Invariants
- **Role Enforcement:** All endpoints require an authenticated user with `role="provider"` and `provider_type="activity"`. Unapproved or non-activity providers receive `403 Forbidden`.
- **Ownership Verification:** All operations enforce `Activity.provider_profile_id == provider_profile.id`. Attempting to access or mutate an activity owned by another provider returns `404 Not Found` (to avoid information disclosure).
- **Referential & Image File Safety:**
  - Files are stored in dedicated storage (`uploads/activities/`) using randomized server-side UUID filenames (`act_img_{activity_id}_{uuid}.ext`). Client filenames are never trusted.
  - Image content, dimensions (50px to 8000px), format (JPEG, PNG, WebP), and size (<= 5 MB) are verified with Pillow.
  - Path traversal is strictly prevented via `Path.name` clean checks and `Path.is_relative_to(target_dir)` containment validation.
  - If database transaction fails, uploaded files are unlinked to prevent orphaned files.
  - File deletion occurs only after the database record deletion commits successfully.

---

### 2. Endpoints Summary

| Method | Path | Summary | Requirements |
|---|---|---|---|
| `PATCH` | `/api/v1/provider/activities/{activity_id}/active` | Toggle activity active status (activate/deactivate) | B03 |
| `POST` | `/api/v1/provider/activities/{activity_id}/images` | Upload image to activity photo gallery | B04 |
| `GET` | `/api/v1/provider/activities/{activity_id}/images` | List all photos in activity gallery | B04 |
| `PATCH` | `/api/v1/provider/activities/{activity_id}/images/{image_id}` | Update image display order, alt text, or cover flag | B04 |
| `DELETE`| `/api/v1/provider/activities/{activity_id}/images/{image_id}` | Delete photo and remove file from disk | B04 |
| `GET` | `/uploads/activities/{filename}` | Public serving route for activity photos | A05 |
| `PATCH` | `/api/v1/provider/activities/{activity_id}/slots/{slot_id}` | Update slot schedule, capacity, or availability | B08 |
| `DELETE`| `/api/v1/provider/activities/{activity_id}/slots/{slot_id}` | Safely remove an unbooked activity slot | B08 |

---

### 3. Detailed Endpoint Contracts

#### 3.1 Activity Activation & Deactivation (B03)

- **Route:** `PATCH /api/v1/provider/activities/{activity_id}/active`
- **Authentication:** Provider (`activity`)
- **Description:** Activates or deactivates an activity. Activating an activity requires `approval_status == 'approved'`. Draft, pending, rejected, or suspended activities cannot be activated. Deactivation is allowed and preserves existing bookings.

##### Request Body (`ActivityActiveUpdate`)
```json
{
  "is_active": true
}
```

##### Response (200 OK) – `ActivityResponse`
```json
{
  "id": 101,
  "provider_profile_id": 10,
  "title": "Kitulgala White Water Rafting",
  "category": "adventure",
  "district": "Kegalle",
  "city": "Kitulgala",
  "duration_minutes": 180,
  "pricing_type": "per_person",
  "price": "6500.00",
  "currency": "LKR",
  "minimum_age": 12,
  "approval_status": "approved",
  "is_active": true,
  "meeting_point": "Base Camp",
  "created_at": "2026-11-20T10:00:00Z"
}
```

##### Error Responses
- `403 Forbidden`: User is not an approved activity provider.
- `404 Not Found`: Activity does not exist or is not owned by the provider.
- `409 Conflict`: Attempting to activate an activity that is not approved (e.g. `approval_status == "draft"` or `"pending"`).
  ```json
  {
    "detail": "Only approved activities can be activated"
  }
  ```

---

#### 3.2 Activity Image Upload (B04)

- **Route:** `POST /api/v1/provider/activities/{activity_id}/images`
- **Content-Type:** `multipart/form-data`
- **Authentication:** Provider (`activity`)
- **Description:** Uploads a photo to the activity's gallery. Enforces maximum 20 images per activity, 5 MB size limit, dimension constraints (50px to 8000px), and valid image formats (`JPEG`, `PNG`, `WebP`). If `is_cover=True`, automatically unsets previous cover images.

##### Form Data Parameters

| Field | Type | Required | Description |
|---|---|---|---|
| `file` | `UploadFile` | Yes | Image binary file (JPEG, PNG, WebP). Max 5 MB. |
| `alt_text` | `string` | No | Optional accessible description (max 255 chars). |
| `is_cover` | `boolean` | No | Default `false`. If `true`, designates as cover image. |
| `display_order` | `int` | No | Default `0`. Order index for gallery sorting. |

##### Response (201 Created) – `ActivityImageResponse`
```json
{
  "id": 501,
  "activity_id": 101,
  "image_url": "/uploads/activities/act_img_101_e3b0c44298fc.jpg",
  "alt_text": "Team tackling rapid grade 3",
  "display_order": 0,
  "is_cover": true
}
```

##### Error Responses
- `400 Bad Request`: Invalid image format, unsupported extension, corrupt image data, image dimensions < 50px or > 8000px, or limit of 20 images reached.
- `404 Not Found`: Activity not found or not owned by provider.
- `422 Unprocessable Entity`: Display order negative or alt text > 255 chars.

---

#### 3.3 List Provider Activity Images (B04)

- **Route:** `GET /api/v1/provider/activities/{activity_id}/images`
- **Authentication:** Provider (`activity`)
- **Description:** Lists all images belonging to the specified activity, sorted by `display_order ASC, id ASC`.

##### Response (200 OK) – `list[ActivityImageResponse]`
```json
[
  {
    "id": 501,
    "activity_id": 101,
    "image_url": "/uploads/activities/act_img_101_e3b0c44298fc.jpg",
    "alt_text": "Team tackling rapid grade 3",
    "display_order": 0,
    "is_cover": true
  }
]
```

---

#### 3.4 Update Image Metadata (B04)

- **Route:** `PATCH /api/v1/provider/activities/{activity_id}/images/{image_id}`
- **Authentication:** Provider (`activity`)
- **Description:** Updates `alt_text`, `display_order`, or sets an image as the cover image. When `is_cover: true` is provided, any other cover image for the activity is unset to guarantee cover uniqueness.

##### Request Body (`ActivityImageUpdate`)
```json
{
  "alt_text": "Updated caption",
  "display_order": 2,
  "is_cover": true
}
```

##### Response (200 OK) – `ActivityImageResponse`

---

#### 3.5 Delete Activity Image (B04)

- **Route:** `DELETE /api/v1/provider/activities/{activity_id}/images/{image_id}`
- **Authentication:** Provider (`activity`)
- **Description:** Deletes the image record from the database and removes the physical file from the storage directory.

##### Response (204 No Content)

---

#### 3.6 Update Scheduled Slot (B08)

- **Route:** `PATCH /api/v1/provider/activities/{activity_id}/slots/{slot_id}`
- **Authentication:** Provider (`activity`)
- **Description:** Updates schedule timing, capacity, or active availability.
  - **Reschedule Protection:** Rescheduling `starts_at` or `ends_at` is strictly prohibited if `reserved_count > 0` or active bookings exist (`409 Conflict`).
  - **Capacity Protection:** Capacity cannot be reduced below the current `reserved_count` (`409 Conflict`).
  - **Overlap Prevention:** Rescheduled times must not overlap existing active slots for the same activity.

##### Request Body (`ActivitySlotUpdate`)
```json
{
  "starts_at": "2026-12-05T09:00:00Z",
  "ends_at": "2026-12-05T12:00:00Z",
  "capacity": 15,
  "is_active": true
}
```

##### Response (200 OK) – `ActivitySlotResponse`
```json
{
  "id": 401,
  "activity_id": 101,
  "starts_at": "2026-12-05T09:00:00",
  "ends_at": "2026-12-05T12:00:00",
  "capacity": 15,
  "reserved_count": 0,
  "is_active": true,
  "created_at": "2026-11-20T11:00:00"
}
```

##### Error Responses
- `409 Conflict`: Rescheduling a slot with existing reservations/bookings.
  ```json
  {
    "detail": "Cannot reschedule a slot with existing reservations"
  }
  ```
- `409 Conflict`: Reducing capacity below reserved count.
  ```json
  {
    "detail": "Cannot reduce capacity to 3; 5 spots already reserved"
  }
  ```
- `409 Conflict`: Overlapping with an existing active slot.
- `422 Unprocessable Entity`: Start time in past, end time before start time, or capacity < 1.

---

#### 3.7 Safe Delete Activity Slot (B08)

- **Route:** `DELETE /api/v1/provider/activities/{activity_id}/slots/{slot_id}`
- **Authentication:** Provider (`activity`)
- **Description:** Permanently deletes an unbooked slot. If the slot has any reservations (`reserved_count > 0`) or attached bookings in `activity_bookings`, deletion is blocked with `409 Conflict` to preserve traveler booking records and referential integrity. Providers must deactivate (`is_active: false`) booked slots rather than delete them.

##### Response (204 No Content)

##### Error Responses
- `409 Conflict`: Slot has active bookings or history.
  ```json
  {
    "detail": "Cannot delete slot with existing reservations"
  }
  ```

---

### 4. Concurrency & Locking Implementation

1. **Atomic Capacity Reservation:**
   `reserve_activity_capacity` executes an atomic conditional update (`WHERE reserved_count <= capacity - participants`).
2. **Atomic Capacity Update:**
   `update_slot_capacity` executes an atomic conditional update (`WHERE reserved_count <= new_capacity`).
3. **Database Concurrency Guarantee:**
   In Microsoft SQL Server, these queries leverage row-level locking (`UPDLOCK, ROWLOCK`) preventing race conditions between concurrent booking requests and provider capacity modifications.

---

### 5. Deferred Workflows

The following workflows remain explicitly out of scope for Phase 3 and will be addressed in future phases:
- **Phase 4:** Booking acceptance, rejection, cash payment confirmation, session weather cancellation (`cancel-session`).
- **Phase 5:** Admin suspension and moderation audit logs.
- **Phase 7:** PayHere online payments and automatic refund evaluation.
- **Phase 8:** Activity reviews and promotional discounts.
