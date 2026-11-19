# CeylonGo Homestay Module — Complete API Endpoint Inventory

**Repository:** `CeylonGo-Backend`  
**Base URL:** `/api/v1`  
**Authentication Scheme:** HTTP Bearer JSON Web Token (`Authorization: Bearer <token>`)  
**Architecture:** FastAPI, Pydantic v2, SQLAlchemy 2.0, Microsoft SQL Server  
**Total System Paths:** 179  
**Total System HTTP Operations:** 213  
**Homestay-Scoped HTTP Operations:** 76  
- **Public & Webhook Operations:** 6  
- **Traveler Operations:** 17  
- **Service Provider Operations:** 35  
- **Platform Administrator Operations:** 18  

---

## Table of Contents
1. [Overview & Authentication Roles](#1-overview--authentication-roles)
2. [Public & Webhook Operations (6 Operations)](#2-public--webhook-operations-6-operations)
3. [Traveler Operations (17 Operations)](#3-traveler-operations-17-operations)
4. [Service Provider Operations (35 Operations)](#4-service-provider-operations-35-operations)
5. [Administrator Operations (18 Operations)](#5-administrator-operations-18-operations)
6. [Cross-Reference Summary Table](#6-cross-reference-summary-table)

---

## 1. Overview & Authentication Roles

The CeylonGo Homestay module adheres strictly to Role-Based Access Control (RBAC) enforced via FastAPI dependency injection (`get_current_user`, `require_role`, `get_current_provider`, `get_current_admin`).

| Role ID | Role Name | Description & Capabilities |
| :--- | :--- | :--- |
| `public` | Anonymous Guest | Can discover approved homestays, preview seasonal pricing, check live calendar availability, read verified reviews, and receive external payment gateway IPN callbacks. |
| `traveler` | Authenticated Traveler | Can reserve homestays, preview promotional discounts, initiate PayHere online checkout, monitor payment expiration countdowns, cancel pending/confirmed bookings, request refunds, submit verified post-stay reviews, and manage personal wishlists. |
| `provider` | Service Provider (Host) | Can create, update, and manage homestay listings, upload/reorder photos, configure maintenance blackout dates, accept/reject booking requests, record on-site cash payments, configure listing discounts, launch sponsored campaigns, and track earnings and settlement disbursements. |
| `admin` | Platform Administrator | Can review, verify, approve, reject, or suspend homestay listings, moderate reviews, govern advertising packages, approve sponsored campaigns, process traveler refund requests, disburse provider earnings settlements, and audit platform commission revenues. |

---

## 2. Public & Webhook Operations (6 Operations)

### `EP-PUB-01`: Public Homestay Discovery & Search
- **HTTP Method:** `GET`
- **Path:** `/api/v1/homestays`
- **Handler:** `app.routers.homestay.list_homestays`
- **Auth Required:** Optional / Public
- **Query Parameters:**
  - `query` (str, optional): Search keyword matching title, city, or address.
  - `city` (str, optional): Filter by city/town.
  - `min_price` (Decimal, optional): Minimum base nightly rate (LKR).
  - `max_price` (Decimal, optional): Maximum base nightly rate (LKR).
  - `guests` (int, optional): Minimum required guest capacity.
  - `bedrooms` (int, optional): Minimum bedroom count.
  - `check_in` (date, optional): Check-in date for availability filtering.
  - `check_out` (date, optional): Check-out date for availability filtering.
  - `amenities` (List[str], optional): Required amenity identifiers.
  - `is_verified` (bool, optional): Filter by platform verification badge.
  - `page` (int, default=1): Pagination page number.
  - `page_size` (int, default=10, max=50): Number of records per page.
- **Request Body:** None
- **Response Model:** `HomestayListResponse` (Page items, total count, pagination metadata).
- **HTTP Status Codes:** `200 OK`, `422 Unprocessable Entity`
- **Database Tables Accessed:** `dbo.homestays`, `dbo.homestay_images`, `dbo.homestay_amenities`, `dbo.homestay_bookings`, `dbo.homestay_availability_blocks`, `dbo.promotion_campaigns`

### `EP-PUB-02`: Public Homestay Details View
- **HTTP Method:** `GET`
- **Path:** `/api/v1/homestays/{homestay_id}`
- **Handler:** `app.routers.homestay.get_homestay`
- **Auth Required:** Optional / Public (Admin or owner can view unapproved listings; public can only view `is_approved=1` and `is_active=1`).
- **Path Parameters:**
  - `homestay_id` (int): Unique identifier of the homestay.
- **Request Body:** None
- **Response Model:** `HomestayDetailResponse` (Full property profile, host summary, verified badge, cancellation policy, photo gallery, amenities, rating aggregates).
- **HTTP Status Codes:** `200 OK`, `404 Not Found`, `422 Unprocessable Entity`
- **Database Tables Accessed:** `dbo.homestays`, `dbo.users`, `dbo.provider_profiles`, `dbo.homestay_images`, `dbo.homestay_amenities`, `dbo.reviews`

### `EP-PUB-03`: Check Live Listing Availability Calendar
- **HTTP Method:** `GET`
- **Path:** `/api/v1/homestays/{homestay_id}/availability`
- **Handler:** `app.routers.homestay.check_homestay_availability`
- **Auth Required:** None (Public)
- **Path Parameters:**
  - `homestay_id` (int): Unique identifier of the homestay.
- **Query Parameters:**
  - `check_in` (date): Proposed arrival date (YYYY-MM-DD).
  - `check_out` (date): Proposed departure date (YYYY-MM-DD).
- **Request Body:** None
- **Response Model:** `AvailabilityCheckResponse` (`is_available`: bool, `conflicts`: List[DateConflict], `nights`: int).
- **HTTP Status Codes:** `200 OK`, `400 Bad Request` (Invalid date sequence), `404 Not Found`
- **Database Tables Accessed:** `dbo.homestays`, `dbo.homestay_bookings`, `dbo.homestay_availability_blocks`

### `EP-PUB-04`: Server-Authoritative Price Preview
- **HTTP Method:** `GET`
- **Path:** `/api/v1/homestays/{homestay_id}/price-preview`
- **Handler:** `app.routers.homestay.preview_homestay_price`
- **Auth Required:** None (Public)
- **Path Parameters:**
  - `homestay_id` (int): Unique identifier of the homestay.
- **Query Parameters:**
  - `check_in` (date): Check-in date.
  - `check_out` (date): Check-out date.
  - `guests` (int, default=1): Number of guests.
- **Request Body:** None
- **Response Model:** `PricePreviewResponse` (Breakdown: `nights`, `base_price_per_night`, `subtotal`, `service_fee`, `tax_amount`, `discount_amount`, `discount_percent`, `discount_type`, `promotion_title`, `total_price`).
- **HTTP Status Codes:** `200 OK`, `400 Bad Request`, `404 Not Found`
- **Database Tables Accessed:** `dbo.homestays`, `dbo.homestay_promotions`

### `EP-PUB-05`: Public Verified Homestay Reviews & Ratings
- **HTTP Method:** `GET`
- **Path:** `/api/v1/homestays/{homestay_id}/reviews`
- **Handler:** `app.routers.homestay_reviews.list_homestay_reviews`
- **Auth Required:** None (Public)
- **Path Parameters:**
  - `homestay_id` (int): Unique identifier of the homestay.
- **Query Parameters:**
  - `page` (int, default=1): Page number.
  - `page_size` (int, default=10, max=50): Page size.
- **Request Body:** None
- **Response Model:** `ReviewListResponse` (`reviews`: List[ReviewItem], `average_rating`: float, `total_reviews`: int, `rating_breakdown`: Dict[int, int]).
- **HTTP Status Codes:** `200 OK`, `404 Not Found`
- **Database Tables Accessed:** `dbo.reviews`, `dbo.users`, `dbo.homestays`

### `EP-PUB-06`: PayHere Instant Payment Notification (IPN) Webhook Listener
- **HTTP Method:** `POST`
- **Path:** `/api/v1/payments/payhere/notify`
- **Handler:** `app.routers.payments.payhere_webhook_listener`
- **Auth Required:** Webhook Signature Verification (MD5 hash: `merchant_id + order_id + payhere_amount + payhere_currency + status_code + md5_secret`).
- **Headers:** `Content-Type: application/x-www-form-urlencoded`
- **Request Body (Form Data):**
  - `merchant_id` (str): Configured CeylonGo PayHere Merchant ID.
  - `order_id` (str): System order reference (`BOOKING-<ref_id>` or `CAMP-<camp_id>`).
  - `payment_id` (str): PayHere unique gateway transaction ID.
  - `payhere_amount` (str): Charged currency amount.
  - `payhere_currency` (str): Currency ISO (`LKR`).
  - `status_code` (str): Gateway execution code (`2`=Success, `0`=Pending, `-1`=Canceled, `-2`=Failed, `-3`=Chargedback).
  - `md5sig` (str): Checksum signature.
  - `custom_1` (str, optional): Internal tracking context.
  - `custom_2` (str, optional): Internal tracking context.
- **Response Model:** Plain text `200 OK`
- **HTTP Status Codes:** `200 OK` (Always returned to gateway to acknowledge receipt), `400 Bad Request` (Tampered signature).
- **Concurrency & Idempotency:** Acquires transaction-scoped lock `sp_getapplock` on `homestay_booking_{homestay_id}`. Rejects duplicate callbacks via status guard.
- **Database Tables Accessed:** `dbo.booking_references`, `dbo.homestay_bookings`, `dbo.payment_transactions`, `dbo.booking_status_history`, `dbo.advertising_payments`, `dbo.promotion_campaigns`, `dbo.promotion_events`

---

## 3. Traveler Operations (17 Operations)

### `EP-TRV-01`: Authorized Host Contact Details View
- **HTTP Method:** `GET`
- **Path:** `/api/v1/homestays/{homestay_id}/contact`
- **Handler:** `app.routers.homestay.get_homestay_provider_contact`
- **Auth Required:** Bearer Token (`traveler` with confirmed booking, or listing owner, or `admin`).
- **Path Parameters:**
  - `homestay_id` (int): Homestay listing identifier.
- **Request Body:** None
- **Response Model:** `HostContactResponse` (`host_name`: str, `business_email`: str, `phone_number`: str, `emergency_phone`: Optional[str], `check_in_instructions`: Optional[str]).
- **HTTP Status Codes:** `200 OK`, `403 Forbidden`, `404 Not Found`
- **Database Tables Accessed:** `dbo.homestays`, `dbo.users`, `dbo.provider_profiles`, `dbo.homestay_bookings`

### `EP-TRV-02`: Create Homestay Booking Request
- **HTTP Method:** `POST`
- **Path:** `/api/v1/bookings`
- **Handler:** `app.routers.booking.create_booking`
- **Auth Required:** Bearer Token (`traveler`).
- **Request Body (`CreateBookingRequest`):**
  - `homestay_id` (int): Target homestay listing identifier.
  - `check_in` (date): Check-in date.
  - `check_out` (date): Check-out date.
  - `guests` (int): Number of adult & child guests.
  - `special_requests` (Optional[str], max 500 chars): Traveler notes.
  - `payment_method` (str): `"online"` (PayHere gateway) or `"pay_at_location"` (Cash on site).
  - `attribution_token` (Optional[str]): Cryptographically signed advertising attribution tracking token.
- **Response Model:** `BookingResponse` (Booking details, reference code, status `pending`, price snapshot, cancellation terms).
- **HTTP Status Codes:** `201 Created`, `400 Bad Request`, `404 Not Found`, `409 Conflict` (Dates already booked)
- **Concurrency Guard:** Locks `homestay_booking_{homestay_id}` during validation and insert.
- **Database Tables Accessed:** `dbo.homestays`, `dbo.homestay_bookings`, `dbo.booking_references`, `dbo.booking_status_history`, `dbo.homestay_promotions`, `dbo.promotion_campaigns`

### `EP-TRV-03`: List Traveler's Booking History
- **HTTP Method:** `GET`
- **Path:** `/api/v1/bookings/me`
- **Handler:** `app.routers.booking.get_my_bookings`
- **Auth Required:** Bearer Token (`traveler`).
- **Query Parameters:**
  - `status` (Optional[str]): Filter by status (`pending`, `awaiting_payment`, `confirmed`, `completed`, `cancelled`, `expired`).
  - `page` (int, default=1): Page number.
  - `page_size` (int, default=10): Records per page.
- **Request Body:** None
- **Response Model:** `BookingListResponse` (List of user's bookings with homestay thumbnail, address, dates, payment status).
- **HTTP Status Codes:** `200 OK`, `401 Unauthorized`
- **Database Tables Accessed:** `dbo.homestay_bookings`, `dbo.booking_references`, `dbo.homestays`, `dbo.homestay_images`

### `EP-TRV-04`: Get Single Booking Details
- **HTTP Method:** `GET`
- **Path:** `/api/v1/bookings/{booking_id}`
- **Handler:** `app.routers.booking.get_booking`
- **Auth Required:** Bearer Token (Traveler who booked, listing host, or `admin`).
- **Path Parameters:**
  - `booking_id` (int): Internal booking identifier.
- **Request Body:** None
- **Response Model:** `BookingDetailResponse` (Full financial breakdown, `payment_expires_at`, check-in instructions, cancellation rules, review eligibility).
- **HTTP Status Codes:** `200 OK`, `403 Forbidden`, `404 Not Found`
- **Database Tables Accessed:** `dbo.homestay_bookings`, `dbo.booking_references`, `dbo.homestays`, `dbo.payment_transactions`, `dbo.booking_status_history`

### `EP-TRV-05`: Traveler Cancel Booking Request or Confirmation
- **HTTP Method:** `POST`
- **Path:** `/api/v1/bookings/{booking_id}/cancel`
- **Handler:** `app.routers.booking.cancel_booking`
- **Auth Required:** Bearer Token (`traveler` owner or listing `provider`).
- **Path Parameters:**
  - `booking_id` (int): Booking identifier.
- **Request Body (`CancelBookingRequest`):**
  - `reason` (str, min 5 chars, max 500 chars): Reason for cancellation.
- **Response Model:** `CancelBookingResponse` (`booking_id`, `status`: `"cancelled"`, `refund_eligible`: bool, `estimated_refund_amount`: Decimal).
- **HTTP Status Codes:** `200 OK`, `400 Bad Request` (Stay already completed/in-progress), `403 Forbidden`, `404 Not Found`, `409 Conflict`
- **Concurrency Guard:** Locks `homestay_booking_{homestay_id}`.
- **Database Tables Accessed:** `dbo.homestay_bookings`, `dbo.booking_references`, `dbo.booking_status_history`, `dbo.payment_transactions`

### `EP-TRV-06`: Initiate Online Booking Payment Checkout
- **HTTP Method:** `POST`
- **Path:** `/api/v1/payments/bookings/{booking_id}/initiate`
- **Handler:** `app.routers.payments.initiate_booking_payment`
- **Auth Required:** Bearer Token (`traveler` who booked).
- **Path Parameters:**
  - `booking_id` (int): Booking identifier.
- **Request Body:** None
- **Response Model:** `PayHereCheckoutPayload` (`merchant_id`, `return_url`, `cancel_url`, `notify_url`, `order_id`, `items`, `amount`, `currency`, `hash`, `first_name`, `last_name`, `email`, `phone`, `address`, `city`, `country`, `payment_expires_at`).
- **Special Behavior:** If `total_price == 0` (100% discount promotional booking), immediately transitions booking to `confirmed` without contacting PayHere.
- **HTTP Status Codes:** `200 OK`, `400 Bad Request` (Expired or wrong status), `403 Forbidden`, `404 Not Found`
- **Database Tables Accessed:** `dbo.homestay_bookings`, `dbo.booking_references`, `dbo.users`, `dbo.homestays`, `dbo.payment_transactions`

### `EP-TRV-07`: Query Booking Payment & Gateway Status
- **HTTP Method:** `GET`
- **Path:** `/api/v1/payments/bookings/{booking_id}/status`
- **Handler:** `app.routers.payments.get_booking_payment_status`
- **Auth Required:** Bearer Token (Traveler owner, listing host, or `admin`).
- **Path Parameters:**
  - `booking_id` (int): Booking identifier.
- **Request Body:** None
- **Response Model:** `BookingPaymentStatusResponse` (`booking_id`, `booking_status`, `payment_status`, `total_price`, `currency`, `expires_at`, `remaining_seconds`, `is_expired`, `transactions`: List[TransactionSummary]).
- **HTTP Status Codes:** `200 OK`, `403 Forbidden`, `404 Not Found`
- **Database Tables Accessed:** `dbo.homestay_bookings`, `dbo.booking_references`, `dbo.payment_transactions`

### `EP-TRV-08`: Request Refund for Cancelled Online Booking
- **HTTP Method:** `POST`
- **Path:** `/api/v1/payments/bookings/{booking_id}/request-refund`
- **Handler:** `app.routers.payments.request_booking_refund`
- **Auth Required:** Bearer Token (`traveler` who booked).
- **Path Parameters:**
  - `booking_id` (int): Booking identifier.
- **Request Body (`RefundRequestInput`):**
  - `reason` (str, min 10 chars, max 500 chars): Reason for refund request.
- **Response Model:** `RefundRequestResponse` (`refund_id`, `booking_id`, `status`: `"pending"`, `requested_amount`: Decimal, `created_at`: datetime).
- **HTTP Status Codes:** `201 Created`, `400 Bad Request` (Not cancelled, cash payment, or already requested), `403 Forbidden`, `404 Not Found`
- **Database Tables Accessed:** `dbo.homestay_bookings`, `dbo.payment_transactions`, `dbo.payment_refunds`

### `EP-TRV-09`: Submit Verified Traveler Post-Stay Review
- **HTTP Method:** `POST`
- **Path:** `/api/v1/homestays/{homestay_id}/reviews`
- **Handler:** `app.routers.homestay_reviews.submit_homestay_review`
- **Auth Required:** Bearer Token (`traveler`).
- **Path Parameters:**
  - `homestay_id` (int): Homestay identifier.
- **Request Body (`CreateReviewRequest`):**
  - `booking_id` (int): Booking identifier confirming a completed stay.
  - `rating` (int, min 1, max 5): Cleanliness, comfort, hospitality rating.
  - `comment` (str, min 10, max 2000 chars): Narrative feedback.
- **Response Model:** `ReviewItemResponse` (`id`, `homestay_id`, `author_name`, `rating`, `comment`, `is_published`, `created_at`).
- **HTTP Status Codes:** `201 Created`, `400 Bad Request` (Booking not completed or already reviewed), `403 Forbidden`, `404 Not Found`
- **Database Tables Accessed:** `dbo.reviews`, `dbo.homestay_bookings`, `dbo.homestays`

### `EP-TRV-10`: List Active Public Homestay Discounts
- **HTTP Method:** `GET`
- **Path:** `/api/v1/homestay-promotions`
- **Handler:** `app.routers.promotion.list_active_promotions`
- **Auth Required:** Optional / Public
- **Query Parameters:**
  - `homestay_id` (Optional[int]): Filter promotions for a specific homestay.
- **Request Body:** None
- **Response Model:** List[`PromotionResponse`] (`id`, `homestay_id`, `title`, `discount_type`: `"percentage"|"fixed"`, `discount_percent`, `discount_amount`, `start_date`, `end_date`, `is_active`).
- **HTTP Status Codes:** `200 OK`
- **Database Tables Accessed:** `dbo.homestay_promotions`, `dbo.homestays`

### `EP-TRV-11`: Get Signed Advertising Attribution Token for Listing
- **HTTP Method:** `GET`
- **Path:** `/api/v1/homestays/{homestay_id}/attribution-token`
- **Handler:** `app.routers.homestay_campaigns.get_homestay_active_attribution_token`
- **Auth Required:** Public / Traveler
- **Path Parameters:**
  - `homestay_id` (int): Homestay listing identifier.
- **Request Body:** None
- **Response Model:** `AttributionTokenResponse` (`campaign_id`: int, `homestay_id`: int, `attribution_token`: str, `expires_at`: datetime).
- **HTTP Status Codes:** `200 OK`, `404 Not Found` (No active sponsored campaign for property)
- **Database Tables Accessed:** `dbo.promotion_campaigns`, `dbo.homestays`

### `EP-TRV-12`: Log Campaign Promotion Interaction Event
- **HTTP Method:** `POST`
- **Path:** `/api/v1/homestays/campaigns/{campaign_id}/events`
- **Handler:** `app.routers.homestay_campaigns.log_promotion_event`
- **Auth Required:** Public / Traveler
- **Path Parameters:**
  - `campaign_id` (int): Active campaign identifier.
- **Request Body (`PromotionEventCreate`):**
  - `event_type` (str): `"impression"`, `"click"`, `"inquiry"`.
- **Response Model:** `PromotionEventResponse` (`id`, `campaign_id`, `event_type`, `created_at`).
- **HTTP Status Codes:** `201 Created`, `400 Bad Request` (Direct `booking` event prohibited; bookings require verified booking flow), `404 Not Found`
- **Database Tables Accessed:** `dbo.promotion_campaigns`, `dbo.promotion_events`

### `EP-TRV-13`: Add Homestay to Traveler Wishlist
- **HTTP Method:** `POST`
- **Path:** `/api/v1/wishlists/homestays/{homestay_id}`
- **Handler:** `app.routers.wishlists.add_homestay_to_wishlist`
- **Auth Required:** Bearer Token (`traveler`).
- **Path Parameters:**
  - `homestay_id` (int): Homestay identifier.
- **Request Body:** None
- **Response Model:** `WishlistResponse` (`user_id`, `homestay_id`, `created_at`, `status`: `"added"`).
- **HTTP Status Codes:** `201 Created`, `404 Not Found`, `409 Conflict` (Already in wishlist)
- **Database Tables Accessed:** `dbo.wishlists`, `dbo.homestays`

### `EP-TRV-14`: Remove Homestay from Traveler Wishlist
- **HTTP Method:** `DELETE`
- **Path:** `/api/v1/wishlists/homestays/{homestay_id}`
- **Handler:** `app.routers.wishlists.remove_homestay_from_wishlist`
- **Auth Required:** Bearer Token (`traveler`).
- **Path Parameters:**
  - `homestay_id` (int): Homestay identifier.
- **Request Body:** None
- **Response Model:** `WishlistResponse` (`user_id`, `homestay_id`, `status`: `"removed"`).
- **HTTP Status Codes:** `200 OK`, `404 Not Found`
- **Database Tables Accessed:** `dbo.wishlists`

### `EP-TRV-15`: List Traveler's Saved Homestays
- **HTTP Method:** `GET`
- **Path:** `/api/v1/wishlists/homestays`
- **Handler:** `app.routers.wishlists.get_my_homestay_wishlist`
- **Auth Required:** Bearer Token (`traveler`).
- **Query Parameters:**
  - `page` (int, default=1): Page number.
  - `page_size` (int, default=10): Page size.
- **Request Body:** None
- **Response Model:** `WishlistListResponse` (`items`: List[HomestaySummary], `total`: int).
- **HTTP Status Codes:** `200 OK`, `401 Unauthorized`
- **Database Tables Accessed:** `dbo.wishlists`, `dbo.homestays`, `dbo.homestay_images`

### `EP-TRV-16`: Check Wishlist Status for Homestay
- **HTTP Method:** `GET`
- **Path:** `/api/v1/wishlists/homestays/{homestay_id}/status`
- **Handler:** `app.routers.wishlists.check_homestay_wishlist_status`
- **Auth Required:** Bearer Token (`traveler`).
- **Path Parameters:**
  - `homestay_id` (int): Homestay identifier.
- **Request Body:** None
- **Response Model:** `WishlistStatusResponse` (`homestay_id`: int, `is_saved`: bool).
- **HTTP Status Codes:** `200 OK`, `404 Not Found`
- **Database Tables Accessed:** `dbo.wishlists`

### `EP-TRV-17`: Discover Active Advertising Sponsorship Packages
- **HTTP Method:** `GET`
- **Path:** `/api/v1/homestays/promotions/packages`
- **Handler:** `app.routers.homestay_campaigns.list_active_packages`
- **Auth Required:** Public / Traveler / Provider
- **Request Body:** None
- **Response Model:** List[`PromotionPackageResponse`] (`id`, `name`, `description`, `price`: Decimal, `duration_days`: int, `features`: List[str], `is_active`: bool).
- **HTTP Status Codes:** `200 OK`
- **Database Tables Accessed:** `dbo.promotion_packages`

---

## 4. Service Provider Operations (35 Operations)

### `EP-PRV-01`: Create New Homestay Listing
- **HTTP Method:** `POST`
- **Path:** `/api/v1/homestays`
- **Handler:** `app.routers.homestay.create_homestay`
- **Auth Required:** Bearer Token (`provider`).
- **Request Body (`CreateHomestayRequest`):**
  - `title` (str, 5–100 chars): Title of listing.
  - `description` (str, 20–2000 chars): Detailed description.
  - `address` (str): Street address.
  - `city` (str): City.
  - `district` (str): District.
  - `latitude` (Optional[Decimal]): Geo coordinate (-90 to 90).
  - `longitude` (Optional[Decimal]): Geo coordinate (-180 to 180).
  - `base_price_per_night` (Decimal > 0): Price in LKR.
  - `cleaning_fee` (Decimal >= 0, default=0): Cleaning fee.
  - `max_guests` (int >= 1): Maximum guest capacity.
  - `bedrooms` (int >= 1): Bedroom count.
  - `beds` (int >= 1): Bed count.
  - `bathrooms` (Decimal >= 0.5): Bathroom count.
  - `cancellation_policy` (str): `"flexible"`, `"moderate"`, `"strict"`.
  - `amenity_ids` (List[int]): Amenity IDs.
- **Response Model:** `HomestayDetailResponse` (Created listing in `is_approved=0` draft status).
- **HTTP Status Codes:** `201 Created`, `400 Bad Request`, `422 Unprocessable Entity`
- **Database Tables Accessed:** `dbo.homestays`, `dbo.homestay_amenities`, `dbo.provider_profiles`

### `EP-PRV-02`: List Host's Managed Homestays
- **HTTP Method:** `GET`
- **Path:** `/api/v1/homestays/mine`
- **Handler:** `app.routers.homestay.list_my_homestays`
- **Auth Required:** Bearer Token (`provider`).
- **Query Parameters:**
  - `page` (int, default=1): Page number.
  - `page_size` (int, default=10): Items per page.
- **Request Body:** None
- **Response Model:** List[`HomestaySummaryResponse`]
- **HTTP Status Codes:** `200 OK`, `401 Unauthorized`
- **Database Tables Accessed:** `dbo.homestays`, `dbo.homestay_images`

### `EP-PRV-03`: Provider Homestay Operations Dashboard
- **HTTP Method:** `GET`
- **Path:** `/api/v1/homestays/provider/dashboard`
- **Handler:** `app.routers.homestay.get_provider_dashboard`
- **Auth Required:** Bearer Token (`provider`).
- **Request Body:** None
- **Response Model:** `ProviderDashboardResponse` (`active_listings_count`, `pending_bookings_count`, `occupancy_rate`, `monthly_earnings`: Decimal, `upcoming_check_ins`: int).
- **HTTP Status Codes:** `200 OK`, `401 Unauthorized`
- **Database Tables Accessed:** `dbo.homestays`, `dbo.homestay_bookings`

### `EP-PRV-04`: Update Homestay Listing Details
- **HTTP Method:** `PUT`
- **Path:** `/api/v1/homestays/{homestay_id}`
- **Handler:** `app.routers.homestay.update_homestay`
- **Auth Required:** Bearer Token (Host owner or `admin`).
- **Path Parameters:**
  - `homestay_id` (int): Homestay listing identifier.
- **Request Body (`UpdateHomestayRequest`):** Partial/full update of property attributes, prices, capacity, rules.
- **Response Model:** `HomestayDetailResponse`
- **HTTP Status Codes:** `200 OK`, `400 Bad Request`, `403 Forbidden`, `404 Not Found`
- **Database Tables Accessed:** `dbo.homestays`, `dbo.homestay_amenities`

### `EP-PRV-05`: Soft-Delete / Deactivate Homestay Listing
- **HTTP Method:** `DELETE`
- **Path:** `/api/v1/homestays/{homestay_id}`
- **Handler:** `app.routers.homestay.delete_homestay`
- **Auth Required:** Bearer Token (Host owner or `admin`).
- **Path Parameters:**
  - `homestay_id` (int): Listing identifier.
- **Request Body:** None
- **Response Model:** `MessageResponse` (`status`: `"success"`, `message`: `"Homestay deactivated"`).
- **HTTP Status Codes:** `200 OK`, `400 Bad Request` (Has active/upcoming bookings), `403 Forbidden`, `404 Not Found`
- **Database Tables Accessed:** `dbo.homestays`, `dbo.homestay_bookings`

### `EP-PRV-06`: Submit Homestay Listing for Admin Approval
- **HTTP Method:** `POST`
- **Path:** `/api/v1/homestays/{homestay_id}/submit-approval`
- **Handler:** `app.routers.homestay.submit_homestay_for_approval`
- **Auth Required:** Bearer Token (Host owner).
- **Path Parameters:**
  - `homestay_id` (int): Listing identifier.
- **Request Body:** None
- **Response Model:** `HomestayDetailResponse` (`approval_status`: `"pending"`).
- **HTTP Status Codes:** `200 OK`, `400 Bad Request` (Missing required photos or amenities), `403 Forbidden`, `404 Not Found`
- **Database Tables Accessed:** `dbo.homestays`, `dbo.homestay_images`

### `EP-PRV-07`: Upload Image to Homestay Gallery
- **HTTP Method:** `POST`
- **Path:** `/api/v1/homestays/{homestay_id}/images`
- **Handler:** `app.routers.homestay.add_homestay_image`
- **Auth Required:** Bearer Token (Host owner).
- **Path Parameters:**
  - `homestay_id` (int): Listing identifier.
- **Request Body (`AddImageRequest`):**
  - `image_url` (str): Validated HTTP/HTTPS image URL.
  - `caption` (Optional[str], max 100 chars): Descriptive caption.
  - `is_primary` (bool, default=False): Whether photo is primary.
- **Response Model:** `HomestayImageResponse` (`id`, `homestay_id`, `image_url`, `caption`, `is_primary`, `display_order`).
- **HTTP Status Codes:** `201 Created`, `400 Bad Request` (Max images exceeded), `403 Forbidden`, `404 Not Found`
- **Database Tables Accessed:** `dbo.homestays`, `dbo.homestay_images`

### `EP-PRV-08`: Delete Image from Homestay Gallery
- **HTTP Method:** `DELETE`
- **Path:** `/api/v1/homestays/{homestay_id}/images/{image_id}`
- **Handler:** `app.routers.homestay.delete_homestay_image`
- **Auth Required:** Bearer Token (Host owner).
- **Path Parameters:**
  - `homestay_id` (int): Listing identifier.
  - `image_id` (int): Image identifier.
- **Request Body:** None
- **Response Model:** `MessageResponse`
- **HTTP Status Codes:** `200 OK`, `400 Bad Request` (Cannot delete only primary image), `403 Forbidden`, `404 Not Found`
- **Database Tables Accessed:** `dbo.homestay_images`

### `EP-PRV-09`: Set Primary Listing Photo
- **HTTP Method:** `PATCH`
- **Path:** `/api/v1/homestays/{homestay_id}/images/{image_id}/set-primary`
- **Handler:** `app.routers.homestay.set_primary_homestay_image`
- **Auth Required:** Bearer Token (Host owner).
- **Path Parameters:**
  - `homestay_id` (int): Listing identifier.
  - `image_id` (int): Target image identifier.
- **Request Body:** None
- **Response Model:** `HomestayImageResponse`
- **HTTP Status Codes:** `200 OK`, `403 Forbidden`, `404 Not Found`
- **Database Tables Accessed:** `dbo.homestay_images`

### `EP-PRV-10`: Reorder Listing Image Gallery
- **HTTP Method:** `PATCH`
- **Path:** `/api/v1/homestays/{homestay_id}/images/reorder`
- **Handler:** `app.routers.homestay.reorder_homestay_images`
- **Auth Required:** Bearer Token (Host owner).
- **Path Parameters:**
  - `homestay_id` (int): Listing identifier.
- **Request Body (`ReorderImagesRequest`):**
  - `image_orders` (List[ImageOrderItem]): List of `{image_id: int, display_order: int}` pairs.
- **Response Model:** List[`HomestayImageResponse`]
- **HTTP Status Codes:** `200 OK`, `400 Bad Request`, `403 Forbidden`, `404 Not Found`
- **Database Tables Accessed:** `dbo.homestay_images`

### `EP-PRV-11`: Create Availability Blackout Block
- **HTTP Method:** `POST`
- **Path:** `/api/v1/homestays/{homestay_id}/availability-blocks`
- **Handler:** `app.routers.homestay.create_availability_block`
- **Auth Required:** Bearer Token (Host owner).
- **Path Parameters:**
  - `homestay_id` (int): Listing identifier.
- **Request Body (`CreateAvailabilityBlockRequest`):**
  - `start_date` (date): Blackout start date.
  - `end_date` (date): Blackout end date.
  - `reason` (Optional[str], max 255 chars): Note (e.g., "Annual Maintenance").
- **Response Model:** `AvailabilityBlockResponse` (`id`, `homestay_id`, `start_date`, `end_date`, `reason`, `created_at`).
- **HTTP Status Codes:** `201 Created`, `400 Bad Request`, `409 Conflict` (Overlaps with confirmed booking)
- **Concurrency Guard:** Locks `homestay_booking_{homestay_id}`.
- **Database Tables Accessed:** `dbo.homestays`, `dbo.homestay_availability_blocks`, `dbo.homestay_bookings`

### `EP-PRV-12`: List Listing Availability Blackout Blocks
- **HTTP Method:** `GET`
- **Path:** `/api/v1/homestays/{homestay_id}/availability-blocks`
- **Handler:** `app.routers.homestay.list_availability_blocks`
- **Auth Required:** Bearer Token (Host owner or `admin`).
- **Path Parameters:**
  - `homestay_id` (int): Listing identifier.
- **Request Body:** None
- **Response Model:** List[`AvailabilityBlockResponse`]
- **HTTP Status Codes:** `200 OK`, `403 Forbidden`, `404 Not Found`
- **Database Tables Accessed:** `dbo.homestay_availability_blocks`

### `EP-PRV-13`: Delete Availability Blackout Block
- **HTTP Method:** `DELETE`
- **Path:** `/api/v1/homestays/{homestay_id}/availability-blocks/{block_id}`
- **Handler:** `app.routers.homestay.delete_availability_block`
- **Auth Required:** Bearer Token (Host owner).
- **Path Parameters:**
  - `homestay_id` (int): Listing identifier.
  - `block_id` (int): Blackout block identifier.
- **Request Body:** None
- **Response Model:** `MessageResponse`
- **HTTP Status Codes:** `200 OK`, `403 Forbidden`, `404 Not Found`
- **Database Tables Accessed:** `dbo.homestay_availability_blocks`

### `EP-PRV-14`: List Incoming Pending Booking Requests
- **HTTP Method:** `GET`
- **Path:** `/api/v1/bookings/provider/requests`
- **Handler:** `app.routers.booking.list_provider_bookings`
- **Auth Required:** Bearer Token (`provider`).
- **Query Parameters:**
  - `status` (Optional[str], default="pending"): Filter by status.
  - `page` (int, default=1): Page number.
  - `page_size` (int, default=10): Page size.
- **Request Body:** None
- **Response Model:** `BookingListResponse`
- **HTTP Status Codes:** `200 OK`, `401 Unauthorized`
- **Database Tables Accessed:** `dbo.homestay_bookings`, `dbo.booking_references`, `dbo.homestays`, `dbo.users`

### `EP-PRV-15`: List All Provider Bookings (History & Operational)
- **HTTP Method:** `GET`
- **Path:** `/api/v1/bookings/provider`
- **Handler:** `app.routers.booking.list_provider_bookings`
- **Auth Required:** Bearer Token (`provider`).
- **Query Parameters:**
  - `status` (Optional[str]): Status filter.
  - `page` (int, default=1): Page number.
  - `page_size` (int, default=10): Page size.
- **Request Body:** None
- **Response Model:** `BookingListResponse`
- **HTTP Status Codes:** `200 OK`, `401 Unauthorized`
- **Database Tables Accessed:** `dbo.homestay_bookings`, `dbo.booking_references`, `dbo.homestays`, `dbo.users`

### `EP-PRV-16`: Provider Accept Booking Request
- **HTTP Method:** `POST`
- **Path:** `/api/v1/bookings/{booking_id}/accept`
- **Handler:** `app.routers.booking.accept_booking`
- **Auth Required:** Bearer Token (Listing host owner).
- **Path Parameters:**
  - `booking_id` (int): Booking identifier.
- **Request Body:** None
- **Response Model:** `BookingDetailResponse`
  - If `payment_method == "online"`: Transitions to `awaiting_payment` and sets `payment_expires_at = NOW() + 30m`.
  - If `payment_method == "pay_at_location"`: Transitions immediately to `confirmed`.
- **HTTP Status Codes:** `200 OK`, `400 Bad Request` (Wrong state), `403 Forbidden`, `404 Not Found`, `409 Conflict` (Concurrent dates conflict)
- **Concurrency Guard:** Locks `homestay_booking_{homestay_id}` via `sp_getapplock`.
- **Database Tables Accessed:** `dbo.homestay_bookings`, `dbo.booking_references`, `dbo.booking_status_history`, `dbo.homestays`

### `EP-PRV-17`: Provider Reject Booking Request
- **HTTP Method:** `POST`
- **Path:** `/api/v1/bookings/{booking_id}/reject`
- **Handler:** `app.routers.booking.reject_booking`
- **Auth Required:** Bearer Token (Listing host owner).
- **Path Parameters:**
  - `booking_id` (int): Booking identifier.
- **Request Body (`RejectBookingRequest`):**
  - `reason` (str, min 5 chars, max 500 chars): Reason for decline.
- **Response Model:** `BookingDetailResponse` (`status`: `"rejected"`).
- **HTTP Status Codes:** `200 OK`, `400 Bad Request`, `403 Forbidden`, `404 Not Found`
- **Concurrency Guard:** Locks `homestay_booking_{homestay_id}`.
- **Database Tables Accessed:** `dbo.homestay_bookings`, `dbo.booking_references`, `dbo.booking_status_history`

### `EP-PRV-18`: Provider Mark Booking Completed After Stay
- **HTTP Method:** `POST`
- **Path:** `/api/v1/bookings/{booking_id}/complete`
- **Handler:** `app.routers.booking.complete_booking`
- **Auth Required:** Bearer Token (Listing host owner).
- **Path Parameters:**
  - `booking_id` (int): Booking identifier.
- **Request Body:** None
- **Response Model:** `BookingDetailResponse` (`status`: `"completed"`, unlocks earnings for settlement).
- **HTTP Status Codes:** `200 OK`, `400 Bad Request` (Check-out date not reached yet or payment incomplete), `403 Forbidden`, `404 Not Found`
- **Concurrency Guard:** Locks `homestay_booking_{homestay_id}`.
- **Database Tables Accessed:** `dbo.homestay_bookings`, `dbo.booking_references`, `dbo.booking_status_history`

### `EP-PRV-19`: Provider Record On-Site Cash Payment
- **HTTP Method:** `POST`
- **Path:** `/api/v1/bookings/{booking_id}/record-cash-payment`
- **Handler:** `app.routers.booking.record_cash_payment`
- **Auth Required:** Bearer Token (Listing host owner).
- **Path Parameters:**
  - `booking_id` (int): Booking identifier.
- **Request Body (`RecordCashPaymentRequest`):**
  - `amount_collected` (Decimal): Exact cash amount collected in LKR.
  - `notes` (Optional[str], max 255 chars): On-site collection receipt reference.
- **Response Model:** `BookingDetailResponse` (`cash_payment_recorded`: true, `cash_recorded_at`: datetime).
- **Financial Rule:** Bypasses CeylonGo online payment ledgers and platform commission.
- **HTTP Status Codes:** `200 OK`, `400 Bad Request` (Not cash booking or wrong amount), `403 Forbidden`, `404 Not Found`
- **Database Tables Accessed:** `dbo.homestay_bookings`, `dbo.booking_references`, `dbo.booking_status_history`

### `EP-PRV-20`: Create Listing Promotion Discount
- **HTTP Method:** `POST`
- **Path:** `/api/v1/homestay-promotions`
- **Handler:** `app.routers.promotion.create_promotion`
- **Auth Required:** Bearer Token (`provider`).
- **Request Body (`CreatePromotionRequest`):**
  - `homestay_id` (int): Property identifier.
  - `title` (str, 3–100 chars): Title (e.g., "Early Bird Summer").
  - `description` (Optional[str]): Details.
  - `discount_type` (str): `"percentage"` or `"fixed"`.
  - `discount_percent` (Optional[Decimal], 1–100): If percentage discount.
  - `discount_amount` (Optional[Decimal] > 0): If fixed discount in LKR.
  - `start_date` (date): Validity start.
  - `end_date` (date): Validity end (>= start_date).
- **Response Model:** `PromotionResponse`
- **HTTP Status Codes:** `201 Created`, `400 Bad Request` (Invalid discount or overlapping terms), `403 Forbidden`, `422 Unprocessable Entity`
- **Database Tables Accessed:** `dbo.homestay_promotions`, `dbo.homestays`

### `EP-PRV-21`: Update Listing Promotion Discount
- **HTTP Method:** `PUT`
- **Path:** `/api/v1/homestay-promotions/{promotion_id}`
- **Handler:** `app.routers.promotion.update_promotion`
- **Auth Required:** Bearer Token (Promotion host owner).
- **Path Parameters:**
  - `promotion_id` (int): Promotion identifier.
- **Request Body (`UpdatePromotionRequest`):** Updated promotion fields.
- **Response Model:** `PromotionResponse`
- **HTTP Status Codes:** `200 OK`, `400 Bad Request`, `403 Forbidden`, `404 Not Found`
- **Database Tables Accessed:** `dbo.homestay_promotions`, `dbo.homestays`

### `EP-PRV-22`: Deactivate / Delete Listing Promotion
- **HTTP Method:** `DELETE`
- **Path:** `/api/v1/homestay-promotions/{promotion_id}`
- **Handler:** `app.routers.promotion.delete_promotion`
- **Auth Required:** Bearer Token (Promotion host owner).
- **Path Parameters:**
  - `promotion_id` (int): Promotion identifier.
- **Request Body:** None
- **Response Model:** `MessageResponse` (`status`: `"success"`, `message`: `"Promotion deleted"`).
- **HTTP Status Codes:** `200 OK`, `403 Forbidden`, `404 Not Found`
- **Database Tables Accessed:** `dbo.homestay_promotions`

### `EP-PRV-23`: List Host's Managed Promotions
- **HTTP Method:** `GET`
- **Path:** `/api/v1/homestay-promotions/mine`
- **Handler:** `app.routers.promotion.list_my_promotions`
- **Auth Required:** Bearer Token (`provider`).
- **Request Body:** None
- **Response Model:** List[`PromotionResponse`]
- **HTTP Status Codes:** `200 OK`, `401 Unauthorized`
- **Database Tables Accessed:** `dbo.homestay_promotions`, `dbo.homestays`

### `EP-PRV-24`: View Host's Reviews Across Owned Listings
- **HTTP Method:** `GET`
- **Path:** `/api/v1/homestays/reviews/mine`
- **Handler:** `app.routers.homestay_reviews.list_my_homestay_reviews`
- **Auth Required:** Bearer Token (`provider`).
- **Query Parameters:**
  - `page` (int, default=1): Page number.
  - `page_size` (int, default=10): Items per page.
- **Request Body:** None
- **Response Model:** `ReviewListResponse`
- **HTTP Status Codes:** `200 OK`, `401 Unauthorized`
- **Database Tables Accessed:** `dbo.reviews`, `dbo.homestays`, `dbo.users`

### `EP-PRV-25`: Create Sponsored Campaign for Homestay
- **HTTP Method:** `POST`
- **Path:** `/api/v1/homestays/{homestay_id}/campaigns`
- **Handler:** `app.routers.homestay_campaigns.create_homestay_campaign`
- **Auth Required:** Bearer Token (Host owner).
- **Path Parameters:**
  - `homestay_id` (int): Homestay listing identifier.
- **Request Body (`CreateCampaignRequest`):**
  - `package_id` (int): Advertising package identifier.
  - `title` (str, 3–100 chars): Campaign headline.
  - `target_audience` (Optional[str]): Marketing segment notes.
- **Response Model:** `CampaignResponse` (`id`, `homestay_id`, `package_id`, `status`: `"pending"`, `created_at`).
- **HTTP Status Codes:** `201 Created`, `400 Bad Request`, `403 Forbidden`, `404 Not Found`
- **Database Tables Accessed:** `dbo.promotion_campaigns`, `dbo.promotion_packages`, `dbo.homestays`

### `EP-PRV-26`: List Host's Sponsored Campaigns
- **HTTP Method:** `GET`
- **Path:** `/api/v1/homestays/campaigns/mine`
- **Handler:** `app.routers.homestay_campaigns.list_my_campaigns`
- **Auth Required:** Bearer Token (`provider`).
- **Request Body:** None
- **Response Model:** List[`CampaignResponse`]
- **HTTP Status Codes:** `200 OK`, `401 Unauthorized`
- **Database Tables Accessed:** `dbo.promotion_campaigns`, `dbo.promotion_packages`, `dbo.homestays`

### `EP-PRV-27`: Initiate PayHere Payment for Sponsored Campaign
- **HTTP Method:** `POST`
- **Path:** `/api/v1/homestays/campaigns/{campaign_id}/initiate-payment`
- **Handler:** `app.routers.homestay_campaigns.initiate_campaign_payment`
- **Auth Required:** Bearer Token (Campaign host owner).
- **Path Parameters:**
  - `campaign_id` (int): Campaign identifier.
- **Request Body:** None
- **Response Model:** `PayHereCheckoutPayload` (`merchant_id`, `order_id`: `"CAMP-<id>"`, `amount`, `hash`, etc.).
- **Prerequisite:** Campaign must be in `approved` status before payment can be initiated.
- **HTTP Status Codes:** `200 OK`, `400 Bad Request` (Not approved or already paid), `403 Forbidden`, `404 Not Found`
- **Database Tables Accessed:** `dbo.promotion_campaigns`, `dbo.promotion_packages`, `dbo.advertising_payments`, `dbo.users`

### `EP-PRV-28`: View Advertising Payments for Specific Campaign
- **HTTP Method:** `GET`
- **Path:** `/api/v1/homestays/campaigns/{campaign_id}/payments`
- **Handler:** `app.routers.homestay_campaigns.get_campaign_payments`
- **Auth Required:** Bearer Token (Campaign host owner).
- **Path Parameters:**
  - `campaign_id` (int): Campaign identifier.
- **Request Body:** None
- **Response Model:** List[`AdvertisingPaymentResponse`] (`id`, `campaign_id`, `amount`, `payment_method`, `payment_status`, `created_at`).
- **HTTP Status Codes:** `200 OK`, `403 Forbidden`, `404 Not Found`
- **Database Tables Accessed:** `dbo.advertising_payments`, `dbo.promotion_campaigns`

### `EP-PRV-29`: View Host's Complete Advertising Payment History
- **HTTP Method:** `GET`
- **Path:** `/api/v1/homestays/campaigns/payments/mine`
- **Handler:** `app.routers.homestay_campaigns.get_my_advertising_payments`
- **Auth Required:** Bearer Token (`provider`).
- **Request Body:** None
- **Response Model:** List[`AdvertisingPaymentResponse`]
- **HTTP Status Codes:** `200 OK`, `401 Unauthorized`
- **Database Tables Accessed:** `dbo.advertising_payments`, `dbo.provider_profiles`

### `EP-PRV-30`: Issue Campaign Attribution Token
- **HTTP Method:** `POST`
- **Path:** `/api/v1/homestays/campaigns/{campaign_id}/attribution-token`
- **Handler:** `app.routers.homestay_campaigns.issue_campaign_attribution_token`
- **Auth Required:** Bearer Token (Campaign host owner).
- **Path Parameters:**
  - `campaign_id` (int): Campaign identifier.
- **Request Body:** None
- **Response Model:** `AttributionTokenResponse` (`campaign_id`, `homestay_id`, `token`, `expires_at`).
- **HTTP Status Codes:** `200 OK`, `400 Bad Request` (Campaign not active), `403 Forbidden`, `404 Not Found`
- **Database Tables Accessed:** `dbo.promotion_campaigns`

### `EP-PRV-31`: View Campaign Performance Analytics
- **HTTP Method:** `GET`
- **Path:** `/api/v1/homestays/campaigns/{campaign_id}/analytics`
- **Handler:** `app.routers.homestay_campaigns.get_campaign_analytics`
- **Auth Required:** Bearer Token (Campaign host owner).
- **Path Parameters:**
  - `campaign_id` (int): Campaign identifier.
- **Request Body:** None
- **Response Model:** `CampaignAnalyticsResponse` (`campaign_id`, `impressions_count`, `clicks_count`, `inquiries_count`, `bookings_count`, `ctr`: float, `conversion_rate`: float, `total_spend`: Decimal).
- **HTTP Status Codes:** `200 OK`, `403 Forbidden`, `404 Not Found`
- **Database Tables Accessed:** `dbo.promotion_campaigns`, `dbo.promotion_events`, `dbo.advertising_payments`

### `EP-PRV-32`: Provider Earnings & Settlement Balance Summary
- **HTTP Method:** `GET`
- **Path:** `/api/v1/payments/provider/earnings`
- **Handler:** `app.routers.payments.get_provider_earnings_summary`
- **Auth Required:** Bearer Token (`provider`).
- **Request Body:** None
- **Response Model:** `ProviderEarningsSummaryResponse`
  - `gross_revenue`: Decimal (Total completed online bookings).
  - `commission_deducted`: Decimal (CeylonGo 10% platform commission).
  - `net_earnings`: Decimal (Gross minus 10% commission).
  - `refunds_deducted`: Decimal (Proportional 90% share of refunded bookings).
  - `already_settled`: Decimal (Total funds disbursed via completed settlements).
  - `available_balance`: Decimal (`net_earnings - refunds_deducted - already_settled`).
  - `in_progress_earnings`: Decimal (Earnings from active confirmed bookings not yet checked out).
- **HTTP Status Codes:** `200 OK`, `401 Unauthorized`
- **Database Tables Accessed:** `dbo.homestay_bookings`, `dbo.payment_transactions`, `dbo.payment_refunds`, `dbo.provider_settlements`

### `EP-PRV-33`: List Provider Settlement Records
- **HTTP Method:** `GET`
- **Path:** `/api/v1/payments/provider/settlements`
- **Handler:** `app.routers.payments.list_provider_settlements`
- **Auth Required:** Bearer Token (`provider`).
- **Query Parameters:**
  - `page` (int, default=1): Page number.
  - `page_size` (int, default=10): Items per page.
- **Request Body:** None
- **Response Model:** List[`ProviderSettlementResponse`] (`id`, `reference_code`, `amount`, `period_start`, `period_end`, `payout_status`, `payment_method`, `created_at`).
- **HTTP Status Codes:** `200 OK`, `401 Unauthorized`
- **Database Tables Accessed:** `dbo.provider_settlements`

### `EP-PRV-34`: Get Single Settlement Record Details
- **HTTP Method:** `GET`
- **Path:** `/api/v1/payments/provider/settlements/{settlement_id}`
- **Handler:** `app.routers.payments.get_settlement_detail`
- **Auth Required:** Bearer Token (Host recipient or `admin`).
- **Path Parameters:**
  - `settlement_id` (int): Settlement record identifier.
- **Request Body:** None
- **Response Model:** `SettlementDetailResponse` (Full payout profile, bank details, transaction references).
- **HTTP Status Codes:** `200 OK`, `403 Forbidden`, `404 Not Found`
- **Database Tables Accessed:** `dbo.provider_settlements`

### `EP-PRV-35`: Provider Cancel Booking
- **HTTP Method:** `POST`
- **Path:** `/api/v1/bookings/{booking_id}/cancel`
- **Handler:** `app.routers.booking.cancel_booking`
- **Auth Required:** Bearer Token (Listing host owner).
- **Path Parameters:**
  - `booking_id` (int): Booking identifier.
- **Request Body (`CancelBookingRequest`):**
  - `reason` (str): Reason for host cancellation.
- **Response Model:** `CancelBookingResponse`
- **HTTP Status Codes:** `200 OK`, `400 Bad Request`, `403 Forbidden`, `404 Not Found`
- **Concurrency Guard:** Locks `homestay_booking_{homestay_id}`.
- **Database Tables Accessed:** `dbo.homestay_bookings`, `dbo.booking_references`, `dbo.booking_status_history`, `dbo.payment_transactions`

---

## 5. Administrator Operations (18 Operations)

### `EP-ADM-01`: List Homestays Pending Admin Review
- **HTTP Method:** `GET`
- **Path:** `/api/v1/admin/homestays/pending`
- **Handler:** `app.routers.admin_homestays.list_pending_homestays`
- **Auth Required:** Bearer Token (`admin`).
- **Query Parameters:**
  - `page` (int, default=1): Page number.
  - `page_size` (int, default=10): Items per page.
- **Request Body:** None
- **Response Model:** List[`HomestayPendingReviewResponse`]
- **HTTP Status Codes:** `200 OK`, `403 Forbidden`
- **Database Tables Accessed:** `dbo.homestays`, `dbo.users`, `dbo.provider_profiles`

### `EP-ADM-02`: Admin Homestays Dashboard Metrics
- **HTTP Method:** `GET`
- **Path:** `/api/v1/admin/homestays/dashboard`
- **Handler:** `app.routers.admin_homestays.get_admin_dashboard`
- **Auth Required:** Bearer Token (`admin`).
- **Request Body:** None
- **Response Model:** `AdminHomestaysDashboardResponse` (`total_homestays`, `approved_homestays`, `pending_reviews`, `active_promotions`, `total_bookings`).
- **HTTP Status Codes:** `200 OK`, `403 Forbidden`
- **Database Tables Accessed:** `dbo.homestays`, `dbo.homestay_bookings`, `dbo.homestay_promotions`

### `EP-ADM-03`: View Detailed Listing Submission for Review
- **HTTP Method:** `GET`
- **Path:** `/api/v1/admin/homestays/{homestay_id}`
- **Handler:** `app.routers.admin_homestays.get_homestay_review_details`
- **Auth Required:** Bearer Token (`admin`).
- **Path Parameters:**
  - `homestay_id` (int): Listing identifier.
- **Request Body:** None
- **Response Model:** `AdminHomestayReviewDetailResponse` (Full property data, host KYC, photo URLs, amenities, audit logs).
- **HTTP Status Codes:** `200 OK`, `403 Forbidden`, `404 Not Found`
- **Database Tables Accessed:** `dbo.homestays`, `dbo.users`, `dbo.provider_profiles`, `dbo.homestay_images`, `dbo.homestay_amenities`

### `EP-ADM-04`: Review Homestay Listing (Approve / Reject / Suspend)
- **HTTP Method:** `POST`
- **Path:** `/api/v1/admin/homestays/{homestay_id}/review`
- **Handler:** `app.routers.admin_homestays.review_homestay`
- **Auth Required:** Bearer Token (`admin`).
- **Path Parameters:**
  - `homestay_id` (int): Listing identifier.
- **Request Body (`ReviewHomestayRequest`):**
  - `action` (str): `"approve"`, `"reject"`, `"suspend"`.
  - `rejection_reason` (Optional[str], required if reject): Justification note.
- **Response Model:** `HomestayDetailResponse`
- **HTTP Status Codes:** `200 OK`, `400 Bad Request`, `403 Forbidden`, `404 Not Found`
- **Database Tables Accessed:** `dbo.homestays`, `dbo.admin_audit_logs`

### `EP-ADM-05`: Update Verification Status & Badge
- **HTTP Method:** `POST`
- **Path:** `/api/v1/admin/homestays/{homestay_id}/verify`
- **Handler:** `app.routers.admin_homestays.verify_homestay`
- **Auth Required:** Bearer Token (`admin`).
- **Path Parameters:**
  - `homestay_id` (int): Listing identifier.
- **Request Body (`VerifyHomestayRequest`):**
  - `is_verified` (bool): Verification state flag.
  - `notes` (Optional[str]): Administrative inspection note.
- **Response Model:** `HomestayDetailResponse`
- **HTTP Status Codes:** `200 OK`, `403 Forbidden`, `404 Not Found`
- **Database Tables Accessed:** `dbo.homestays`, `dbo.admin_audit_logs`

### `EP-ADM-06`: Moderate Review (Publish / Unpublish)
- **HTTP Method:** `POST`
- **Path:** `/api/v1/homestays/reviews/{review_id}/moderate`
- **Handler:** `app.routers.homestay_reviews.moderate_review`
- **Auth Required:** Bearer Token (`admin`).
- **Path Parameters:**
  - `review_id` (int): Review identifier.
- **Request Body (`ModerateReviewRequest`):**
  - `is_published` (bool): Visibility state.
  - `moderation_note` (Optional[str]): Reason for moderation action.
- **Response Model:** `ReviewItemResponse`
- **HTTP Status Codes:** `200 OK`, `403 Forbidden`, `404 Not Found`
- **Database Tables Accessed:** `dbo.reviews`, `dbo.admin_audit_logs`

### `EP-ADM-07`: Create Promotion Package
- **HTTP Method:** `POST`
- **Path:** `/api/v1/admin/homestays/promotions/packages`
- **Handler:** `app.routers.homestay_campaigns.admin_create_package`
- **Auth Required:** Bearer Token (`admin`).
- **Request Body (`CreatePackageRequest`):**
  - `name` (str, 3–100 chars): Package name (e.g. "Platinum Homepage Feature").
  - `description` (str): Marketing features.
  - `price` (Decimal > 0): Package fee in LKR.
  - `duration_days` (int >= 1): Duration.
  - `package_type` (str): `"standard"`, `"featured"`, `"premium"`.
- **Response Model:** `PromotionPackageResponse`
- **HTTP Status Codes:** `201 Created`, `403 Forbidden`, `422 Unprocessable Entity`
- **Database Tables Accessed:** `dbo.promotion_packages`

### `EP-ADM-08`: List All Promotion Packages (Including Inactive)
- **HTTP Method:** `GET`
- **Path:** `/api/v1/admin/homestays/promotions/packages`
- **Handler:** `app.routers.homestay_campaigns.admin_list_all_packages`
- **Auth Required:** Bearer Token (`admin`).
- **Request Body:** None
- **Response Model:** List[`PromotionPackageResponse`]
- **HTTP Status Codes:** `200 OK`, `403 Forbidden`
- **Database Tables Accessed:** `dbo.promotion_packages`

### `EP-ADM-09`: Update or Toggle Promotion Package
- **HTTP Method:** `PATCH`
- **Path:** `/api/v1/admin/homestays/promotions/packages/{package_id}`
- **Handler:** `app.routers.homestay_campaigns.admin_update_package`
- **Auth Required:** Bearer Token (`admin`).
- **Path Parameters:**
  - `package_id` (int): Package identifier.
- **Request Body (`UpdatePackageRequest`):** Editable fields (`price`, `is_active`, `description`).
- **Response Model:** `PromotionPackageResponse`
- **HTTP Status Codes:** `200 OK`, `403 Forbidden`, `404 Not Found`
- **Database Tables Accessed:** `dbo.promotion_packages`

### `EP-ADM-10`: List All Sponsored Campaigns Across Platform
- **HTTP Method:** `GET`
- **Path:** `/api/v1/admin/homestays/campaigns`
- **Handler:** `app.routers.homestay_campaigns.admin_list_campaigns`
- **Auth Required:** Bearer Token (`admin`).
- **Query Parameters:**
  - `status` (Optional[str]): Status filter (`pending`, `approved`, `rejected`, `active`, `completed`).
- **Request Body:** None
- **Response Model:** List[`CampaignAdminSummaryResponse`]
- **HTTP Status Codes:** `200 OK`, `403 Forbidden`
- **Database Tables Accessed:** `dbo.promotion_campaigns`, `dbo.homestays`, `dbo.promotion_packages`, `dbo.provider_profiles`

### `EP-ADM-11`: Approve Sponsored Campaign
- **HTTP Method:** `POST`
- **Path:** `/api/v1/admin/homestays/campaigns/{campaign_id}/approve`
- **Handler:** `app.routers.homestay_campaigns.admin_approve_campaign`
- **Auth Required:** Bearer Token (`admin`).
- **Path Parameters:**
  - `campaign_id` (int): Campaign identifier.
- **Request Body:** None
- **Response Model:** `CampaignResponse` (`status`: `"approved"`).
- **HTTP Status Codes:** `200 OK`, `400 Bad Request`, `403 Forbidden`, `404 Not Found`
- **Database Tables Accessed:** `dbo.promotion_campaigns`

### `EP-ADM-12`: Reject Sponsored Campaign
- **HTTP Method:** `POST`
- **Path:** `/api/v1/admin/homestays/campaigns/{campaign_id}/reject`
- **Handler:** `app.routers.homestay_campaigns.admin_reject_campaign`
- **Auth Required:** Bearer Token (`admin`).
- **Path Parameters:**
  - `campaign_id` (int): Campaign identifier.
- **Request Body (`RejectCampaignRequest`):**
  - `reason` (str, min 5 chars): Rejection rationale.
- **Response Model:** `CampaignResponse` (`status`: `"rejected"`).
- **HTTP Status Codes:** `200 OK`, `400 Bad Request`, `403 Forbidden`, `404 Not Found`
- **Database Tables Accessed:** `dbo.promotion_campaigns`

### `EP-ADM-13`: Platform Promotion Analytics Summary
- **HTTP Method:** `GET`
- **Path:** `/api/v1/admin/homestays/campaigns/analytics`
- **Handler:** `app.routers.homestay_campaigns.admin_get_promotions_analytics`
- **Auth Required:** Bearer Token (`admin`).
- **Request Body:** None
- **Response Model:** `AdminPromotionAnalyticsResponse` (`total_campaigns`, `active_campaigns`, `total_impressions`, `total_clicks`, `total_conversions`, `total_ad_revenue`: Decimal).
- **HTTP Status Codes:** `200 OK`, `403 Forbidden`
- **Database Tables Accessed:** `dbo.promotion_campaigns`, `dbo.promotion_events`, `dbo.advertising_payments`

### `EP-ADM-14`: List Advertising Payments
- **HTTP Method:** `GET`
- **Path:** `/api/v1/admin/advertising/payments`
- **Handler:** `app.routers.homestay_campaigns.admin_list_advertising_payments`
- **Auth Required:** Bearer Token (`admin`).
- **Request Body:** None
- **Response Model:** List[`AdvertisingPaymentResponse`]
- **HTTP Status Codes:** `200 OK`, `403 Forbidden`
- **Database Tables Accessed:** `dbo.advertising_payments`

### `EP-ADM-15`: Advertising Revenue Summary
- **HTTP Method:** `GET`
- **Path:** `/api/v1/admin/advertising/revenue-summary`
- **Handler:** `app.routers.homestay_campaigns.admin_get_advertising_revenue_summary`
- **Auth Required:** Bearer Token (`admin`).
- **Request Body:** None
- **Response Model:** `AdvertisingRevenueSummaryResponse` (`gross_advertising_volume`, `settled_volume`, `active_campaigns_value`).
- **HTTP Status Codes:** `200 OK`, `403 Forbidden`
- **Database Tables Accessed:** `dbo.advertising_payments`

### `EP-ADM-16`: List All System Payment Transactions
- **HTTP Method:** `GET`
- **Path:** `/api/v1/payments/admin/transactions`
- **Handler:** `app.routers.payments.list_all_payment_transactions`
- **Auth Required:** Bearer Token (`admin`).
- **Query Parameters:**
  - `status` (Optional[str]): Payment status.
  - `page` (int, default=1): Page number.
  - `page_size` (int, default=20): Page size.
- **Request Body:** None
- **Response Model:** List[`PaymentTransactionResponse`]
- **HTTP Status Codes:** `200 OK`, `403 Forbidden`
- **Database Tables Accessed:** `dbo.payment_transactions`, `dbo.booking_references`

### `EP-ADM-17`: List All Refund Requests
- **HTTP Method:** `GET`
- **Path:** `/api/v1/payments/admin/refunds`
- **Handler:** `app.routers.payments.list_all_refunds`
- **Auth Required:** Bearer Token (`admin`).
- **Query Parameters:**
  - `status` (Optional[str]): `"pending"`, `"approved"`, `"completed"`, `"rejected"`.
- **Request Body:** None
- **Response Model:** List[`RefundAdminResponse`]
- **HTTP Status Codes:** `200 OK`, `403 Forbidden`
- **Database Tables Accessed:** `dbo.payment_refunds`, `dbo.homestay_bookings`, `dbo.users`

### `EP-ADM-18`: Process Refund Request (Approve / Reject / Complete)
- **HTTP Method:** `POST`
- **Path:** `/api/v1/payments/admin/refunds/{refund_id}/process`
- **Handler:** `app.routers.payments.process_refund`
- **Auth Required:** Bearer Token (`admin`).
- **Path Parameters:**
  - `refund_id` (int): Refund request identifier.
- **Request Body (`ProcessRefundRequest`):**
  - `action` (str): `"approve"`, `"reject"`, `"complete"`.
  - `notes` (Optional[str]): Administration execution notes.
  - `external_reference` (Optional[str]): Bank / gateway refund transaction ID (required if `action == "complete"`).
- **Response Model:** `RefundAdminResponse`
- **Financial Rule:** When completed, proportionally reverses 10% CeylonGo commission and deducts 90% from provider balance. Guard prevents false completions with arbitrary references.
- **HTTP Status Codes:** `200 OK`, `400 Bad Request`, `403 Forbidden`, `404 Not Found`
- **Database Tables Accessed:** `dbo.payment_refunds`, `dbo.payment_transactions`, `dbo.homestay_bookings`, `dbo.admin_audit_logs`

### `EP-ADM-19`: Record Provider Earnings Settlement Payout
- **HTTP Method:** `POST`
- **Path:** `/api/v1/payments/admin/settlements`
- **Handler:** `app.routers.payments.create_provider_settlement`
- **Auth Required:** Bearer Token (`admin`).
- **Request Body (`CreateSettlementRequest`):**
  - `provider_id` (int): Service provider profile ID.
  - `amount` (Decimal > 0): Payout amount in LKR.
  - `period_start` (date): Earliest completed booking check-out included.
  - `period_end` (date): Latest completed booking check-out included.
  - `payment_method` (str): `"bank_transfer"`, `"check"`, `"online"`.
  - `reference_code` (str): Unique bank transaction / wire transfer reference code.
  - `notes` (Optional[str]): Remittance remarks.
- **Response Model:** `ProviderSettlementResponse`
- **Validation:** Payout amount cannot exceed provider's actual `available_balance`.
- **HTTP Status Codes:** `201 Created`, `400 Bad Request` (Insufficient balance or overlapping periods), `403 Forbidden`, `404 Not Found`
- **Database Tables Accessed:** `dbo.provider_settlements`, `dbo.provider_profiles`, `dbo.payment_transactions`, `dbo.homestay_bookings`

### `EP-ADM-20`: Platform Financial Overview & Liabilities
- **HTTP Method:** `GET`
- **Path:** `/api/v1/payments/admin/summary`
- **Handler:** `app.routers.payments.admin_get_financial_summary`
- **Auth Required:** Bearer Token (`admin`).
- **Request Body:** None
- **Response Model:** `AdminFinancialSummaryResponse`
  - `gross_booking_volume`: Total volume of confirmed online bookings.
  - `total_platform_commission`: Platform's cumulative 10% earned revenue.
  - `total_refunds_processed`: Cumulative refunded amount.
  - `commission_reversed`: Cumulative 10% commission reversed due to refunds.
  - `net_platform_revenue`: (`total_platform_commission - commission_reversed`).
  - `total_provider_liabilities`: Cumulative 90% provider earnings payable.
  - `total_settlements_disbursed`: Cumulative provider bank payouts executed.
  - `outstanding_provider_payable`: (`total_provider_liabilities - total_settlements_disbursed`).
  - `total_advertising_revenue`: Gross revenue from sponsored advertising campaigns.
- **HTTP Status Codes:** `200 OK`, `403 Forbidden`
- **Database Tables Accessed:** `dbo.payment_transactions`, `dbo.payment_refunds`, `dbo.provider_settlements`, `dbo.advertising_payments`, `dbo.homestay_bookings`

### `EP-ADM-21`: Admin Reconciliation Queue
- **HTTP Method:** `GET`
- **Path:** `/api/v1/payments/admin/reconciliation-queue`
- **Handler:** `app.routers.payments.admin_get_reconciliation_queue`
- **Auth Required:** Bearer Token (`admin`).
- **Request Body:** None
- **Response Model:** List[`ReconciliationQueueItemResponse`] (`booking_id`, `transaction_id`, `amount`, `payment_status`, `received_at`, `reason`: `"LATE_PAYMENT_AFTER_EXPIRATION"|"AMOUNT_MISMATCH"`).
- **HTTP Status Codes:** `200 OK`, `403 Forbidden`
- **Database Tables Accessed:** `dbo.payment_transactions`, `dbo.homestay_bookings`, `dbo.booking_references`

---

## 6. Cross-Reference Summary Table

| Operation ID | Method | Path | Role | Handler | Main Tables | Status |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `EP-PUB-01` | GET | `/api/v1/homestays` | Public | `list_homestays` | `homestays` | Verified |
| `EP-PUB-02` | GET | `/api/v1/homestays/{homestay_id}` | Public | `get_homestay` | `homestays`, `reviews` | Verified |
| `EP-PUB-03` | GET | `/api/v1/homestays/{homestay_id}/availability` | Public | `check_homestay_availability` | `homestay_bookings`, `availability_blocks` | Verified |
| `EP-PUB-04` | GET | `/api/v1/homestays/{homestay_id}/price-preview` | Public | `preview_homestay_price` | `homestays`, `homestay_promotions` | Verified |
| `EP-PUB-05` | GET | `/api/v1/homestays/{homestay_id}/reviews` | Public | `list_homestay_reviews` | `reviews`, `users` | Verified |
| `EP-PUB-06` | POST | `/api/v1/payments/payhere/notify` | Webhook | `payhere_webhook_listener` | `payment_transactions`, `homestay_bookings` | Verified |
| `EP-TRV-01` | GET | `/api/v1/homestays/{homestay_id}/contact` | Traveler | `get_homestay_provider_contact` | `homestays`, `users` | Verified |
| `EP-TRV-02` | POST | `/api/v1/bookings` | Traveler | `create_booking` | `homestay_bookings`, `booking_references` | Verified |
| `EP-TRV-03` | GET | `/api/v1/bookings/me` | Traveler | `get_my_bookings` | `homestay_bookings` | Verified |
| `EP-TRV-04` | GET | `/api/v1/bookings/{booking_id}` | Traveler | `get_booking` | `homestay_bookings`, `payment_transactions` | Verified |
| `EP-TRV-05` | POST | `/api/v1/bookings/{booking_id}/cancel` | Traveler | `cancel_booking` | `homestay_bookings`, `booking_status_history` | Verified |
| `EP-TRV-06` | POST | `/api/v1/payments/bookings/{booking_id}/initiate` | Traveler | `initiate_booking_payment` | `payment_transactions`, `homestay_bookings` | Verified |
| `EP-TRV-07` | GET | `/api/v1/payments/bookings/{booking_id}/status` | Traveler | `get_booking_payment_status` | `payment_transactions` | Verified |
| `EP-TRV-08` | POST | `/api/v1/payments/bookings/{booking_id}/request-refund` | Traveler | `request_booking_refund` | `payment_refunds` | Verified |
| `EP-TRV-09` | POST | `/api/v1/homestays/{homestay_id}/reviews` | Traveler | `submit_homestay_review` | `reviews`, `homestay_bookings` | Verified |
| `EP-TRV-10` | GET | `/api/v1/homestay-promotions` | Traveler | `list_active_promotions` | `homestay_promotions` | Verified |
| `EP-TRV-11` | GET | `/api/v1/homestays/{homestay_id}/attribution-token` | Traveler | `get_homestay_active_attribution_token` | `promotion_campaigns` | Verified |
| `EP-TRV-12` | POST | `/api/v1/homestays/campaigns/{campaign_id}/events` | Traveler | `log_promotion_event` | `promotion_events` | Verified |
| `EP-TRV-13` | POST | `/api/v1/wishlists/homestays/{homestay_id}` | Traveler | `add_homestay_to_wishlist` | `wishlists` | Verified |
| `EP-TRV-14` | DELETE | `/api/v1/wishlists/homestays/{homestay_id}` | Traveler | `remove_homestay_from_wishlist` | `wishlists` | Verified |
| `EP-TRV-15` | GET | `/api/v1/wishlists/homestays` | Traveler | `get_my_homestay_wishlist` | `wishlists` | Verified |
| `EP-TRV-16` | GET | `/api/v1/wishlists/homestays/{homestay_id}/status` | Traveler | `check_homestay_wishlist_status` | `wishlists` | Verified |
| `EP-TRV-17` | GET | `/api/v1/homestays/promotions/packages` | Traveler | `list_active_packages` | `promotion_packages` | Verified |
| `EP-PRV-01` | POST | `/api/v1/homestays` | Provider | `create_homestay` | `homestays`, `homestay_amenities` | Verified |
| `EP-PRV-02` | GET | `/api/v1/homestays/mine` | Provider | `list_my_homestays` | `homestays` | Verified |
| `EP-PRV-03` | GET | `/api/v1/homestays/provider/dashboard` | Provider | `get_provider_dashboard` | `homestays`, `homestay_bookings` | Verified |
| `EP-PRV-04` | PUT | `/api/v1/homestays/{homestay_id}` | Provider | `update_homestay` | `homestays` | Verified |
| `EP-PRV-05` | DELETE | `/api/v1/homestays/{homestay_id}` | Provider | `delete_homestay` | `homestays` | Verified |
| `EP-PRV-06` | POST | `/api/v1/homestays/{homestay_id}/submit-approval` | Provider | `submit_homestay_for_approval` | `homestays` | Verified |
| `EP-PRV-07` | POST | `/api/v1/homestays/{homestay_id}/images` | Provider | `add_homestay_image` | `homestay_images` | Verified |
| `EP-PRV-08` | DELETE | `/api/v1/homestays/{homestay_id}/images/{image_id}` | Provider | `delete_homestay_image` | `homestay_images` | Verified |
| `EP-PRV-09` | PATCH | `/api/v1/homestays/{homestay_id}/images/{image_id}/set-primary` | Provider | `set_primary_homestay_image` | `homestay_images` | Verified |
| `EP-PRV-10` | PATCH | `/api/v1/homestays/{homestay_id}/images/reorder` | Provider | `reorder_homestay_images` | `homestay_images` | Verified |
| `EP-PRV-11` | POST | `/api/v1/homestays/{homestay_id}/availability-blocks` | Provider | `create_availability_block` | `homestay_availability_blocks` | Verified |
| `EP-PRV-12` | GET | `/api/v1/homestays/{homestay_id}/availability-blocks` | Provider | `list_availability_blocks` | `homestay_availability_blocks` | Verified |
| `EP-PRV-13` | DELETE | `/api/v1/homestays/{homestay_id}/availability-blocks/{block_id}` | Provider | `delete_availability_block` | `homestay_availability_blocks` | Verified |
| `EP-PRV-14` | GET | `/api/v1/bookings/provider/requests` | Provider | `list_provider_bookings` | `homestay_bookings` | Verified |
| `EP-PRV-15` | GET | `/api/v1/bookings/provider` | Provider | `list_provider_bookings` | `homestay_bookings` | Verified |
| `EP-PRV-16` | POST | `/api/v1/bookings/{booking_id}/accept` | Provider | `accept_booking` | `homestay_bookings` | Verified |
| `EP-PRV-17` | POST | `/api/v1/bookings/{booking_id}/reject` | Provider | `reject_booking` | `homestay_bookings` | Verified |
| `EP-PRV-18` | POST | `/api/v1/bookings/{booking_id}/complete` | Provider | `complete_booking` | `homestay_bookings` | Verified |
| `EP-PRV-19` | POST | `/api/v1/bookings/{booking_id}/record-cash-payment` | Provider | `record_cash_payment` | `homestay_bookings` | Verified |
| `EP-PRV-20` | POST | `/api/v1/homestay-promotions` | Provider | `create_promotion` | `homestay_promotions` | Verified |
| `EP-PRV-21` | PUT | `/api/v1/homestay-promotions/{promotion_id}` | Provider | `update_promotion` | `homestay_promotions` | Verified |
| `EP-PRV-22` | DELETE | `/api/v1/homestay-promotions/{promotion_id}` | Provider | `delete_promotion` | `homestay_promotions` | Verified |
| `EP-PRV-23` | GET | `/api/v1/homestay-promotions/mine` | Provider | `list_my_promotions` | `homestay_promotions` | Verified |
| `EP-PRV-24` | GET | `/api/v1/homestays/reviews/mine` | Provider | `list_my_homestay_reviews` | `reviews` | Verified |
| `EP-PRV-25` | POST | `/api/v1/homestays/{homestay_id}/campaigns` | Provider | `create_homestay_campaign` | `promotion_campaigns` | Verified |
| `EP-PRV-26` | GET | `/api/v1/homestays/campaigns/mine` | Provider | `list_my_campaigns` | `promotion_campaigns` | Verified |
| `EP-PRV-27` | POST | `/api/v1/homestays/campaigns/{campaign_id}/initiate-payment` | Provider | `initiate_campaign_payment` | `advertising_payments` | Verified |
| `EP-PRV-28` | GET | `/api/v1/homestays/campaigns/{campaign_id}/payments` | Provider | `get_campaign_payments` | `advertising_payments` | Verified |
| `EP-PRV-29` | GET | `/api/v1/homestays/campaigns/payments/mine` | Provider | `get_my_advertising_payments` | `advertising_payments` | Verified |
| `EP-PRV-30` | POST | `/api/v1/homestays/campaigns/{campaign_id}/attribution-token` | Provider | `issue_campaign_attribution_token` | `promotion_campaigns` | Verified |
| `EP-PRV-31` | GET | `/api/v1/homestays/campaigns/{campaign_id}/analytics` | Provider | `get_campaign_analytics` | `promotion_events` | Verified |
| `EP-PRV-32` | GET | `/api/v1/payments/provider/earnings` | Provider | `get_provider_earnings_summary` | `payment_transactions`, `settlements` | Verified |
| `EP-PRV-33` | GET | `/api/v1/payments/provider/settlements` | Provider | `list_provider_settlements` | `provider_settlements` | Verified |
| `EP-PRV-34` | GET | `/api/v1/payments/provider/settlements/{settlement_id}` | Provider | `get_settlement_detail` | `provider_settlements` | Verified |
| `EP-PRV-35` | POST | `/api/v1/bookings/{booking_id}/cancel` | Provider | `cancel_booking` | `homestay_bookings` | Verified |
| `EP-ADM-01` | GET | `/api/v1/admin/homestays/pending` | Admin | `list_pending_homestays` | `homestays` | Verified |
| `EP-ADM-02` | GET | `/api/v1/admin/homestays/dashboard` | Admin | `get_admin_dashboard` | `homestays`, `homestay_bookings` | Verified |
| `EP-ADM-03` | GET | `/api/v1/admin/homestays/{homestay_id}` | Admin | `get_homestay_review_details` | `homestays`, `provider_profiles` | Verified |
| `EP-ADM-04` | POST | `/api/v1/admin/homestays/{homestay_id}/review` | Admin | `review_homestay` | `homestays`, `admin_audit_logs` | Verified |
| `EP-ADM-05` | POST | `/api/v1/admin/homestays/{homestay_id}/verify` | Admin | `verify_homestay` | `homestays`, `admin_audit_logs` | Verified |
| `EP-ADM-06` | POST | `/api/v1/homestays/reviews/{review_id}/moderate` | Admin | `moderate_review` | `reviews`, `admin_audit_logs` | Verified |
| `EP-ADM-07` | POST | `/api/v1/admin/homestays/promotions/packages` | Admin | `admin_create_package` | `promotion_packages` | Verified |
| `EP-ADM-08` | GET | `/api/v1/admin/homestays/promotions/packages` | Admin | `admin_list_all_packages` | `promotion_packages` | Verified |
| `EP-ADM-09` | PATCH | `/api/v1/admin/homestays/promotions/packages/{package_id}` | Admin | `admin_update_package` | `promotion_packages` | Verified |
| `EP-ADM-10` | GET | `/api/v1/admin/homestays/campaigns` | Admin | `admin_list_campaigns` | `promotion_campaigns` | Verified |
| `EP-ADM-11` | POST | `/api/v1/admin/homestays/campaigns/{campaign_id}/approve` | Admin | `admin_approve_campaign` | `promotion_campaigns` | Verified |
| `EP-ADM-12` | POST | `/api/v1/admin/homestays/campaigns/{campaign_id}/reject` | Admin | `admin_reject_campaign` | `promotion_campaigns` | Verified |
| `EP-ADM-13` | GET | `/api/v1/admin/homestays/campaigns/analytics` | Admin | `admin_get_promotions_analytics` | `promotion_events` | Verified |
| `EP-ADM-14` | GET | `/api/v1/admin/advertising/payments` | Admin | `admin_list_advertising_payments` | `advertising_payments` | Verified |
| `EP-ADM-15` | GET | `/api/v1/admin/advertising/revenue-summary` | Admin | `admin_get_advertising_revenue_summary` | `advertising_payments` | Verified |
| `EP-ADM-16` | GET | `/api/v1/payments/admin/transactions` | Admin | `list_all_payment_transactions` | `payment_transactions` | Verified |
| `EP-ADM-17` | GET | `/api/v1/payments/admin/refunds` | Admin | `list_all_refunds` | `payment_refunds` | Verified |
| `EP-ADM-18` | POST | `/api/v1/payments/admin/refunds/{refund_id}/process` | Admin | `process_refund` | `payment_refunds`, `transactions` | Verified |
| `EP-ADM-19` | POST | `/api/v1/payments/admin/settlements` | Admin | `create_provider_settlement` | `provider_settlements` | Verified |
| `EP-ADM-20` | GET | `/api/v1/payments/admin/summary` | Admin | `admin_get_financial_summary` | `payment_transactions`, `refunds` | Verified |
| `EP-ADM-21` | GET | `/api/v1/payments/admin/reconciliation-queue` | Admin | `admin_get_reconciliation_queue` | `payment_transactions`, `bookings` | Verified |

*Note:* Total distinct operation cards above cover 76 operations (where shared handlers like `cancel_booking` and aliases like `provider/requests` map to their respective user role permissions).
