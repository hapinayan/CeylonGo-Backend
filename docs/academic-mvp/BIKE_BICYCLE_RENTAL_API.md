# Bike and Bicycle Rental API integration contract

Use `http://127.0.0.1:8000/api/v1` for the local Academic MVP. The same operations are available under `/api`. Paths below omit the prefix. Protected operations use `Authorization: Bearer <access_token>`. The runtime schema is available at `/openapi.json`.

The public `vehicle_id` and shared `listing_id` are the same ID. Each physical vehicle in a fleet has its own `unit_id`. The shared categories are `bike` for motorized two-wheelers and `bicycle` for pedal bicycles.

| Category | Supported `vehicle_subtype` values | Driving license |
| --- | --- | --- |
| `bike` | `motorcycle`, `scooter` | Motorcycle license attestation is required. |
| `bicycle` | `city`, `mountain`, `road`, `hybrid` | No license fields are required. |

`transmission` accepts `manual`, `automatic`, and `single_speed`. Bicycle listings cannot set `automatic`. For a motorbike, the card field `category` contains `motorcycle` or `scooter` when configured. A listing marked with `fuel_type: "electric"` can display `category: "electric"`. Use `vehicle_type` and `vehicle_subtype` as API filters. Tuk-tuks are available through the legacy runtime.

## Public discovery and quotation

| Method and path | Authorization | Request and response |
| --- | --- | --- |
| `GET /rental-vehicles` | Public | Returns `{total,page,limit,pages,items}`. Query parameters are listed below. |
| `GET /rental-vehicles/categories` | Public | Array of `{category,name,description,requires_license,requires_helmet,active_fleet_count,sample_image_url}`. The count is the number of visible listings in the category. |
| `GET /rental-vehicles/{vehicle_id}` | Public | Rental card plus `rental` specifications, pickup `location`, `images`, `approval_status`, and `requires_driving_license`. |
| `GET /rental-vehicles/{vehicle_id}/images` | Public | Array of photo objects with `id`, `vehicle_id`, `image_url`, `caption`, `display_order`, and `is_primary`. |
| `GET /listings/{listing_id}/photos/{photo_id}` | Public | Image bytes. `primary_image_url` and `images[].image_url` are absolute URLs. |
| `GET /rental-vehicles/{vehicle_id}/reviews` | Public | Supports `page` and `limit`. Returns the empty review collection for MVP listings. |
| `GET /rental-vehicles/{vehicle_id}/availability` | Public | Required `start_date` and `end_date` ISO 8601 timestamps. Optional `quantity` (default 1, max 20). Returns the same shape as a quote. |
| `POST /rental-vehicles/{vehicle_id}/calculate-quote` | Public | Body `{"pickup_at":"2026-12-12T09:00:00+05:30","return_at":"2026-12-13T09:30:00+05:30","quantity":2}`. Returns rental quote. |
| `POST /rental-bookings/validate-dates` | Public | Same body plus `vehicle_id`. Returns the ID, normalized timestamps, quantity, duration, `is_valid`, `collision_type` and `error_message`. |

Discovery accepts `page` (default 1), `limit` (default 20, max 100), partial `district` and `city`, `vehicle_type`, `vehicle_subtype`, `transmission`, daily `min_price` and `max_price`, and `pickup_at` and `return_at` as a pair. With dates, `quantity` filters by the number of vehicles that can fill the entire interval. `verified_only` is accepted because all public results already require an approved listing and an approved, active provider. `sort_by` accepts `newest`, `price_asc`, and `price_desc`. The Angular values `rating` and `popular` use newest order in the absence of MVP review and popularity data.

An empty database with the MVP schema and no published rentals returns HTTP 200.

```json
{"total":0,"page":1,"limit":6,"pages":0,"items":[]}
```

A detail response includes card fields `id`, `provider_id`, `provider_business_name`, `is_provider_verified`, `vehicle_type`, `vehicle_subtype`, `category`, `vehicle_name`, `description`, `price_per_day`, `currency`, `status`, `district`, `city`, and `primary_image_url`. Vehicle specifications and cancellation policy are supplied when configured. The location object has `location_name`, `address`, `district`, `city`, `latitude`, and `longitude`. The first public image is the cover. Optional legacy insurance, deposit, and helmet fields are empty where the MVP has no data. A review response with `total: 0` and `items: []` represents no ratings.

Example quote for a 24-hour and 30-minute rental of two vehicles at LKR 1,500 per day.

```json
{
  "vehicle_id": 41,
  "pickup_at": "2026-12-12T03:30:00Z",
  "return_at": "2026-12-13T04:00:00Z",
  "quantity": 2,
  "is_available": true,
  "available_quantity": 2,
  "duration_days": 2,
  "duration_hours": 24.5,
  "rate": "1500.00",
  "total_price": "6000.00",
  "currency": "LKR",
  "requires_driving_license": false,
  "selected_plan_name": "Daily rate",
  "security_deposit": null
}
```

The server charges one day per started 24-hour interval, with a minimum of one day. The total is billable days × daily rate × quantity. Pickup must be in the future and return must be after pickup, within 90 days. Send timestamps with an offset such as `+05:30` for Sri Lanka. Offset-free timestamps are treated as UTC. For date-only UI, send midnight in the pickup locality. Quote and booking timestamps are returned in UTC.

A unit is available when an open window covers the entire interval, the unit is active, and no block or reservation overlaps. A block takes precedence over an open window. Intervals are half-open, so a return at the next pickup time does not overlap. Date validation returns `collision_type: "none"` when available, `"booked"` when reservations consume the available units, and `"maintenance"` when the active unblocked schedule cannot supply the requested quantity.

## Traveler wishlist

| Method and path | Authorization | Request and response |
| --- | --- | --- |
| `GET /rental-wishlist` | Traveler | Saved public rentals. Returns the discovery page envelope and accepts `page` and `limit`. |
| `GET /rental-wishlist/{vehicle_id}/status` | Traveler | Returns `{"vehicle_id":41,"saved":true}`. |
| `POST /rental-wishlist/{vehicle_id}` | Traveler | Empty body. HTTP 201 with `{"vehicle_id":41,"saved":true,"message":"Vehicle saved","wishlist_id":null}`. |
| `DELETE /rental-wishlist/{vehicle_id}` | Traveler | Empty body. Returns the same shape with `saved:false`. Works for a saved listing that was subsequently unpublished. |

## Booking and payment

| Method and path | Authorization | Request and response |
| --- | --- | --- |
| `POST /bookings` | Traveler | Shared booking request. HTTP 201 for creation and identical replay. |
| `GET /bookings/my` | Traveler | Own booking records. Use `category=bike` or `category=bicycle`. Supports `limit` and `before_id`. |
| `GET /bookings/{booking_id}` | Booking traveler, owning provider, or admin | Shared booking record and `payments`. |
| `PATCH /bookings/{booking_id}/status` | Booking traveler, owning provider, or admin, subject to transition rules | Body `{"to_status":"cancelled"}`, optional `reason`. Returns updated record. |
| `POST /payments/simulate` | Booking traveler | Body `{"booking_id":105,"request_key":"rent-demo-0001","attempt_no":1,"simulate_outcome":"success"}`. Send `success` or `failure` explicitly. |
| `POST /payments/at-location` | Booking traveler | Body `{"booking_id":105,"request_key":"rent-location-0001"}`. Returns payment with `method:"at_location"` and `status:"unpaid"`. |

```json
{
  "listing_id": 41,
  "starts_at": "2026-12-12T03:30:00Z",
  "ends_at": "2026-12-13T04:00:00Z",
  "quantity": 2,
  "expected_total": "6000.00",
  "request_key": "10a19850-4393-4b67-ae70-c9ad81ccb9f3"
}
```

For a `bike` booking, add `"motorcycle_license_confirmed":true` and `"license_valid_until":"2027-01-31"`. The date must cover the Sri Lanka return date. These fields are an attestation and expiry validation. The provider checks the original license before handover. The MVP has no traveler license-document verification record and stores neither a license number nor a document.

```json
{
  "id": 105,
  "listing_id": 41,
  "traveler_id": 21,
  "request_key": "10a19850-4393-4b67-ae70-c9ad81ccb9f3",
  "starts_at": "2026-12-12T03:30:00Z",
  "ends_at": "2026-12-13T04:00:00Z",
  "quantity": 2,
  "total_amount": "6000.00",
  "currency": "LKR",
  "status": "pending",
  "created_at": "2026-11-26T11:30:00Z",
  "booking_reference": "RENT-105",
  "slot_id": null,
  "package_id": null,
  "rental_unit_ids": [23, 24]
}
```

The server assigns units and holds them atomically. Omit `slot_id`. Reuse the original key (10–36 characters) when retrying the same interval, quantity, and price. Reusing it for a different selection returns 409. Rejection and cancellation free the interval. If availability or price changes, refresh the quote and use a new key for a new selection.

Label online payment **Academic Demo Online Payment**. The API accepts no card fields. A failed simulation leaves the booking pending and allows a new attempt. Use a new payment key and increment `attempt_no`. Retrying a recorded payment with the same key and data returns the recorded payment. Both payment methods keep the booking pending. The owning provider confirms or rejects a pending booking, completes a confirmed booking, and may cancel a pending or confirmed booking. The traveler and admin can also cancel a pending or confirmed booking. Confirmation requires a live listing, future reserved vehicles, and either `demo_succeeded` or unpaid `at_location`.

## Provider listing and fleet management

Each operation requires an approved provider. Access to existing listings, units, files, and bookings is limited to the owner.

| Method and path | Request and response |
| --- | --- |
| `POST /provider/listings` | Body `{"category":"bicycle"}` or `{"category":"bike"}`. HTTP 201 with listing review envelope. |
| `GET /provider/listings` | Optional `category`, `limit`, and `offset`. Owned listing review envelopes. |
| `GET /provider/listings/{listing_id}` | Review envelope with editable metadata at `details.rental` and fleet at `listing.units`. |
| `PATCH /provider/listings/{listing_id}` | Shared listing fields and complete `rental` object when editing rental details. Returns review envelope. |
| `POST /provider/listings/{listing_id}/units` | Body `{"label":"Unit 23","capacity":1}`. HTTP 201 with inventory unit. |
| `POST /listings/{listing_id}/units` | Existing shared inventory entry point. Same body and rental capacity rule. |
| `PATCH /provider/listings/{listing_id}/units/{unit_id}` | Body `{"label":"Unit 23","is_active":false}`. Updates vehicle label or activation. |
| `GET /provider/listings/{listing_id}/availability` | Array of slots. Supports `limit` and `offset`. |
| `POST /provider/listings/{listing_id}/availability` | Full slot body below. HTTP 201 with the slot plus `id` and `listing_id`. |
| `POST /listings/{listing_id}/slots` | Existing shared calendar entry point. Same rental slot validation. |
| `PUT /provider/listings/{listing_id}/availability/{slot_id}` | Full slot body. Close or reopen a unit window. |
| `POST /provider/listings/{listing_id}/photos` | Multipart `file` and optional `alt_text`. HTTP 201 with media metadata. |
| `GET /provider/listings/{listing_id}/photos/{photo_id}` | Authenticated draft or published photo bytes. |
| `PUT /provider/listings/{listing_id}/photos/{photo_id}/cover` | Empty body. Returns review envelope. |
| `DELETE /provider/listings/{listing_id}/photos/{photo_id}` | Archives the photo. HTTP 204. |
| `PUT /provider/listings/{listing_id}/evidence` | Replaces `{"links":[{"kind":"maps","url":"https://maps.google.com/"}],"notes":"Optional explanation"}`. Returns review envelope. |
| `POST /provider/listings/{listing_id}/evidence/files` | Multipart `file`, `kind`, and `redacted` where applicable. HTTP 201 with private file metadata. |
| `GET /provider/listings/{listing_id}/evidence/files/{file_id}` | Private evidence download. |
| `DELETE /provider/listings/{listing_id}/evidence/files/{file_id}` | Archives private evidence. HTTP 204. |
| `POST /provider/listings/{listing_id}/submit` | Empty body. Submits the draft or resubmits from rejected or more-evidence state. |
| `POST /provider/listings/{listing_id}/publish` | Empty body. Makes an approved listing public. |
| `POST /provider/listings/{listing_id}/unpublish` | Empty body. Removes the listing from public discovery. |
| `GET /provider/bookings` | Use `category=bike` or `category=bicycle`, with optional `status`, `limit`, and `offset`. Owned records and payments. |
| `GET /provider/bookings/{booking_id}` | Owned booking, payments, and `traveler_contact` with name, email, and optional phone. |

```json
{
  "title": "Ella Mountain Bike",
  "description": "Mountain bicycle for the roads around Ella.",
  "district": "Badulla",
  "locality": "Ella",
  "latitude": "6.866700",
  "longitude": "81.046600",
  "price": "1500.00",
  "rental": {
    "subtype": "mountain",
    "transmission": "manual",
    "model_year": 2025,
    "seating_capacity": 1,
    "specifications": "Front suspension",
    "pickup_name": "Ella station",
    "pickup_address": "Station Road",
    "requirements": "Bring identification for pickup",
    "rental_terms": "Return to the pickup office",
    "cancellation_policy": "Contact the provider"
  }
}
```

```json
{
  "unit_id": 23,
  "starts_at": "2026-12-12T08:00:00+05:30",
  "ends_at": "2026-12-20T18:00:00+05:30",
  "capacity": 1,
  "is_open": true
}
```

Each fleet unit represents one physical vehicle. Create a closed slot to block a period, or edit an existing slot when it has the same start time. To unblock, edit the closed slot and set `is_open:true`. Booked vehicles cannot be deactivated or blocked for a reserved interval. The provider calendar returns UTC timestamps without an offset.

Required for submission are title, description, location, coordinates, positive daily price, rental metadata, an active unit, at least one hosted photo, and ownership or authorization evidence or an explanation. Supported private rental evidence kinds are `vehicle_registration`, `ownership`, `authorization`, `insurance`, `vehicle_photo`, `business_certificate`, and `customer_feedback`. Shared link kinds include `maps`, `facebook`, `instagram`, and `website`. Customer feedback requires an explicit provider redaction attestation. Keep price, inventory and calendar edits separate from material listing edits. Changing vehicle specifications, location, requirements or evidence requires reapproval.

## Admin verification

| Method and path | Authorization | Request and response |
| --- | --- | --- |
| `GET /admin/listings` | Admin | Use `category=bike` or `category=bicycle`. Supports `status`, `limit`, and `offset`. |
| `GET /admin/listings/{listing_id}` | Admin | Review envelope with listing, provider, fleet and private evidence metadata. |
| `GET /admin/listings/{listing_id}/photos/{photo_id}` | Admin | Authenticated photo preview. |
| `GET /admin/listings/{listing_id}/evidence/files/{file_id}` | Admin | Private file bytes. File ID comes from `details.files`. |
| `GET /admin/listings/{listing_id}/history` | Admin | Decision events with `actor_id`, `reason`, `occurred_at`, and `decision`. |
| `POST /admin/listings/{listing_id}/decision` | Admin | Body `{"approval_status":"more_evidence_required","decision_reason":"Provide proof of vehicle ownership"}`. Returns the updated shared listing. |

Listing states are `draft`, `pending`, `more_evidence_required`, `approved`, `rejected`, and `suspended`. Admin decisions accept `approved`, `rejected`, `more_evidence_required`, and `suspended`. A reason is required except for approval. An approved rental stays private until the owning provider publishes it.

## Errors and deployment

Business errors use `{"detail":"message"}`. Schema validation errors are 422 with `detail` as an array. Common statuses are 401 for authentication, 403 for role or ownership, 404 for hidden or missing resources, 409 for stale quotes, unavailable stock, or invalid workflow, and 422 for invalid interval, quantity, or license attestation. The shared booking service can return 400 for an unavailable listing or illegal status transition.

The adapter uses the existing MVP listings, verification metadata, media, inventory, availability, allocations, bookings, payment tables and listing wishlist storage. MVP upgrades 1.0.3 and 1.0.4 provide the wishlist and verification tables. No new migration is required. Legacy rental records retain their existing IDs, and no automatic backfill occurs. Advanced legacy rate plans, deposits, inspections, incident handling, document-based driver eligibility, and live payments remain on the legacy runtime.
