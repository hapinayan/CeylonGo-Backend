# Phase 02B implementation plan

## Proposed canonical contract

The contract below has 70 method/path templates under /api/v1. Phase 01 budgeted about 76 operations. Dispatching four categories through the same validated service parameter reduces the number of templates. Where role, error, input or transaction behavior differs materially, retain existing contracts or split the template after parity verification. Legacy aliases are excluded from the proposed count. These paths are proposals, not mounted routes.

| Module | Role | Methods and paths | Count |
|---|---|---|---:|
| TR-01 | Public / Traveler | POST /auth/check-email, /auth/register, /auth/login, /auth/logout; GET/PATCH /auth/me | 6 |
| TR-02?05 | Public read / Traveler booking | GET /catalog/{service}, /catalog/{service}/{id}, /catalog/{service}/{id}/availability, /catalog/{service}/{id}/price; POST /bookings; GET /me/bookings, /bookings/{id}; POST /bookings/{id}/cancel | 8 |
| Shared payment | Traveler | PUT /bookings/{id}/payment; POST /bookings/{id}/payment/simulate; GET /bookings/{id}/payment | 3 |
| TR-06 | Traveler | POST /travel-plans/itinerary | 1 |
| TR-07 | Traveler | GET/POST /community/posts; GET/DELETE /community/posts/{id}; PUT/DELETE /community/posts/{id}/like; GET/POST /community/posts/{id}/comments | 8 |
| TR-08 | Traveler / Public | GET /map/locations, /map/search | 2 |
| TR-09 | Traveler / Public | GET /weather/current, /weather/forecast, /weather/locations | 3 |
| SP-01 | Public / Provider | POST /providers/registration, /providers/login, /providers/logout; GET/PATCH /providers/me; GET /providers/me/verification | 6 |
| SP-02 | Provider | POST /providers/listings, /providers/listings/{id}/photos, /providers/listings/{id}/submit; DELETE /providers/listings/{id}/photos/{photo_id} | 4 |
| SP-03 | Provider | GET /providers/listings, /providers/listings/{id}, /providers/listings/{id}/availability, /providers/bookings, /providers/bookings/{id}; PATCH /providers/listings/{id}; PUT /providers/listings/{id}/availability; POST /providers/bookings/{id}/accept, /providers/bookings/{id}/reject, /providers/bookings/{id}/complete | 10 |
| AD-01 | Admin | GET /admin/providers/pending, /admin/providers/{id}, /admin/listings/pending, /admin/listings/{id}; PUT /admin/providers/{id}/decision, /admin/listings/{id}/decision | 6 |
| AD-02 | Admin | POST /admin/auth/login; GET /admin/auth/me, /admin/travelers, /admin/providers, /admin/listings, /admin/bookings, /admin/bookings/{id}; PATCH /admin/travelers/{id}, /admin/providers/{id}, /admin/listings/{id}; PUT /admin/community/posts/{id}/moderation | 11 |
| Platform | Public health / authorized media | GET /health, /uploads/{id} | 2 |

Catalog validates one of Homestay, Bike/Bicycle, Adventure, or Guide and returns approved active listings, display pricing, and service-specific inventory. Availability and price reads do not allocate capacity. Booking creation verifies traveler access and listing eligibility. Provider decisions verify ownership and allocate capacity atomically. History is role-scoped. Define completion/cancellation actors, time policy, and capacity release before implementation.

Payment records are separate from booking state. Simulation uses bounded success/failure inputs and idempotent retries with no real money or gateway call. Pay at Location leaves payment unpaid and permits provider acceptance. Reject foreign booking access and invalid state changes. One Admin role performs provider/listing decisions and post moderation with audit evidence.

Provider listings reuse current inputs for category, details, photos, price, location, and availability. Provider approval and listing approval remain separate. Registration retains existing verification safeguards and legacy OTP and draft contracts until client parity. Existing auth tokens, ownership checks and auth version checks need parity. AI itinerary accepts destination, duration, and budget and returns day-by-day suggestions without autonomous actions. Directions use a map-service link if approved. Weather retains timeout, quota and failure handling.

## Implementation order and checkpoints

1. Lock canonical contracts and trace existing role guards and both clients. Start with a route/OpenAPI snapshot and an MVP regression checklist. Approve Adventure selection, Bike/Bicycle eligibility, role transitions, directions, credentials and demo retry rules.
2. First implementation batch after approval: add contract checks over existing native booking, auth and discovery behavior. Keep production route registration intact. Verify traveler ranking/visibility only insofar as it serves approved discovery. Keep existing OTP, compatibility aliases, and callback routes. Roll back the new contract layer independently if parity fails.
3. Add demo payment handling, then validate concurrent last-unit booking, authorization, retries, rollback and cash acceptance across all four services. Test invalid transitions and stale listing approval. Keep gateway issuance disabled in MVP mode. Roll back that mode or the compatibility adapter without altering historic transaction records.
4. Verify planner, Community, Map and Weather acceptance and provider/Admin ownership and approval. Coordinate Angular and Flutter changes before widening rollout. Check status codes, validation errors, token selection and shapes. Test markers and catalog together.
5. Review non-MVP wishlist endpoints as the first retirement candidate only after dependency and client sign-off. Snapshot method/path and hidden aliases, run targeted listing and booking regressions and restore prior router registration on failure. Evaluate Artisan, campaigns, sponsorship and analytics as separate batches.
6. Consider physical database changes only after a separate retention and FK review. Keep historic migration checksums and tables. Any isolated schema rehearsal requires explicit approval and backup/restore verification.

Phase 02A ran no application tests. Before implementation or retirement use bounded offline FastAPI tests with overridden dependencies. Candidate suites include existing auth/RBAC, home/rental/guide/activity booking, payment mock, community, map and weather. Execute SQL Server acceptance only on an explicitly approved isolated database.
