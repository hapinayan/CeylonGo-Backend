# Academic MVP Traveler Account Management API

## Integration

The paths below follow the `/api/v1` prefix. The runtime also mounts them under `/api`. Private endpoints require `Authorization: Bearer <access token>` for an active traveler. Existing authenticated providers and admins retain access to GET /auth/me. Avatar access follows the policy below.

The current Angular account components still use disabled placeholders for several actions. Connect profile save and photo controls to the profile endpoints and replace the local profile state with each returned Profile. Connect the notification service to the paginated feed and unread-count endpoint, and refresh its count after read actions. The feed returns singular categories `booking` and `payment`. Map these to `bookings` and `payments` if retaining the existing Angular display enum. Use HTTP PATCH for mark-one and HTTP POST for mark-all. Connect Settings controls to the preferences endpoints and clear the local session when a password-change response sets `login_required`.

| Method | Path | Input | Success |
| --- | --- | --- | --- |
| GET | /auth/me | None | 200 Profile, existing operation extended |
| PATCH | /auth/me | ProfilePatch JSON | 200 Profile |
| POST | /auth/me/avatar | Multipart field `file` | 200 Profile |
| DELETE | /auth/me/avatar | None | 200 Profile |
| GET | /users/{user_id}/avatar | Positive user ID; optional authorized viewer | 200 image/jpeg |
| POST | /auth/change-password | PasswordChange JSON | 200 PasswordChanged |
| GET | /traveler/preferences | None | 200 Preferences |
| PUT | /traveler/preferences | Preferences JSON | 200 Preferences |
| GET | /notifications | Optional page, page_size, unread_only, category | 200 NotificationPage |
| GET | /notifications/unread-count | None | 200 UnreadCount |
| PATCH | /notifications/{notification_id}/read | Positive notification ID, no body | 200 NotificationItem |
| POST | /notifications/read-all | None | 200 ReadAll |
| GET | /traveler/settings/notifications | None | 200 NotificationSettings |
| PATCH | /traveler/settings/notifications | Partial NotificationSettings JSON | 200 NotificationSettings |

### Profile

PATCH example

```json
{"full_name":"Traveler Name","phone":"+94771234567","country":"Sri Lanka"}
```

Omit unchanged fields. Set phone or country to `null` to clear it. Full name is 2–120 characters. Phone accepts 7–15 digits with an optional leading plus after removing common separators. Country accepts a name up to 80 characters. Extra fields, including email, role, status and password, receive 422.

Example Profile

```json
{
  "id": 27,
  "full_name": "Traveler Name",
  "email": "traveler@example.com",
  "phone": "+94771234567",
  "country": "Sri Lanka",
  "avatar_url": null,
  "email_verified": true,
  "authentication_method": "password",
  "role": "traveler",
  "is_active": true,
  "auth_version": 0,
  "created_at": "2026-11-26T10:30:00",
  "updated_at": "2026-11-27T09:00:00"
}
```

UTC timestamps may omit the offset in existing MVP responses. Authentication method is `google`, `password`, or `null` when provenance cannot be established. Email verification is based on completed OTP evidence or documented Google verification. Legacy accounts without sufficient evidence remain unverified.

### Avatar

Send a JPEG, PNG or WebP file of at most 5 MiB. The server decodes and re-encodes it as metadata-free JPEG, with limits of 4096 pixels per input dimension and 12 million input pixels. The stored JPEG is at most 1024 pixels per dimension. Original filenames have no effect on storage. Upload replaces the avatar after the database commits. Delete clears the reference and cleans up the prior file. `TRAVELER_AVATAR_DIR` optionally sets a persistent, non-executable storage directory. The default is `uploads/traveler_avatars`.

The account owner can fetch their private avatar with a bearer token and display the returned blob using an object URL. Revoke the previous object URL when replacing or removing it. Public retrieval is available for active travelers who have a visible Community post. Community author responses optionally include `author.avatar_url`. Use initials when the URL is missing or the image is inaccessible. Retrieval sends `X-Content-Type-Options: nosniff`.

### Password

```json
{"current_password":"CurrentPassword","new_password":"NewPassword","confirm_password":"NewPassword"}
```

Current password verification and matching confirmation are required. The new password follows the existing Academic MVP registration length policy of 6–128 characters. The request limit is five attempts per traveler per five minutes. After a successful change the backend invalidates prior JWTs and outstanding reset links. Angular should clear its token and return the traveler to sign-in.

```json
{"message":"Password changed. Please log in again.","login_required":true}
```

Known Google-only accounts receive 400 and can use the existing Forgot Password flow to set a password. Historical Google-only accounts cannot be distinguished reliably from other legacy accounts without evidence. Current-password verification is still required.

### Travel preferences

PUT replaces the preferences object. Empty defaults are empty lists and null budgets and travel style. Arrays are bounded and deduplicated. Destinations support Sri Lankan district and locality names up to 80 characters. Budgets are integer LKR amounts between 0 and 1,000,000,000 with minimum no greater than maximum.

```json
{
  "preferred_destinations":["Jaffna","Trincomalee"],
  "interests":["culture","local_food"],
  "accommodation_preferences":["homestay"],
  "transport_preferences":["bicycle"],
  "budget_min":20000,
  "budget_max":100000,
  "travel_style":"local_experiences"
}
```

| Field | Accepted values and display labels |
| --- | --- |
| Interests | `culture` Local Culture, `adventure` Adventure, `wildlife` Wildlife, `nature` Nature, `eco_tourism` Eco-Tourism, `local_food` Local Food, `heritage` Heritage, `beaches` Beaches, `village_experiences` Village Experiences, `artisan_handicrafts` Artisan & Handicrafts, `cycling` Cycling |
| Accommodation | `homestay` Homestay, `guesthouse` Guesthouse, `hotel` Hotel, `hostel` Hostel, `other` Other |
| Transport | `walking` Walking, `bicycle` Bicycle, `motorbike` Motorbike, `scooter` Scooter, `public_transport` Public Transport, `other` Other |
| Style | `budget` Budget Travel, `adventure` Adventure Travel, `cultural` Cultural Travel, `family` Family Travel, `relaxation` Relaxation, `local_experiences` Local Experiences |

### Notifications

Query defaults are `page=1`, `page_size=20`, `unread_only=false`, and no category filter. Page accepts 1–100000 and page_size accepts 1–100. Results sort newest first by time and ID. `total` counts the filtered items and `unread_count` counts all visible unread items for the recipient, including other categories.

```json
{
  "items":[{"id":31,"notification_type":"booking_pending","category":"booking",
    "title":"Booking request submitted","message":"Your request is awaiting provider review.",
    "related_entity_type":"booking","related_entity_id":103,"is_read":false,
    "created_at":"2026-11-27T09:00:00","read_at":null}],
  "total":1,"page":1,"page_size":20,"unread_count":1
}
```

Mark-one returns an item of this shape with the original read timestamp on repeats. Unread count returns `{"unread_count":0}`. Mark-all returns `{"updated_count":1,"unread_count":0}`. Angular can route `booking` to My Bookings detail, `community_post` to the corresponding Community detail, and `account` to Settings. Use the typed entity and numeric ID rather than building links from notification text.

| Category | Event values |
| --- | --- |
| `booking` | `booking_pending`, `booking_confirmed`, `booking_rejected`, `booking_cancelled`, `booking_cancelled_by_traveler`, `booking_cancelled_by_provider`, `booking_completed` |
| `payment` | `demo_succeeded`, `demo_failed`, `unpaid` for Pay at Location |
| `community` | `post_liked`, `post_commented` |
| `account` | `password_changed`, `password_reset` |

Events use the same transactions as booking, payment, and Community changes. Booking requests made through AI use the shared booking services. Community self-actions are ignored. Repeated like/unlike cycles produce at most one notification per post and liker. Hidden or deleted posts and comments are excluded from feed and unread counts. Notifications are generated prospectively after migration.

PATCH notification settings with any subset of the following boolean fields. GET and PATCH both return the full settings. Defaults are:

```json
{"booking_updates":true,"payment_updates":true,"community_activity":true,"promotional_updates":false}
```

These settings affect future in-app events. Account security events are always enabled. No promotional events are generated. Booking and payment records remain available in My Bookings.

### Errors

```json
{"detail":"Notification not found"}
```

```json
{"detail":[{"loc":["body","budget_max"],"msg":"Input should be a valid integer","type":"int_type"}]}
```

Responses include 400 for invalid password operations, 401 for missing or invalid authentication, 403 for forbidden roles, 404 for inaccessible items, 413 for oversized images, 415 for unsupported images, 422 for invalid input, 429 with Retry-After for rate limits, and 503 for required schema or media storage unavailable. Until 1.0.7 is applied, GET /auth/me and existing booking, payment and Community operations remain usable. New account endpoints return 503.

## Database deployment

Prepared artifact `scripts/mvp_upgrades/1.0.7_traveler_accounts.sql`. SHA-256 `48398527e73181345d82c577c4ece7a61b2f30bd3ab495702bc0aba54b9b6ea7`. The migration adds two tables and expects 29 tables based on the previous verified count of 27. No existing columns are altered and no existing rows are changed by the DDL.

- `traveler_settings` adds `user_id` as primary key and user FK, `country`, `avatar`, `email_verified`, `google_only`, `preferences`, `booking_updates`, `payment_updates`, `community_activity`, and `promotional_updates`. It checks that preferences contain JSON and uses safe BIT and JSON defaults.
- `traveler_notifications` adds identity `id`, user FK `recipient_user_id`, `notification_type`, `category`, `title`, `message`, `related_entity_type`, `related_entity_id`, `source_comment_id`, `is_read`, `created_at`, `read_at`, and unique `event_key`. Checks enforce valid navigation and read state. Recipient/date and recipient/unread/date indexes support the feed.

The following procedure requires explicit approval. Pause application writes, verify the exact server and DB, verify that only 1.0.7 is pending, and fingerprint relevant existing tables. Use a unique server-writable local path for the backup and require SQL Server verification with checksums before upgrading.

```sql
BACKUP DATABASE [CeylonGoDB]
TO DISK = N'C:\Program Files\Microsoft SQL Server\MSSQL17.SQLEXPRESS\MSSQL\Backup\CeylonGoDB_before_mvp_1_0_7_<utc>.bak'
WITH COPY_ONLY, CHECKSUM;
RESTORE VERIFYONLY
FROM DISK = N'C:\Program Files\Microsoft SQL Server\MSSQL17.SQLEXPRESS\MSSQL\Backup\CeylonGoDB_before_mvp_1_0_7_<utc>.bak'
WITH CHECKSUM;
```

```powershell
.\.venv\Scripts\python.exe scripts/bootstrap_mvp_schema.py CeylonGoDB upgrade --server 'lpc:.\SQLEXPRESS' --target 1.0.7 --dry-run
.\.venv\Scripts\python.exe scripts/bootstrap_mvp_schema.py CeylonGoDB upgrade --server 'lpc:.\SQLEXPRESS' --target 1.0.7 --yes
.\.venv\Scripts\python.exe scripts/bootstrap_mvp_schema.py CeylonGoDB status --server 'lpc:.\SQLEXPRESS'
```

The runner verifies the local alias against the exact host and uses a transactional migration lock. Failure before commit rolls back both the DDL and ledger insert. After commit, verify the two tables, indexes and prior data fingerprints. If application rollback is needed, redeploy the prior application while leaving the additive tables in place. Restoring the verified backup would replace database state and requires a separately approved recovery plan.

