# Provider Account Management API

Use the MVP provider bearer token. All paths below use the `/api/v1` prefix. Account and dashboard queries are scoped to the signed-in user. The existing provider access policy requires an active, approved provider for login and account operations. Use the existing registration workflow for pre-approval and rejected applications.

| Method | Path | Behavior |
|---|---|---|
| GET | `/providers/me` | Own profile, avatar URL and verification information. Retains the previous response fields. |
| PATCH | `/providers/me` | Update routine fields and submit any material fields for admin review. |
| GET | `/providers/me/avatar` | Authenticated JPEG response, or HTTP 404 when absent. |
| POST | `/providers/me/avatar` | Upload or replace the photograph with multipart field `file`. |
| DELETE | `/providers/me/avatar` | Remove the photograph and return the updated profile. |
| POST | `/providers/change-password` | Change password and invalidate existing sessions and reset links. |
| GET, PATCH | `/provider/settings` | Read or set the dashboard booking-list size. |
| GET | `/provider/dashboard` | Owned listing and booking counts, upcoming and recent bookings, and wallet totals. |

## Profile and admin review

Example PATCH request:

```json
{
  "business_description": "Guided heritage walks in Kandy",
  "business_phone": "+94771234567",
  "website_url": "https://example.org",
  "social_links": ["https://example.org/community"],
  "business_name": "Kandy Heritage Walks"
}
```

Routine fields are `personal_phone`, `business_phone`, `business_description`, `website_url`, and `social_links`. URLs may use HTTP or HTTPS and must omit credentials. Phone fields use the existing Sri Lankan phone validator. Email, IDs, account role, registration date, approval status, verification status, and decision information are read-only. Identity documents and document numbers are omitted from the provider profile response. There is one provider verification workflow. `business_verification_status` is null because there is no separately verified business status.

Changes to `full_name`, `personal_address`, `personal_district`, `business_name`, `business_category`, `business_address`, or `business_district` are staged in `profile_change`. Current verified values are used until approval. The response shows the current values and the proposed fields separately:

```json
{
  "business_name": "Kandy Walks",
  "approval_status": "approved",
  "profile_change": {
    "request_id": "31b2d0ad-47a0-4f45-9781-d405f0e285b4",
    "status": "pending",
    "changes": {"business_name": "Kandy Heritage Walks"},
    "requested_at": "2026-11-27T07:30:00",
    "decided_at": null,
    "decision_reason": null
  }
}
```

`GET /admin/providers` includes `profile_change_pending`. `GET /admin/providers/{provider_id}/application` includes the proposal. To decide it, the admin sends `profile_change_request_id` from the displayed proposal with `verification_status: "approved"` or `"rejected"` and an optional `decision_reason` to `POST /admin/providers/{provider_id}/decision`. Omitting the request ID selects the established account verification action. Stale review IDs and a second material edit while a proposal is pending return HTTP 409. Rejected proposals leave verified fields in effect, and the provider can submit another proposal. Dates without an offset represent UTC.

## Photos and passwords

Submit a JPEG, PNG or WebP file up to 5 MiB. The existing shared avatar pipeline checks file contents and image dimensions, rejects animated images, strips metadata, and stores a resized JPEG with a generated filename. The owner uses the bearer token when fetching the returned `avatar_url`. A missing or removed photo returns HTTP 404. The existing `TRAVELER_AVATAR_DIR` setting configures the shared avatar directory for both roles.

Password changes accept `current_password`, `new_password`, and `confirm_password`. New provider passwords use the existing 12–128 character policy. Successful changes return:

```json
{"message": "Password changed. Please log in again.", "login_required": true}
```

Use the existing `POST /auth/password-reset/request` and `POST /auth/password-reset/confirm` for recovery. Recovery email depends on existing SMTP configuration. Existing login and logout are `POST /providers/login` and `POST /providers/logout`. Password change, password reset, and logout invalidate previously issued tokens. Clear local tokens and return to sign-in after success.

## Settings and dashboard

`PATCH /provider/settings` accepts `{"dashboard_booking_limit": 10}`. Set an integer from 1 to 20. The default is 5. The active in-app notification feed serves travelers. `provider_notifications_available` is false, and provider booking, wallet, service verification and community switches are unavailable. Security and recovery messages are outside preference control.

Example dashboard response for a newly approved provider:

```json
{
  "total_services": 0,
  "published_services": 0,
  "pending_verification_services": 0,
  "rejected_services": 0,
  "total_bookings": 0,
  "pending_bookings": 0,
  "confirmed_bookings": 0,
  "completed_bookings": 0,
  "cancelled_bookings": 0,
  "recent_bookings": [],
  "upcoming_bookings": [],
  "wallet_balance": "0.00",
  "currency": "LKR",
  "free_bookings_remaining": 3,
  "net_commission_paid": "0.00"
}
```

Published means approved and active. Pending verification includes applications awaiting review or evidence. Recent bookings use creation time, and upcoming bookings are future confirmed bookings ordered by start time. Both lists use the existing booking response with separate payment records. Total bookings includes rejected bookings. Balance and commission amounts are decimal strings. The dashboard reads the existing wallet or its opening state and does not create a wallet.

## Angular and Flutter

Use the existing authenticated API client for edits and avatar downloads. Angular can retrieve an authenticated avatar as a Blob and revoke the object URL when it is no longer used. Flutter can provide bearer headers for the image request. On both clients, present the verified profile and pending changes separately, refresh the profile after a decision, and redraw the dashboard after a listing, booking, wallet or preference change. Hide provider notification toggles when the settings response reports them unavailable.

Continue to use the existing provider listing, approval, media, room, package, rental inventory and Artisan showcase APIs. Use `GET /provider/bookings` for filtering, `GET /provider/bookings/{booking_id}` for details and `PATCH /bookings/{booking_id}/status` for eligible decisions. Continue to use the existing wallet and shared community routes.

The implementation writes account preferences, avatar pointers and profile review proposals into the existing provider registration metadata. It reuses the existing avatar storage and admin audit log. MVP schema 1.0.8 requires no database migration for these changes.
