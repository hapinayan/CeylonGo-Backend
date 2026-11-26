# Artisan and Local Business API integration contract

Use this contract for the Angular traveler discovery flow and the provider and admin screens. The Academic MVP serves these routes under `/api/v1` and `/api`. Paths below omit the prefix. Protected requests require `Authorization: Bearer <access_token>` from the existing MVP authentication flow. The runtime OpenAPI schema at `/openapi.json` provides the full field definitions.

The shared listing category is `artisan`. Artisan listings are non-bookable showcase profiles—there is no cart, checkout, order, reservation, or payment flow. Calling `POST /bookings`, `POST /listings/{listing_id}/slots`, or `POST /listings/{listing_id}/units` on an `artisan` listing returns `422`.

## Public Artisan discovery and business details

| Method and path | Authorization | Parameters and response |
| --- | --- | --- |
| `GET /artisan-listings` | Public | Optional `search` (max 150; matches title, description, district, locality, or craft category), `category` (craft category), `district`, `business_type`, `min_price`, `max_price`, `sort_by`, `page` (default 1), `page_size` (default 20, max 100), `is_featured`. Returns `{items, total, page, page_size, total_pages}`. |
| `GET /artisan-listings/categories` | Public | Returns sorted `string[]` of distinct craft categories across published artisan listings. |
| `GET /artisan-listings/districts` | Public | Returns sorted `string[]` of distinct districts across published artisan listings. |
| `GET /artisan-listings/{listing_id}` | Public | Returns full `ArtisanDetails` for a published artisan listing (`404` otherwise). |
| `GET /artisan-listings/{listing_id}/business-hours` | Public | Returns `OpeningDay[]` sorted by `day_of_week` (`0..6`). |
| `GET /listings/{listing_id}/photos/{photo_id}` | Public | Returns image bytes when the listing is published and the photo is visible. Follow URLs in `cover_image_url` and `images[].image_url`. |

`sort_by` accepts `newest` (default, listing ID descending), `title` (ascending), `price_asc`, and `price_desc`. Text filters (`category`, `district`, `business_type`) are case-insensitive exact matches after trimming. Supplying `min_price` or `max_price` restricts results to listings with `price > 0`; `min_price > max_price` returns `422`. `is_featured=true` returns an empty page (`total: 0`) because Academic MVP does not include sponsored promotion flags.

Public responses require `approval_status == "approved"`, `is_active == true`, and an approved, active provider. Academic MVP does not store reviews or ratings for artisan listings, so `average_rating` is `null` and `total_reviews` is `0`. Seeded Academic MVP listings (`craft-01` through `craft-20`) default `craft_category` to `"Pottery & Ceramics"` and return `null`, `[]`, or `{}` for unconfigured optional contact, social, and hours fields.

Example `GET /artisan-listings/{listing_id}` response:

```json
{
  "craft_category": "Pottery & Ceramics",
  "business_type": "Workshop",
  "business_background": "A clay workshop inspired by local craft.",
  "artisan_information": "Studio maker",
  "phone": "+94771234567",
  "whatsapp_number": "+94771234567",
  "email": "studio@example.com",
  "website_url": "https://example.com/",
  "social_links": {
    "instagram": "https://instagram.com/example"
  },
  "opening_hours": "Mon-Sat 09:00-17:00",
  "business_hours": [
    {
      "day_of_week": 0,
      "is_closed": false,
      "open_time": "09:00:00",
      "close_time": "17:00:00"
    }
  ],
  "id": 121,
  "provider_profile_id": 9,
  "title": "Delft Clay Studio",
  "description": "Small pottery studio producing terracotta cookware.",
  "district": "Jaffna",
  "city": "Delft",
  "address": "12 Harbor Road, Delft",
  "latitude": 9.52,
  "longitude": 79.72,
  "price": 1600.0,
  "currency": "LKR",
  "provider_business_name": "Delft Artisans",
  "cover_image_url": "http://127.0.0.1:8000/api/listings/121/photos/120",
  "whatsapp_link": "https://wa.me/94771234567",
  "is_active": true,
  "is_verified": true,
  "is_featured": false,
  "approval_status": "approved",
  "average_rating": null,
  "total_reviews": 0,
  "images": [
    {
      "id": 120,
      "artisan_listing_id": 121,
      "image_url": "http://127.0.0.1:8000/api/listings/121/photos/120",
      "caption": "Workshop entrance",
      "display_order": 0,
      "is_cover": true,
      "created_at": "2026-11-26T09:46:11.017000"
    }
  ],
  "business_images": [
    {
      "id": 120,
      "artisan_listing_id": 121,
      "image_url": "http://127.0.0.1:8000/api/listings/121/photos/120",
      "caption": "Workshop entrance",
      "display_order": 0,
      "is_cover": true,
      "created_at": "2026-11-26T09:46:11.017000"
    }
  ],
  "created_at": "2026-11-26T09:46:11",
  "updated_at": "2026-11-26T09:46:11"
}
```

`images` and `business_images` contain the same ordered gallery list, with the selected cover photo placed first (`is_cover: true`). `whatsapp_link` is automatically derived from `whatsapp_number` when present.

## Public product showcase and photographs

| Method and path | Authorization | Parameters and response |
| --- | --- | --- |
| `GET /showcase-items` | Public | Optional `search` (matches title, description, or materials), `artisan_listing_id` (>0), `item_type` (`product` or `service`), `category`, `page` (default 1), `page_size` (default 20, max 100). Returns `{items, total, page, page_size, total_pages}`. |
| `GET /artisan-listings/{listing_id}/showcase-items` | Public | Optional `page` (default 1), `page_size` (default 20, max 100). Returns paginated active products for a published artisan listing (`404` if listing is not published). |
| `GET /showcase-items/{product_id}` | Public | Returns `ProductDetails` for an active product under a published artisan listing (`404` otherwise). |
| `GET /showcase-items/{product_id}/images` | Public | Returns ordered `ProductImage[]` for a public product. |
| `GET /showcase-items/{product_id}/images/{photo_id}` | Public | Returns product photo bytes (`X-Content-Type-Options: nosniff`). |

All MVP showcase entries have `item_type: "product"` (`item_type=service` returns an empty page). Product photographs are kept separate from business gallery photos and are never exposed via `/listings/{listing_id}/photos/{photo_id}`.

Example `GET /showcase-items/{product_id}` response:

```json
{
  "title": "Glazed clay cup",
  "category": "Ceramics",
  "description": "Hand-glazed cup in brown clay.",
  "materials": "Clay",
  "price": 1800.0,
  "is_active": true,
  "id": 45,
  "artisan_listing_id": 121,
  "listing_title": "Delft Clay Studio",
  "item_type": "product",
  "currency": "LKR",
  "image_url": "http://127.0.0.1:8000/api/showcase-items/45/images/125",
  "is_available": true,
  "approval_status": "approved",
  "images": [
    {
      "id": 125,
      "showcase_item_id": 45,
      "image_url": "http://127.0.0.1:8000/api/showcase-items/45/images/125",
      "caption": "Side view",
      "display_order": 0,
      "is_primary": true,
      "created_at": "2026-11-26T09:50:00"
    }
  ],
  "product_images": [
    {
      "id": 125,
      "showcase_item_id": 45,
      "image_url": "http://127.0.0.1:8000/api/showcase-items/45/images/125",
      "caption": "Side view",
      "display_order": 0,
      "is_primary": true,
      "created_at": "2026-11-26T09:50:00"
    }
  ],
  "created_at": "2026-11-26T09:48:00",
  "updated_at": "2026-11-26T09:48:00"
}
```

## Traveler wishlist

All wishlist routes require an authenticated user with the `traveler` role (`401` unauthenticated, `403` for provider or admin). Wishlist state is persisted in the shared `homestay_wishlist` table scoped by `category = "artisan"`.

| Method and path | Authorization | Parameters and response |
| --- | --- | --- |
| `GET /artisan-wishlists/me` | Traveler | Optional `page` (default 1), `page_size` (default 20, max 100). Returns `Page[WishlistItem]` ordered by listing ID descending. |
| `GET /artisan-listings/{listing_id}/wishlist/check` | Traveler | Returns `{"listing_id":121,"is_saved":true,"wishlist_id":121}` (or `is_saved: false, wishlist_id: null`). Returns `404` if `listing_id` is missing or not an `artisan` listing. |
| `POST /artisan-listings/{listing_id}/wishlist` | Traveler | Empty body (or `{}`). Saves a published artisan listing idempotently and returns `WishlistItem` with HTTP `201`. Returns `404` if listing is not published. |
| `DELETE /artisan-listings/{listing_id}/wishlist` | Traveler | Removes the listing from the traveler's wishlist (including if the listing was later unpublished). Returns HTTP `204`. |

Each `WishlistItem` has `{id, artisan_listing_id, user_id, is_available, listing}`. When a saved listing is later unpublished, rejected, or suspended, `GET /artisan-wishlists/me` retains the entry with `is_available: false` and `listing: null`.

## Provider business, metadata, gallery, and product showcase management

Every provider route requires an approved `provider` account (`403` otherwise) and enforces ownership on existing listings (`403` for another provider's listing, `404` for a missing or non-artisan listing). Suspended listings cannot be modified (`409`).

### Business listing, metadata, photos, and verification submission

| Method and path | Request and response |
| --- | --- |
| `POST /provider/listings` | Body `{"category":"artisan"}` (optionally including `title`, `description`, `district`, `locality`, `latitude`, `longitude`, `price`). Returns `ReviewListing` envelope with HTTP `201`. |
| `GET /provider/listings?category=artisan` | Returns `ReviewListing[]`. Optional `limit` (default 25, max 100) and `offset`. |
| `GET /provider/listings/{listing_id}` | Returns `ReviewListing` envelope (`details.artisan`, `details.products`, `rooms: []`). |
| `PATCH /provider/listings/{listing_id}` | Partial update of shared listing fields (`title`, `description`, `district`, `locality`, `latitude`, `longitude`, `price`, `address`, `province`, `contact`) and `artisan` metadata. Returns `ReviewListing` envelope. |
| `POST /provider/listings/{listing_id}/photos` | Multipart `file` and optional `alt_text`. Returns media metadata with HTTP `201`. |
| `GET /provider/listings/{listing_id}/photos/{photo_id}` | Authenticated owner preview of business gallery photo (including drafts). |
| `PUT /provider/listings/{listing_id}/photos/{photo_id}/cover` | Empty body. Sets cover photo without invalidating approval. Returns `ReviewListing`. |
| `DELETE /provider/listings/{listing_id}/photos/{photo_id}` | Archives the business gallery photo (`is_visible: false`). HTTP `204`. |
| `PUT /provider/listings/{listing_id}/evidence` | Body `{"links":[{"kind":"website","url":"https://example.com"}],"notes":"..."}`. Returns `ReviewListing`. |
| `POST /provider/listings/{listing_id}/evidence/files` | Multipart `file`, `kind` (`workshop`, `business_certificate`, `customer_feedback`, etc.), and `redacted` (`true` required for `customer_feedback`). Returns `{id, kind, media_type, redacted}` with HTTP `201`. |
| `GET /provider/listings/{listing_id}/evidence/files/{file_id}` | Downloads the private verification file. |
| `DELETE /provider/listings/{listing_id}/evidence/files/{file_id}` | Archives the private verification file. HTTP `204`. |
| `POST /provider/listings/{listing_id}/submit` | Empty body. Moves `draft`, `rejected`, or `more_evidence_required` to `pending`. Returns `ReviewListing`. |
| `POST /provider/listings/{listing_id}/publish` | Empty body. Sets `is_active: true` on an `approved` listing. Returns `ReviewListing`. |
| `POST /provider/listings/{listing_id}/unpublish` | Empty body. Sets `is_active: false`. Returns `ReviewListing`. |

When `artisan` is supplied in `PATCH /provider/listings/{listing_id}`, only the provided fields inside `artisan` are merged into existing business metadata (`exclude_unset=True`), so omitting a field preserves its current value while passing `null` clears it. Supported `artisan` fields (`extra="forbid"`):

- `craft_category`: string (1–100 chars) or `null`
- `business_type`: string (1–100 chars) or `null`
- `business_background`: string (max 2000 chars) or `null`
- `artisan_information`: string (max 2000 chars) or `null`
- `phone`: string matching `^\+?[0-9][0-9 ()-]{6,23}$` or `null`
- `whatsapp_number`: E.164 string matching `^\+[1-9][0-9]{7,14}$` or `null`
- `email`: valid email or `null`
- `website_url`: public HTTP/HTTPS URL (no embedded credentials, max 1000 chars) or `null`
- `social_links`: object with optional keys `facebook`, `instagram`, `tiktok`, `youtube` mapped to public HTTP/HTTPS URLs
- `opening_hours`: display summary string (max 500 chars) or `null`
- `business_hours`: up to 7 unique `OpeningDay` entries (`day_of_week` 0–6, `is_closed` bool, `open_time` and `close_time` as offset-free Sri Lanka local times `HH:MM[:SS]`; `close_time` must be strictly after `open_time` when open; automatically sorted by `day_of_week`).

Example `PATCH /provider/listings/{listing_id}` payload:

```json
{
  "title": "Delft Clay Studio",
  "description": "Small pottery studio producing terracotta cookware.",
  "district": "Jaffna",
  "locality": "Delft",
  "address": "12 Harbor Road, Delft",
  "latitude": "9.520000",
  "longitude": "79.720000",
  "price": "1600.00",
  "artisan": {
    "craft_category": "Pottery & Ceramics",
    "business_type": "Workshop",
    "business_background": "A clay workshop inspired by local craft.",
    "artisan_information": "Studio maker",
    "phone": "+94771234567",
    "whatsapp_number": "+94771234567",
    "email": "studio@example.com",
    "website_url": "https://example.com",
    "social_links": {"instagram": "https://instagram.com/example"},
    "opening_hours": "Mon-Sat 09:00-17:00",
    "business_hours": [
      {"day_of_week": 0, "open_time": "09:00", "close_time": "17:00"}
    ]
  }
}
```

Material edits to listing fields, photos, evidence, or `artisan` metadata on an `approved` or `pending` listing reset `approval_status` to `draft` and `is_active` to `false`, requiring resubmission. Submission requires at least one uploaded business photo, `title` (≥3 chars), `description` (≥10 chars), `district`, `locality`, paired `latitude`/`longitude`, and either a `workshop` / `business_certificate` evidence file or non-empty `notes`. Unlike bookable categories, `price` may be `0`.

### Product showcase CRUD and photographs

| Method and path | Request and response |
| --- | --- |
| `GET /provider/listings/{listing_id}/products` | Optional `page` (default 1), `page_size` (default 20, max 100). Returns `Page[ProductDetails]` including inactive products, with authenticated preview `image_url` values. |
| `GET /provider/listings/{listing_id}/products/{product_id}` | Returns a single owned `ProductDetails` (active or inactive). |
| `POST /provider/listings/{listing_id}/products` | Full `ProductInput` body below. Returns `ProductDetails` with HTTP `201`. |
| `PUT /provider/listings/{listing_id}/products/{product_id}` | Full `ProductInput` body (set `is_active: false` to deactivate or `true` to reactivate). Preserves attached photos. Returns `ProductDetails`. |
| `DELETE /provider/listings/{listing_id}/products/{product_id}` | Deactivates the product (`is_active: false`). HTTP `204`. |
| `POST /provider/listings/{listing_id}/products/{product_id}/images` | Multipart `file` and optional `caption` (max 160). Max 12 photos per product (`409` if reached). Returns `ProductImage` with HTTP `201`. |
| `GET /provider/listings/{listing_id}/products/{product_id}/images/{photo_id}` | Authenticated owner preview of a product photograph (including drafts and inactive products). |
| `DELETE /provider/listings/{listing_id}/products/{product_id}/images/{photo_id}` | Detaches the photo from the product. HTTP `204`. |

Example `ProductInput` payload (`POST` / `PUT`):

```json
{
  "title": "Glazed clay cup",
  "category": "Ceramics",
  "description": "Hand-glazed cup in brown clay.",
  "materials": "Clay",
  "price": "1800.00",
  "is_active": true
}
```

`materials` and `price` are optional (`null` allowed; `price` must be `>= 0` with up to 12 digits and 2 decimals when provided). Product CRUD and product photo operations preserve the parent listing's approval state.

## Admin verification

Shared admin routes require the `admin` role (`403` otherwise):

| Method and path | Authorization | Request and response |
| --- | --- | --- |
| `GET /admin/listings?category=artisan` | Admin | Review envelope array. Optional `status` (`draft`, `pending`, `more_evidence_required`, `approved`, `rejected`, `suspended`; default `pending`), `limit` (default 25, max 100), `offset`. |
| `GET /admin/listings/{listing_id}` | Admin | `AdminReviewListing` with `provider: {id, business_name, email, phone, verification_status}`, `details.artisan`, `details.products`, `details.files`, and `rooms: []`. |
| `GET /admin/listings/{listing_id}/photos/{photo_id}` | Admin | Authenticated business photo preview. |
| `GET /admin/listings/{listing_id}/evidence/files/{file_id}` | Admin | Private verification evidence file download (file IDs from `details.files`). |
| `POST /admin/listings/{listing_id}/decision` | Admin | Body `{"approval_status":"approved"}` or `{"approval_status":"more_evidence_required","decision_reason":"..."}`. Returns the shared `ListingResponse`. |
| `GET /admin/listings/{listing_id}/history` | Admin | Array of `{actor_id, reason, occurred_at, decision}`, newest first. Optional `limit` and `offset`. |

API status values are `draft`, `pending`, `more_evidence_required`, `rejected`, `approved`, and `suspended`. Admin decisions accept `approved`, `rejected`, `more_evidence_required`, and `suspended` (`decision_reason` is required for all decisions except `approved`). Approving an `artisan` listing sets `is_active: true` automatically; suspending or rejecting hides both the artisan listing and all of its showcase products from public discovery and marks saved traveler wishlist items as `is_available: false, listing: null`.

## Error handling and deployment

FastAPI errors return `{"detail":"message"}` for business/workflow errors and `{"detail":[...]}` (`422`) for schema validation errors. Handle `401` for missing or invalid authentication, `403` for role or ownership denial, `404` for unpublished or missing resources, `409` for suspended edits, workflow conflicts, or photo limit reached, `422` for validation errors or unsupported booking/slot/unit requests on `artisan` listings, and `503` if MVP schema `1.0.3` / `1.0.4` is unavailable.

This implementation reuses the unified Academic MVP tables (`listings`, `listing_verification`, `listing_media`, `inventory_units`, `homestay_wishlist`, `admin_audit_logs`) with MVP schema versions `1.0.3` and `1.0.4`. No new database migration is required.
