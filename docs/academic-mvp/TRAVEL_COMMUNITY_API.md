# Travel Community & Social Feed API Integration Contract

This contract documents the Travel Community & Social Feed endpoints exposed by the active Academic MVP runtime (`app/main_mvp.py`) under both `/api/v1` and `/api`. Paths below omit the prefix. Protected endpoints require `Authorization: Bearer <access_token>` obtained from the MVP authentication endpoints. Full OpenAPI schemas are served at `/openapi.json`.

## Authorization and Identity Rules

- **Public reads**: Feed discovery, single post details, post photos, and post comments are publicly readable for `visibility == "visible"` content authored by active accounts. Supplying an optional valid bearer token populates `viewer_has_liked` and `viewer_has_saved` and allows authors or admins to view non-deleted hidden posts. Passing an invalid, expired, or revoked bearer token returns `401 Unauthorized`.
- **Authenticated writes**: Active `traveler` accounts, approved active `provider` accounts, and `admin` accounts may create posts, comment, like, bookmark, and report inappropriate content. Providers whose verification status is not `approved` receive `403 Forbidden`.
- **Ownership**: Only the post/comment author may edit their content (`403` otherwise); authors cannot edit posts or comments hidden by an administrator.
- **Admin moderation**: Admin endpoints require `role == "admin"` (`403` otherwise) and record immutable audit entries in `admin_audit_logs` (`action = "post_moderation"`).

## Schema Extension Status (`1.0.5_community_extensions.sql`)

- Core community operations (feed discovery, pagination, latest sorting, author/provider identity, create/edit/delete posts, like/unlike, add/edit/delete/list comments, and admin post/comment moderation) run directly against existing Academic MVP schema tables (`community_posts`, `community_comments`, `community_post_likes`, `admin_audit_logs`, `users`, `provider_profiles`).
- Post photographs (`community_post_media`), saved posts (`community_saved_posts`), and moderation reports (`community_reports`) are prepared in `scripts/mvp_upgrades/1.0.5_community_extensions.sql`. When `1.0.5` is not yet applied to SQL Server (`CeylonGoDB` remains at `1.0.4`), feed reads return `media: []` and `viewer_has_saved: false`, while extension write/report endpoints return `503 Service Unavailable`.

---

## 1. Community Feed & Post Discovery

| Method and path | Authorization | Query params / behavior |
| --- | --- | --- |
| `GET /community/posts` | Public (optional JWT) | Optional `search` (max 150 chars, case-insensitive body substring), `author_id` (≥1), `author_type` (`traveler` \| `provider`), `page` (default `1`), `page_size` (default `20`, max `50`). Sorted latest first (`created_at DESC, id DESC`). Returns `CommunityPage[CommunityPostDetails]`. |
| `GET /community/feed` | Public (optional JWT) | Alias for `GET /community/posts`. |
| `GET /community/posts/me` | Authenticated | Paginated non-deleted posts (`visible` + `hidden`) authored by the caller (`page`, `page_size`). |
| `GET /community/posts/{post_id}` | Public (optional JWT) | Returns `CommunityPostDetails`. Hidden posts are only returned to the author or an admin (`404` otherwise). |

### Example `CommunityPostDetails`

```json
{
  "id": 21,
  "author_id": 14,
  "author_name": "[DEMO] Traveler 01",
  "author_type": "traveler",
  "author": {
    "id": 14,
    "full_name": "[DEMO] Traveler 01",
    "author_type": "traveler",
    "provider_profile_id": null,
    "provider_business_name": null,
    "provider_district": null,
    "is_verified_provider": false,
    "member_since": "2026-11-26T09:46:11"
  },
  "body": "Planning a sample visit to Jaffna.",
  "visibility": "visible",
  "created_at": "2026-11-26T09:46:11.099000",
  "updated_at": "2026-11-26T09:46:11.099000",
  "likes_count": 3,
  "comments_count": 2,
  "viewer_has_liked": false,
  "viewer_has_saved": false,
  "media": [
    {
      "id": 1,
      "post_id": 21,
      "media_type": "image",
      "image_url": "/api/v1/community/posts/21/media/1",
      "alt_text": "Jaffna Fort gate",
      "display_order": 0,
      "created_at": "2026-11-26T10:00:00"
    }
  ]
}
```

### Paginated Envelope (`CommunityPage[T]`)

```json
{
  "items": [],
  "total": 21,
  "page": 1,
  "page_size": 20,
  "total_pages": 2
}
```

---

## 2. Post Management & Photo Uploads

| Method and path | Authorization | Request body & status |
| --- | --- | --- |
| `POST /community/posts` | Traveler / Approved Provider / Admin | JSON `{"body": "..."}` (`1..2000` chars; HTML/script tags stripped). Returns `CommunityPostDetails` with HTTP `201`. |
| `PATCH /community/posts/{post_id}` | Post Author | JSON `{"body": "..."}` (`1..2000` chars). `403` if non-owner or post was hidden by admin. |
| `PUT /community/posts/{post_id}` | Post Author | Alias for `PATCH /community/posts/{post_id}`. |
| `DELETE /community/posts/{post_id}` | Post Author or Admin | Soft-deletes post (`visibility = "deleted"`). Returns `{"deleted": true, "post_id": 21, "visibility": "deleted"}`. Author receives `403` on admin-hidden posts. |
| `POST /community/posts/{post_id}/media` | Post Author (`1.0.5`) | Multipart `file` (`JPEG`/`PNG`/`WebP`, max `5 MB`, max `6` photos per post) and optional `alt_text` (max `160` chars). Validates magic bytes, resolution bounds, and re-encodes via Pillow with EXIF metadata stripped. Returns `CommunityPostMediaItem` (`201`). |
| `GET /community/posts/{post_id}/media/{media_id}` | Public (optional JWT, `1.0.5`) | Streams image bytes with `X-Content-Type-Options: nosniff`. Hidden post photos are restricted to author/admin (`404` otherwise). |
| `DELETE /community/posts/{post_id}/media/{media_id}` | Post Author or Admin (`1.0.5`) | Hides photo (`is_visible = false`). Returns `{"deleted": true, "media_id": 1, "post_id": 21}`. |

---

## 3. Social Interactions (Likes, Comments & Saved Posts)

| Method and path | Authorization | Request & response |
| --- | --- | --- |
| `POST /community/posts/{post_id}/like` | Traveler / Approved Provider / Admin | Toggles like state and returns `{"post_id": 21, "liked": true, "likes_count": 1}`. |
| `PUT /community/posts/{post_id}/like` | Traveler / Approved Provider / Admin | Idempotent like (prevents duplicate like rows). Returns `{"post_id": 21, "liked": true, "likes_count": 1}`. |
| `DELETE /community/posts/{post_id}/like` | Traveler / Approved Provider / Admin | Idempotent unlike. Returns `{"post_id": 21, "liked": false, "likes_count": 0}`. |
| `GET /community/posts/{post_id}/comments` | Public (optional JWT) | Paginated comments (`page`, `page_size`) ordered oldest-first (`created_at ASC, id ASC`). Returns `CommunityPage[CommunityCommentDetails]`. |
| `POST /community/posts/{post_id}/comments` | Traveler / Approved Provider / Admin | JSON `{"body": "..."}` (`1..1000` chars). Returns `CommunityCommentDetails` with HTTP `201`. |
| `PATCH /community/comments/{comment_id}` | Comment Author | JSON `{"body": "..."}` (`1..1000` chars). `403` for non-owner or admin-hidden comment. |
| `DELETE /community/comments/{comment_id}` | Comment Author or Admin | Marks comment `visibility = "deleted"`. Returns `{"deleted": true, "comment_id": 5, "visibility": "deleted"}`. |
| `PUT /community/posts/{post_id}/save` | Authenticated (`1.0.5`) | Idempotently bookmarks a visible post. Returns `{"post_id": 21, "saved": true}`. |
| `DELETE /community/posts/{post_id}/save` | Authenticated (`1.0.5`) | Removes bookmark. Returns `{"post_id": 21, "saved": false}`. |
| `GET /community/saved` | Authenticated (`1.0.5`) | Returns `CommunityPage[CommunityPostDetails]` for visible bookmarked posts (`page`, `page_size`). |

---

## 4. Reporting & Admin Moderation

| Method and path | Authorization | Request & response |
| --- | --- | --- |
| `POST /community/reports` | Authenticated (`1.0.5`) | JSON `{"entity_type": "post" \| "comment", "entity_id": 21, "reason": "Spam link", "description": "..."}`. Self-reports return `422`; duplicate pending report by the same user returns `409`. Returns `CommunityReportDetails` (`201`). |
| `GET /admin/community/reports` | Admin (`1.0.5`) | Optional `status` (`pending` \| `reviewed` \| `resolved` \| `dismissed`), `entity_type` (`post` \| `comment`), `page`, `page_size`. Returns `CommunityPage[CommunityReportDetails]`. |
| `PATCH /admin/community/reports/{report_id}` | Admin (`1.0.5`) | JSON `{"status": "reviewed" \| "resolved" \| "dismissed", "review_notes": "...", "action_visibility": "visible" \| "hidden" \| "deleted"}`. Updates report and optionally moderates target post/comment with an audit log entry. |
| `POST /admin/community/posts/{post_id}/moderate` | Admin | JSON `{"visibility": "visible" \| "hidden" \| "deleted", "reason": "..."}`. Updates post visibility and writes `admin_audit_logs`. |
| `PATCH /admin/community/posts/{post_id}` | Admin | JSON `{"visibility": "visible" \| "hidden" \| "deleted", "reason": "..."}`. RESTful alias for post visibility moderation + audit log. |
| `PATCH /admin/community/comments/{comment_id}` | Admin | JSON `{"visibility": "visible" \| "hidden" \| "deleted", "reason": "..."}`. Moderates comment visibility and logs `post_moderation`. |
| `DELETE /admin/community/comments/{comment_id}` | Admin | Optional query `reason` (default `"Removed by moderator"`). Marks comment `deleted` and logs `post_moderation`. |

---

## Standard Error Responses

| Status | Meaning |
| --- | --- |
| `400 Bad Request` | Empty, oversized (`>5 MB`), corrupted, or unsupported photo upload format. |
| `401 Unauthorized` | Missing, invalid, expired, or invalidated bearer token; inactive user. |
| `403 Forbidden` | Non-owner modification, unapproved provider write, admin-required action, or attempt to edit an admin-hidden post/comment. |
| `404 Not Found` | Missing, deleted, or hidden post/comment/photo/report. |
| `409 Conflict` | Duplicate pending user report or >6 active photos on a post. |
| `422 Unprocessable Entity` | Validation error (empty body after HTML stripping, self-report, out-of-range pagination). |
| `503 Service Unavailable` | Schema upgrade `1.0.5_community_extensions.sql` has not yet been applied to SQL Server (`CeylonGoDB`). |
