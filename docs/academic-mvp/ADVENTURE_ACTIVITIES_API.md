# Adventure and Activities API integration contract

Use this contract for the Angular traveler flow and the provider and admin screens. The Academic MVP serves these routes at `/api/v1` and `/api`. The tables below omit the prefix. Protected routes require `Authorization: Bearer <access_token>` from the existing MVP authentication flow. The runtime OpenAPI document at `/openapi.json` provides the complete field definitions.

## Traveler discovery and availability

| Method and path | Authorization | Parameters and response |
| --- | --- | --- |
| `GET /activities` | Public | Optional `destination` (partial district or locality), `category` (activity slug), `date` (Sri Lanka calendar date), `participants` (1–1000), `min_price`, `max_price`, `sort`, `limit` (default 20, max 100), `offset` (default 0). Returns `{items, next_offset}`. |
| `GET /activities/{listing_id}` | Public | Returns one activity with the same shape as a discovery item. |
| `GET /activities/{listing_id}/availability` | Public | Optional `date`, `participants`, `limit` (default 50, max 100), `offset`. Returns upcoming slots in start-time order, including blocked and full slots. |
| `GET /listings/{listing_id}/photos/{photo_id}` | Public | Returns the image when the listing is public and the photo is visible. Follow the URL in `photos[].image_url`. |
| `GET /activities/wishlist` | Traveler | Uses discovery pagination parameters `limit` and `offset` and returns the traveler's saved public activities. |
| `PUT /activities/{listing_id}/wishlist` | Traveler | Empty body. Returns `{"listing_id":41,"saved":true}`. |
| `DELETE /activities/{listing_id}/wishlist` | Traveler | Empty body. Returns `{"listing_id":41,"saved":false}`. |

Use `newest` (default), `price_asc`, `price_desc`, or `title` for sorting. Fetch `next_offset` until it is null. If discovery includes a date or participant filter, the listing must have a future, open slot with sufficient capacity. With a date alone, the listing's own minimum group size is used. Availability also defaults to the listing's minimum. Dates are local to Sri Lanka. Activity availability and booking response timestamps carry a UTC offset. Legacy provider calendar payloads may return offset-free UTC timestamps. Send the booking timestamps directly from the availability response.

The shared listing category is `adventure`. Keep it separate from the activity subcategory.

| Label | Activity slug |
| --- | --- |
| Hiking & Trekking | `hiking-trekking` |
| Wildlife Safari | `wildlife-safari` |
| Water Activities | `water-activities` |
| Adventure Sports | `adventure-sports` |
| Cycling | `cycling` |
| Cultural & Village | `cultural-village` |
| Food & Cooking | `food-cooking` |
| Nature & Eco | `nature-eco` |

Example item returned by discovery and details. Money and coordinates are JSON strings to keep decimal precision. Display the first photo as the cover. A public activity with older, incomplete metadata can return `activity: null` and an empty schedule. The Academic MVP activity data has no review source. Hide the rating when `rating` is null and use the empty review state.

```json
{
  "id": 41,
  "title": "Ella Ridge Hike",
  "description": "A guided hike along the ridge.",
  "district": "Badulla",
  "locality": "Ella",
  "latitude": "6.866700",
  "longitude": "81.046600",
  "price_per_participant": "2500.00",
  "currency": "LKR",
  "activity": {
    "category": "hiking-trekking",
    "meeting_point": "Ella main entrance",
    "duration_minutes": 120,
    "min_participants": 1,
    "max_participants": 8,
    "minimum_age": 12,
    "highlights": ["Ridge viewpoint"],
    "included": ["Guide"],
    "excluded": ["Transport"],
    "equipment": "Bring trail shoes",
    "facilities": null,
    "safety_instructions": "Stay on the marked trail",
    "restrictions": null,
    "cancellation_policy": null
  },
  "photos": [
    {"id": 12, "image_url": "/api/v1/listings/41/photos/12", "alt_text": "Ridge trail"}
  ],
  "provider_id": 9,
  "provider_name": "Ella Trails",
  "rating": null,
  "reviews": []
}
```

Example `GET /activities/41/availability?date=2026-12-12&participants=2` response.

```json
[
  {
    "id": 72,
    "date": "2026-12-12",
    "starts_at": "2026-12-12T03:30:00Z",
    "ends_at": "2026-12-12T05:30:00Z",
    "remaining_capacity": 6,
    "available": true,
    "total_amount": "5000.00",
    "currency": "LKR"
  }
]
```

## Booking and payment

| Method and path | Authorization | Parameters and response |
| --- | --- | --- |
| `POST /bookings` | Traveler | Booking request below. Returns booking with HTTP 201. Replay the identical request key and payload after a timeout. |
| `GET /bookings/my?category=adventure` | Traveler | Own booking records, newest ID first. Optional `limit` (default 50, max 100) and `before_id` (last ID on preceding page). |
| `GET /bookings/{booking_id}` | Booking traveler, owning provider, or admin | Booking record plus `payments` array. Use this for the confirmation screen and status refresh. |
| `PATCH /bookings/{booking_id}/status` | Booking traveler, owning provider, or admin, subject to action rules | Body `{"to_status":"cancelled"}` or other permitted transition. Optional `reason` of up to 500 characters. Returns updated booking. |
| `POST /payments/simulate` | Booking traveler | Body `{"booking_id":105,"request_key":"demo-payment-example","attempt_no":1,"simulate_outcome":"success"}`. Outcome must be explicitly `success` or `failure`. Returns payment. |
| `POST /payments/at-location` | Booking traveler | Body `{"booking_id":105,"request_key":"at-location-example"}`. Returns payment with status `unpaid`. |

```json
{
  "listing_id": 41,
  "slot_id": 72,
  "starts_at": "2026-12-12T03:30:00Z",
  "ends_at": "2026-12-12T05:30:00Z",
  "quantity": 2,
  "expected_total": "5000.00",
  "request_key": "2503798f-7a0c-439b-9b5a-08620661c879"
}
```

```json
{
  "id": 105,
  "listing_id": 41,
  "traveler_id": 21,
  "request_key": "2503798f-7a0c-439b-9b5a-08620661c879",
  "starts_at": "2026-12-12T03:30:00Z",
  "ends_at": "2026-12-12T05:30:00Z",
  "quantity": 2,
  "total_amount": "5000.00",
  "currency": "LKR",
  "status": "pending",
  "created_at": "2026-11-26T11:30:00Z",
  "booking_reference": "ACT-105",
  "slot_id": 72
}
```

Booking creation holds the selected capacity while the request is pending. Cancellation or rejection releases the hold. A repeated key with different booking data and a second active reservation by the same traveler for the same slot both return 409. Use a fresh key of 10–36 characters for a genuinely new action. The booking uses traveler profile contact information. The provider detail endpoint exposes the current name, email, and optional phone to the owning provider. The booking body has no special-request or contact-override fields.

Label the payment option **Academic Demo Online Payment**. The endpoint accepts simulated outcomes and has no card fields. A failed attempt leaves the booking pending and allows a new attempt. Increment `attempt_no` after a failed simulation and use a new payment key. If switching to Pay at Location after a failed simulation, the server assigns the next attempt number. Retrying a recorded payment with the same key and input returns that payment. Once a successful demo or Pay at Location is selected, a further selection returns 409.

```json
{
  "id": 8,
  "booking_id": 105,
  "request_key": "demo-payment-example",
  "attempt_no": 1,
  "method": "simulated",
  "status": "demo_succeeded",
  "display_amount": "5000.00",
  "currency": "LKR",
  "created_at": "2026-11-26T11:31:00",
  "resolved_at": "2026-11-26T11:31:00"
}
```

Payment methods are `simulated` and `at_location`. Activity payment results are `demo_succeeded`, `demo_failed`, and `unpaid`. Pay at Location has a null `resolved_at`. Both methods leave the booking pending until the provider acts. The owning provider may confirm or reject a pending booking and complete a confirmed booking. Confirmation requires an eligible published listing, a future reserved slot, and an accepted payment condition. The traveler, owning provider, or admin can cancel a pending or confirmed booking. `rejected`, `cancelled`, and `completed` are terminal.

## Provider management

Every route in this table requires an approved provider and checks ownership for operations on existing records.

| Method and path | Request and response |
| --- | --- |
| `POST /provider/listings` | Body `{"category":"adventure"}` creates a draft. Returns the review envelope with HTTP 201. |
| `GET /provider/listings?category=adventure` | List of review envelopes. Optional `limit` (default 25, max 100) and `offset`. |
| `GET /provider/listings/{listing_id}` | One review envelope. |
| `PATCH /provider/listings/{listing_id}` | Update title, description, district, locality, both coordinates, and LKR `price` as needed. Send the complete `activity` object when editing activity fields. Returns review envelope. |
| `POST /provider/listings/{listing_id}/photos` | Multipart `file` and optional `alt_text`. Returns photo metadata with HTTP 201. |
| `GET /provider/listings/{listing_id}/photos/{photo_id}` | Authenticated photo preview, including for drafts. |
| `PUT /provider/listings/{listing_id}/photos/{photo_id}/cover` | Empty body. Returns review envelope. |
| `DELETE /provider/listings/{listing_id}/photos/{photo_id}` | Archives the photo. HTTP 204. |
| `GET /provider/listings/{listing_id}/availability` | Slot array, with optional `limit` (default 50, max 100) and `offset`. |
| `POST /provider/listings/{listing_id}/availability` | Calendar body below. Creates a slot with HTTP 201. |
| `PUT /provider/listings/{listing_id}/availability/{slot_id}` | Same full body. Set `is_open: false` to block the slot. Returns updated slot. |
| `PUT /provider/listings/{listing_id}/evidence` | Replaces evidence links and notes. Returns review envelope. |
| `POST /provider/listings/{listing_id}/evidence/files` | Multipart `file`, `kind`, and `redacted` where applicable. Returns `{id, kind, media_type, redacted}` with HTTP 201. |
| `GET /provider/listings/{listing_id}/evidence/files/{file_id}` | Returns the private file. |
| `DELETE /provider/listings/{listing_id}/evidence/files/{file_id}` | Archives the private file. HTTP 204. |
| `POST /provider/listings/{listing_id}/submit` | Empty body. Moves `draft`, `rejected`, or `more_evidence_required` to `pending`. Returns review envelope. |
| `POST /provider/listings/{listing_id}/publish` | Empty body. An approved listing becomes public. Returns review envelope. |
| `POST /provider/listings/{listing_id}/unpublish` | Empty body. Sets `listing.is_active` to false. Returns review envelope. |
| `GET /provider/bookings?category=adventure` | Owned booking records with `room_ids` (legacy inventory-unit IDs) and `payments`. Optional `status`, `limit` (default 25, max 100), and `offset`. |
| `GET /provider/bookings/{booking_id}` | Same record plus `traveler_contact: {full_name, email, phone}` for activities. |
| `PATCH /bookings/{booking_id}/status` | Example body `{"to_status":"rejected","reason":"The trip cannot run"}`. Returns the shared booking record. |

The review envelope contains `listing`, `verification_status`, `submitted_at`, `decided_at`, `decision_reason`, `details`, `homestay_metadata`, and `rooms`. Read the activity form from `details.activity` and its inventory unit ID from `listing.units[0].id`. Use `listing.media[].id` for authenticated photo previews. A valid activity edit creates the unit when needed and keeps its capacity in sync with `max_participants`.

```json
{
  "unit_id": 19,
  "starts_at": "2026-12-12T09:00:00+05:30",
  "ends_at": "2026-12-12T11:00:00+05:30",
  "capacity": 8,
  "is_open": true
}
```

The slot response adds `id` and `listing_id`. A listing's slots cannot overlap. A slot with reservations cannot move or be blocked, and its capacity cannot fall below held places. Reduce scheduled capacity before reducing the listing's maximum. The provider may change valid prices, participant limits, and schedules without triggering reapproval. Changes to the public description, category, location, safety details, other material metadata, or verification evidence trigger reapproval. A positive LKR price, a hosted photo, coordinates, activity metadata, safety instructions, and safety evidence or an explanation are required to submit.

Use `{"links":[{"kind":"maps","url":"https://maps.google.com/"},{"kind":"website","url":"https://example.com/"}],"notes":"Safety evidence explanation"}` for the evidence update. Supported activity link kinds include `maps`, `facebook`, `instagram`, `website`, and `video`. Use file kinds `customer_feedback`, `activity_photo`, `video`, `safety`, `license`, or `credentials` for the corresponding evidence. Customer feedback requires an explicit provider attestation via multipart `redacted=true`.

## Admin verification

| Method and path | Authorization | Request and response |
| --- | --- | --- |
| `GET /admin/listings?category=adventure` | Admin | Review envelope array. Optional `status` (default `pending`), `limit` (default 25, max 100), and `offset`. |
| `GET /admin/listings/{listing_id}` | Admin | Review envelope plus `provider: {id, business_name, email, phone, verification_status}`. |
| `GET /admin/listings/{listing_id}/photos/{photo_id}` | Admin | Authenticated photo preview. |
| `GET /admin/listings/{listing_id}/evidence/files/{file_id}` | Admin | Private evidence file. Read its ID from `details.files`. |
| `POST /admin/listings/{listing_id}/decision` | Admin | Body below. Returns the shared listing object with the updated `approval_status`. |
| `GET /admin/listings/{listing_id}/history` | Admin | Array of `{actor_id, reason, occurred_at, decision}`, newest first. Optional `limit` and `offset`. |

```json
{"approval_status":"more_evidence_required","decision_reason":"Provide the current trail safety certificate"}
```

API status values are `draft`, `pending`, `more_evidence_required`, `rejected`, `approved`, and `suspended`. Publication is represented by `approval_status: "approved"` together with `is_active: true`. An admin can approve, reject, or request more evidence on a pending listing, suspend an approved listing, or reapprove or request evidence on a suspended listing. Decisions other than approval require a reason. After approval, the provider publishes the activity explicitly.

## Error handling and deployment

FastAPI errors use `{"detail":"message"}` for business failures and `{"detail":[...]}` for schema validation. Handle 401 as missing or invalid authentication, 403 as role or ownership denial, 404 as unavailable or missing resource, 409 as a conflict requiring a refresh or different action, and 422 as invalid input. The shared booking API can return 400 for an unavailable listing or an illegal status transition. On a 409 indicating a capacity or price change, refresh availability before retrying with a new selection.

This implementation reuses the existing MVP listing, verification JSON, media, inventory, allocation, booking, payment, and listing wishlist tables. Activity wishlist entries use the existing `homestay_wishlist` table, with both API families scoped by listing category. MVP schema 1.0.4 provides verification storage and 1.0.3 provides wishlist storage. No new migration is needed. Existing Adventure records without an activity setup can be edited by their owners and submitted for approval. Legacy fixtures and records are outside automatic backfill.

The isolated SQLite run passed 33 tests across Adventure, Homestays, shared listing management, authentication, and the broader Academic MVP workflow. A competing-request test used separate database connections and returned one 201 and one 409 for the final places. All 39 documented method and path combinations matched OpenAPI under both prefixes. The filtered discovery query compiled for the SQL Server dialect. Live SQL Server runtime behavior and Angular integration remain unverified.
