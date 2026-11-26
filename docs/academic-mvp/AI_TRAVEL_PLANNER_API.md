# Academic MVP AI travel planner API

All routes below are mounted under both `/api/v1` and `/api`. Send `Authorization: Bearer <traveler access token>` on every request. Planner routes require the traveler role and limit access to the authenticated traveler's own conversations, trips and bookings.

## Endpoints

| Method | Path | Request | Success |
| --- | --- | --- | --- |
| POST | `/ai/planner/conversations` | `{"title":"Northern Sri Lanka"}` | 201, `Thread` |
| GET | `/ai/planner/conversations` | `limit=20`, `offset=0` | 200, `Thread[]` |
| GET | `/ai/planner/conversations/{conversation_id}` | — | 200, `Thread` |
| PATCH | `/ai/planner/conversations/{conversation_id}` | `{"title":"New title"}` | 200, `Thread` |
| DELETE | `/ai/planner/conversations/{conversation_id}` | — | 204 |
| GET | `/ai/planner/conversations/{conversation_id}/messages` | `limit=20`, optional `before_id` | 200, `Turn[]`, newest first |
| POST | `/ai/planner/conversations/{conversation_id}/messages` | `{"text":"Plan a 5-day trip to Northern Sri Lanka for two people for LKR 100,000."}` | 201, `Turn` |
| POST | `/ai/planner/booking-quotes` | `Selection` | 200, `Proposal` |
| POST | `/ai/planner/bookings/confirm` | `Confirm` | 200, `BookingDetailResponse` |
| POST | `/ai/planner/trips` | `{"title":"Jaffna trip","turn_id":123}` | 201, `SavedTrip` |
| GET | `/ai/planner/trips` | `limit=20`, `offset=0` | 200, `SavedTrip[]` |
| GET | `/ai/planner/trips/{trip_id}` | — | 200, `SavedTrip` |
| PATCH | `/ai/planner/trips/{trip_id}` | `{"title":"Updated trip","turn_id":124}`. Omit `turn_id` to rename only. | 200, `SavedTrip` |
| DELETE | `/ai/planner/trips/{trip_id}` | — | 204 |
| POST | `/itinerary/generate` | `{"destination":"Jaffna","duration_days":3,"budget_level":"moderate","interests":["culture"]}` | 200, existing `ItineraryResponse` |
| POST | `/payments/simulate` | `{"booking_id":123,"request_key":"demo-attempt-123","attempt_no":1,"simulate_outcome":"success"}` | 200, shared `PaymentResponse` |
| POST | `/payments/at-location` | `{"booking_id":123,"request_key":"location-attempt-123"}` | 200, shared `PaymentResponse` |
| GET | `/bookings/{booking_id}` | — | 200, shared `BookingDetailResponse` |

List limits accept 1–50. Conversations and trips sort by latest update, then ID. To page messages, set `before_id` to the oldest turn ID in the preceding page. There is no request body for DELETE. Deletion removes the record from normal reads. A saved trip retains its itinerary snapshot if its conversation is later deleted.

## Conversation and itinerary

`Thread` has `id`, `title`, `created_at` and `updated_at`. `Turn` has `id`, `created_at`, `user_message`, `assistant_message`, `listings`, `quotes`, `bookings`, nullable `itinerary` and nullable `proposal`. A follow-up question is returned in `assistant_message` when the model needs essential details. The server saves a turn only after successful generation and validation. Recent conversation turns provide context for changes such as new dates, budget, transport, or selected-day regeneration. Use a new turn to update a saved trip. `SavedTrip` adds `itinerary` to the thread fields.

`Itinerary` contains:

- `plan` with title, summary, optional start date, traveler count, LKR budget, interests, preferences, style, consecutive days, and alternative service references.
- Each day contains `day`, `destination`, `morning`, `afternoon`, `evening`, `transport`, `service_refs`, and separate LKR estimates for food, transport and other expenses.
- `listings` and `quotes` provide the actual catalog records or dated quotes behind the references. A listing without a quote is a suggestion whose cost remains unquoted.
- `quoted_services_lkr`, `estimated_other_lkr`, `estimated_total_lkr`, and `remaining_budget_lkr` are calculated by the server. A quoted service referenced on multiple days is counted once. Alternatives are excluded from the total. Negative remaining budget indicates an over-budget plan.

Quote and budget amounts are serialized as decimal strings in LKR. Transport suggestions are general planning estimates. The MVP does not supply verified live traffic, travel times or road distances. The one-shot endpoint keeps the legacy response fields and uses an assumed single traveler and a budget band. The conversation API accepts precise natural-language preferences and retains the itinerary.

## Read-only agent tools

| Tool | Input and result |
| --- | --- |
| `search_services` | Category and destination. Returns up to six approved and published services ordered by advertised price. Categories include `homestay`, `adventure`, `guide`, `bike`, `bicycle`, and `artisan`. |
| `service_details` | `listing_id`. Returns published catalog facts and supported options, including room amenities and guide packages. |
| `quote_availability` | `Selection`. Returns an authoritative candidate quote using the existing inventory services. |
| `my_booking_status` | `booking_id`. The traveler is taken from JWT context. Returns the actual booking and latest payment state for an owned booking. |

Listing and quote references are scoped to the successful turn. The server rejects references that were absent from its tool results. A catalog price is informational while a dated quote is authoritative for its selection. Provider descriptions, evidence, account emails and credentials are omitted from tool results. Titles and user content are untrusted data. Model tools have no write access.

## Quote, confirm and pay

A booking request in conversation can return a `proposal` when the backend finds a valid quote. A client can also use the standalone quote endpoint without a Gemini connection.

```json
{
  "listing_id": 42,
  "quantity": 2,
  "starts_at": "2026-12-15T00:00:00+05:30",
  "ends_at": "2026-12-17T00:00:00+05:30"
}
```

`Selection` requires `listing_id` and `quantity`. Timed activity and guide requests accept `day`, `slot_id`, or exact offset-aware start/end timestamps. Optionally specify `package_id` for a guide or `room_id` for a homestay. Homestays use Sri Lanka check-in and check-out dates. Rentals require offset-aware pickup and return timestamps and count vehicles as quantity. Artisans are discovery and direct-contact only.

`Proposal` contains `quote`, `confirmation_token` and `expires_at`. The quote includes listing ID, category, title, UTC start and end, quantity, `total_lkr`, nullable room, slot and package IDs, and `motorcycle_license_required`. Display the exact quote before the traveler confirms.

```json
{"confirmation_token":"<signed token from proposal>","confirm":true}
```

For a motorized rental, supply `motorcycle_license_confirmed: true` and `license_valid_until` in the confirmation body when applicable. Send these fields to the confirmation endpoint. Keep them out of chat.

The 10-minute token is bound to the authenticated traveler and credential versions, exact selection and price. Replaying it within its validity returns the same booking through the shared idempotency key. The booking service rechecks price and availability transactionally. A conflict requires a fresh quote and explicit confirmation. A successful response contains the actual booking ID and status, which normally starts as `pending`.

After confirmation, use the shared payment endpoints. Online payment is an Academic MVP simulation and accepts only a simulated outcome. Pay at Location records `unpaid`. Use the shared booking read or the booking-status tool to display actual payment and booking states. Provider booking decisions stay in the existing workflow.

## Configuration and errors

Set backend environment variable `GEMINI_API_KEY`. `GEMINI_MODEL` defaults to `gemini-3.5-flash-lite`. `GEMINI_TIMEOUT_SECONDS` defaults to 12 per upstream request and accepts 1–20 seconds. The gateway uses separate Gemini requests for tool calling and structured JSON output.

| Status | Meaning |
| --- | --- |
| 401 | Missing or invalid traveler authentication |
| 403 | Role denied or confirmation belongs to another traveler session |
| 404 | Owned planner record, public listing, or owned booking unavailable |
| 409 | Stale or expired quote, unavailable inventory, or concurrent conversation edit |
| 422 | Invalid request, missing required fields, or a selected turn has no itinerary |
| 429 | Per-traveler generation limit or Gemini rate limit |
| 502 | Gemini transport error, invalid output or unverified reference |
| 503 | Missing provider configuration or missing MVP schema 1.0.6 |
| 504 | Gemini request timed out |

Business errors use FastAPI's `{"detail":"message"}` envelope. Validation errors return HTTP 422. Gemini errors produce no fabricated plan. Shared booking and payment errors continue to apply.

Conversation and saved-trip persistence requires the additive MVP upgrade [1.0.6](../../scripts/mvp_upgrades/1.0.6_ai_travel_planner.sql). It creates three planner tables and leaves existing application tables unchanged. Quote confirmation reuses the shared booking tables and needs no additional token table. Applying the upgrade to CeylonGoDB requires separate approval.
