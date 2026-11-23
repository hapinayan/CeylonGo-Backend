# Phase 02B Stage 01 client compatibility

All paths are relative to `/api/v1`. This matrix records existing routes and documented gaps. A dash means the source inspection did not establish a concrete API caller for that group. It does not establish that released clients or dynamic call sites are absent. Neither client was built or run.

| Scope | Mounted contract and authorization | Angular | Flutter | Compatibility gate |
|---|---|---|---|---|
| TR-01 Traveler account | POST /auth/register, /auth/login, /auth/check-email and /auth/google. GET /auth/me requires bearer JWT. Existing recovery paths retained. | Account service and auth-entry flow | AuthService calls register, login, me and recovery through ApiConstants | Preserve token and response DTOs, and confirm traveler profile update and logout semantics. |
| TR-02 Homestay | GET /homestays and /homestays/{homestay_id} public. POST /bookings requires traveler. | ? | ? | Keep catalog filters, ownership and blackout behavior. |
| TR-03 Bike/Bicycle | GET /rental-vehicles and /rental-vehicles/{vehicle_id} public. POST /rental-bookings requires traveler. | ? | ? | Preserve interval checks. Agree how the MVP will exclude tuk-tuks. |
| TR-04 Adventure | GET /activities/{activity_id}/slots and /eco-experiences. POST /activity-bookings or /eco-reservations with traveler access. | ? | ? | Choose canonical discovery and booking path. Native activity booking currently confirms on creation, while the MVP requires a provider decision. |
| TR-05 Guide | GET /tour-guides, /tour-guides/{guide_id}, and /tour-guides/{guide_id}/availability public. POST /tour-guides/{guide_id}/bookings and GET /tour-guide-bookings/me are traveler flows. | ? | ? | Keep package pricing, history shape, ownership and overlap protection. |
| TR-06 Basic AI planner | No mounted itinerary route in the snapshot. | ? | ? | Approve a destination, duration and budget request with a bounded itinerary response. |
| TR-07 Community | GET /community/feed, POST /community/posts, DELETE /community/posts/{post_id}, PUT/DELETE /community/posts/{post_id}/like, GET/POST /community/posts/{post_id}/comments. Mutations require authenticated actors and relevant permissions. | ? | ? | Preserve author controls, feed visibility and moderation. |
| TR-08 Map | GET /map/locations and /map/search, POST /map/route. | ? | ? | Confirm client link directions versus server route operation. Keep approved catalog filters. |
| TR-09 Weather | GET /weather/current, /weather/forecast and /weather/locations. | ? | ? | Preserve destination lookup and graceful upstream failures. |
| SP-01 Provider onboarding | POST /providers/otp/send, /providers/otp/verify, /providers/registration/submit, /providers/password/setup, /providers/login. GET/PATCH /providers/me requires provider access. | OTP and registration service, ProviderAuthService, provider token handling in interceptor | No concrete provider API call verified | Keep approval token flow, and preserve prefix or update token selection during coordinated cutover. |
| SP-02 Listing publication | Service-specific listing handlers, including POST /provider/activities and /provider/activities/{activity_id}/submit. Writes require provider ownership and publication approval. | ? | ? | Preserve media validation, fields, ownership and listing approval. |
| SP-03 Service management | GET /provider/bookings, /provider/bookings/{booking_id}; POST /provider/bookings/{booking_id}/action. Provider ownership applies. Native service-specific alternatives remain. | ? | ? | Reconcile guide, rental, homestay and Adventure decision and history state. |
| AD-01 Verification | Admin login and admin profile, GET /admin/providers, POST /admin/providers/{provider_id}/approve or /reject and POST /admin/homestays/{homestay_id}/review. | ? | ? | Preserve authorization and evidence while selecting the approved MVP Admin role. |
| AD-02 Management | GET /admin/travelers, PATCH /admin/travelers/{traveler_id}/status, PATCH /community/admin/posts/{post_id}. Provider/listing and booking views are domain-specific. | ? | ? | Confirm required booking visibility and moderation privileges before consolidation. |
| Shared payment | Existing cash flows and gateway-related operations remain mounted. Native Activity only exposes Pay at Location at creation. There is no verified shared demo contract across all four services. | ? | ? | Settle demo failure/retry semantics, preserve cash and keep payment records distinct from booking status. |

## Client source evidence

- Angular traveler and Google auth: `src/app/core/services/auth.service.ts` and `src/app/pages/auth-entry/auth-entry.ts`.
- Angular provider login and bearer routing: `src/app/core/services/provider-auth.service.ts` and `src/app/core/interceptors/auth.interceptor.ts:24`. Onboarding: `src/app/provider/registration/services/provider-otp.service.ts:34?150`.
- Flutter traveler requests: `lib/features/auth/services/auth_service.dart:23?201` and `lib/core/constants/api_constants.dart:24?26`. Flutter startup checks current token in `lib/main.dart`.
- [Phase 02A endpoint evidence with handler-level source locations](PHASE_02A_ENDPOINT_INVENTORY.csv).

Before cutover, test request DTOs, auth role and header mapping, error formats, pagination, and date/time conventions against supported client versions. Use observed release traffic or coordinated client-owner sign-off before removing compatibility routes.
