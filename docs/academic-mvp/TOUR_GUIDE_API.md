# Tour Guide API integration contract

The Academic MVP serves these routes under `/api/v1` and `/api`. Paths below omit the prefix. Protected requests use `Authorization: Bearer <access_token>` from MVP authentication. The runtime schema is available at `/openapi.json`.

Use the shared listing ID as `listing_id`. The guide service category is `guide`. Package IDs are shared inventory-unit IDs. Guide categories are the existing `cultural_heritage`, `adventure_nature`, `city_village`, and `wildlife_eco` slugs.

## Discovery, availability, and wishlist

| Method and path | Authorization | Request and response |
| --- | --- | --- |
| `GET /tour-guides` | Public | Optional `destination` (partial base district or locality), `limit` (default 20, max 100), `offset` (default 0). Returns `{items, next_offset}`, newest listing ID first. |
| `GET /tour-guides/categories` | Public | Array of `{slug, name, examples}` from the original guide module. |
| `GET /tour-guides/{listing_id}` | Public | Returns `{listing, provider_name, profile, packages}`. |
| `GET /tour-guides/{listing_id}/packages` | Public | Array of active package objects. |
| `GET /tour-guides/{listing_id}/packages/{package_id}` | Public | Returns a single active package belonging to the guide. |
| `GET /tour-guides/{listing_id}/availability` | Public | Required `package_id`. Optional `date` (Sri Lanka calendar date), `participants` (default 1, max 1000), `limit` (default 50, max 100), and `offset` (default 0). Returns upcoming package slots with LKR quotes, earlier start times first. |
| `GET /listings/{listing_id}/photos/{photo_id}` | Public | Image bytes. Follow `listing.media[].image_url`. |
| `GET /tour-guides/wishlist` | Traveler | Paginated `{items, next_offset}` containing the traveler's saved guides that are currently public. Supports the same `limit` and `offset` as discovery. |
| `PUT /tour-guides/{listing_id}/wishlist` | Traveler | Empty body. Returns `{"listing_id":41,"saved":true}`. |
| `DELETE /tour-guides/{listing_id}/wishlist` | Traveler | Empty body. Returns `{"listing_id":41,"saved":false}`. |

Public guide responses require an active, approved listing and an active, approved provider. The `listing` object carries the shared ID, title, description, provider ID, district, locality, coordinates, base price, currency, verification status, publication flag, media, and creation time. The guide bio is `listing.description`. The `profile` object contains the guide metadata shown below. Previously approved MVP guides without this metadata can return `profile: null` and `packages: []`. Display an empty schedule when there are no slots. Private evidence stays in provider and admin routes.

Example response item for availability with `package_id=23&participants=2`.

```json
{
  "id": 72,
  "package_id": 23,
  "starts_at": "2026-12-12T03:30:00Z",
  "ends_at": "2026-12-12T05:30:00Z",
  "available": true,
  "capacity": 4,
  "total_amount": "4500.00",
  "currency": "LKR"
}
```

`capacity` is the slot group-size limit. `available` accounts for blocked slots, overlapping bookings for the same provider, and the requested group size. A guide serves one group at a time, even when two packages have overlapping slots. Select a slot marked available and copy its ID, timestamps, and quoted amount into the booking request. Currency and coordinate decimals are serialized as strings.

## Booking and payment

| Method and path | Authorization | Request and response |
| --- | --- | --- |
| `POST /bookings` | Traveler | Body below. Returns the shared booking object with HTTP 201. |
| `GET /bookings/my?category=guide` | Traveler | Own guide bookings. Optional `limit` (default 50, max 100) and `before_id`. |
| `GET /bookings/{booking_id}` | Booking traveler, owning provider, or admin | Booking object plus `payments` array. |
| `PATCH /bookings/{booking_id}/status` | Booking traveler, owning provider, or admin, subject to transition rules | Body `{"to_status":"cancelled"}`, with optional `reason` up to 500 characters. Returns updated booking. |
| `POST /payments/simulate` | Booking traveler | Body `{"booking_id":105,"request_key":"guide-demo-001","attempt_no":1,"simulate_outcome":"success"}`. Send `success` or `failure` explicitly. Returns the shared payment object. |
| `POST /payments/at-location` | Booking traveler | Body `{"booking_id":105,"request_key":"guide-location-001"}`. Returns a shared payment object with `method: "at_location"` and `status: "unpaid"`. |

```json
{
  "listing_id": 41,
  "slot_id": 72,
  "starts_at": "2026-12-12T03:30:00Z",
  "ends_at": "2026-12-12T05:30:00Z",
  "quantity": 2,
  "expected_total": "4500.00",
  "request_key": "971ea73b-218a-4cb5-b042-bf4380ced412"
}
```

```json
{
  "id": 105,
  "listing_id": 41,
  "traveler_id": 21,
  "request_key": "971ea73b-218a-4cb5-b042-bf4380ced412",
  "starts_at": "2026-12-12T03:30:00Z",
  "ends_at": "2026-12-12T05:30:00Z",
  "quantity": 2,
  "total_amount": "4500.00",
  "currency": "LKR",
  "status": "pending",
  "created_at": "2026-11-26T11:30:00Z",
  "booking_reference": "GUIDE-105",
  "slot_id": 72,
  "package_id": 23
}
```

The booking holds the provider's selected interval while pending or confirmed. Rejection and cancellation release it. Replay the original request body and key of 10–36 characters after a timeout. Changing the selection or amount while reusing the key returns 409. An overlapping assignment or a stale quote also returns 409. After a conflict, refresh availability before making a new selection.

Payment uses the server's stored booking total. Online payment is labeled as an academic simulation and accepts no card data. Demo results are `demo_succeeded` and `demo_failed`. After failure, use a new payment key and increment the attempt number for another simulation. A recorded payment can be replayed with identical input. After a successful demo or selection of Pay at Location, further payment selections return 409.

```json
{
  "id": 8,
  "booking_id": 105,
  "request_key": "guide-location-001",
  "attempt_no": 1,
  "method": "at_location",
  "status": "unpaid",
  "display_amount": "4500.00",
  "currency": "LKR",
  "created_at": "2026-11-26T11:31:00",
  "resolved_at": null
}
```

Both methods leave the booking `pending`. Only the owning provider can confirm, reject, or complete it. Confirmation requires an eligible published guide, a future reserved slot, and either a successful demo or unpaid Pay at Location. The traveler, owning provider, or admin can cancel a pending or confirmed booking. Booking states are `pending`, `confirmed`, `rejected`, `cancelled`, and `completed`.

## Provider listings, profiles, packages, and calendar

Each route requires an approved provider. Actions on existing resources require ownership.

| Method and path | Request and response |
| --- | --- |
| `POST /provider/listings` | Body `{"category":"guide"}`. Returns the listing review envelope with HTTP 201. |
| `GET /provider/listings?category=guide` | Owned review envelopes with optional `limit` (default 25, max 100) and `offset`. |
| `GET /provider/listings/{listing_id}` | Review envelope with guide data at `details.guide`. |
| `PATCH /provider/listings/{listing_id}` | Shared listing fields and full `guide` metadata object when changing the profile. Returns the review envelope. |
| `GET /provider/listings/{listing_id}/packages` | All guide package objects, including inactive packages. |
| `POST /provider/listings/{listing_id}/packages` | Full package body below. Returns the object plus `id` with HTTP 201. |
| `PUT /provider/listings/{listing_id}/packages/{package_id}` | Full package body. Set `is_active: false` to archive. Returns updated package. |
| `GET /provider/listings/{listing_id}/availability` | Shared slot objects with optional `limit` (default 50, max 100) and `offset`. |
| `POST /provider/listings/{listing_id}/availability` | Shared slot body below. Returns the slot plus `id` and `listing_id` with HTTP 201. |
| `PUT /provider/listings/{listing_id}/availability/{slot_id}` | Same full slot body. Use `is_open: false` to close an unreserved slot. |
| `POST /provider/listings/{listing_id}/photos` | Multipart `file` and optional `alt_text`. Returns media metadata with HTTP 201. |
| `GET /provider/listings/{listing_id}/photos/{photo_id}` | Authenticated image preview. |
| `PUT /provider/listings/{listing_id}/photos/{photo_id}/cover` | Empty body. Returns review envelope. |
| `DELETE /provider/listings/{listing_id}/photos/{photo_id}` | Archives photo. HTTP 204. |
| `PUT /provider/listings/{listing_id}/evidence` | Replaces `{links, notes}`. Returns review envelope. Links are objects with `kind` and `url`. |
| `POST /provider/listings/{listing_id}/evidence/files` | Multipart `file`, `kind`, and optional `redacted`. Use `credentials` or `license` for guide qualifications. Customer feedback requires explicit `redacted=true`. |
| `GET /provider/listings/{listing_id}/evidence/files/{file_id}` | Private file download. |
| `DELETE /provider/listings/{listing_id}/evidence/files/{file_id}` | Archives private evidence. HTTP 204. |
| `POST /provider/listings/{listing_id}/submit` | Empty body. Returns review envelope in `pending`. Requires valid listing details, coordinates, hosted photo, positive price, guide profile, a live package, and license or credentials evidence or an explanation. |
| `POST /provider/listings/{listing_id}/publish` | Empty body. Publishes an approved listing. Returns review envelope. |
| `POST /provider/listings/{listing_id}/unpublish` | Empty body. Unpublishes the listing. Returns review envelope. |
| `GET /provider/bookings?category=guide` | Owned records including payments. Optional `status`, `limit`, and `offset`. |
| `GET /provider/bookings/{booking_id}` | Owned record plus `traveler_contact` with `full_name`, `email`, and nullable `phone`. The legacy `room_ids` field carries inventory-unit IDs. |

Example guide listing patch.

```json
{
  "title": "Ella Local Guide",
  "description": "Guided walks and heritage tours around Ella.",
  "district": "Badulla",
  "locality": "Ella",
  "latitude": "6.866700",
  "longitude": "81.046600",
  "price": "4500.00",
  "guide": {
    "years_of_experience": 4,
    "categories": ["cultural_heritage"],
    "languages": [
      {"language_code": "en", "language_name": "English", "proficiency_level": "fluent", "is_primary": true}
    ],
    "service_areas": [{"province": "Uva", "district": "Badulla", "city": "Ella", "is_primary": true}],
    "cancellation_policy": null
  }
}
```

Example package request. Package pricing is `per_group` or `per_person`. `itinerary`, included and excluded services, and the paired meeting coordinates are optional. Currency is LKR.

```json
{
  "title": "Ella Heritage Walk",
  "description": "Explore local history with a guide.",
  "duration_hours": "2.00",
  "pricing_type": "per_group",
  "price": "4500.00",
  "currency": "LKR",
  "max_people": 4,
  "meeting_point": "Ella station",
  "is_active": true
}
```

Example calendar request. Use the package ID as `unit_id`. Open slot duration must match the package, and slot capacity cannot exceed `max_people`.

```json
{
  "unit_id": 23,
  "starts_at": "2026-12-12T09:00:00+05:30",
  "ends_at": "2026-12-12T11:00:00+05:30",
  "capacity": 4,
  "is_open": true
}
```

The shared provider calendar returns offset-free UTC timestamps. Titles and material changes to guide details or packages require reapproval. Routine package price and capacity changes preserve approval where compatible with existing slots. Changing duration requires closing future slots first. Existing reservations prevent calendar edits that would invalidate them.

## Admin verification

| Method and path | Authorization | Request and response |
| --- | --- | --- |
| `GET /admin/listings?category=guide` | Admin | Review envelopes. Optional `status` (default `pending`), `limit`, and `offset`. |
| `GET /admin/listings/{listing_id}` | Admin | Review envelope including `provider` details and submitted package objects under `details.packages`. |
| `GET /admin/listings/{listing_id}/photos/{photo_id}` | Admin | Authenticated photo preview. |
| `GET /admin/listings/{listing_id}/evidence/files/{file_id}` | Admin | Private evidence download. Get the file ID from `details.files`. |
| `GET /admin/listings/{listing_id}/history` | Admin | Decisions with `actor_id`, `reason`, `occurred_at`, and `decision`. |
| `POST /admin/listings/{listing_id}/decision` | Admin | Body `{"approval_status":"approved"}`. Decisions include `approved`, `rejected`, `more_evidence_required`, and `suspended`. Include `decision_reason` except for approval. |

The review envelope contains `listing`, `verification_status`, `submitted_at`, `decided_at`, `decision_reason`, `details`, `homestay_metadata`, and `rooms`. Listing states are `draft`, `pending`, `more_evidence_required`, `approved`, `rejected`, and `suspended`. Shared approval publishes guide listings. Providers can subsequently unpublish or republish approved listings.

## Errors and deployment

Typical errors use `{"detail":"message"}`. Invalid schema requests return 422 with `detail` as an array. Handle 401 for authentication, 403 for authorization, 404 for unavailable resources, 409 for booking, price, or workflow conflicts, and 503 for a missing MVP schema dependency or a busy SQL Server guide booking lock. The shared booking routes can return 400 for unavailable listings or illegal status transitions.

The MVP adapter uses existing listing, verification, inventory, slot, wishlist, booking, and payment tables from the MVP schema, including upgrades 1.0.3 and 1.0.4. No new migration is required. It reuses the legacy package validation and pricing rules, guide category handler, and SQL Server transaction lock. Existing legacy records retain their legacy IDs and runtime. There is no automatic data import. MVP reservations use scheduled packages, including packages priced for a group or per participant. Direct hourly and daily rate booking, reviews, promotions, and live payment processing remain legacy-only.
