# CeylonGo Artisan & Small Business Promotion — Development Gap Audit

**Audit date:** 20 November 2026  
**Repository and branch:** CeylonGo-Backend, `feature/artisan-integration`  
**Base commit:** `1043307`. The audit includes modified and untracked Phase 03 files.

## 1. Executive Summary

**74.8% of specified behavior is implemented by the scoring method in §11. Verified completion is 23.3% (10 of 43 features).** Four more features have complete backend contracts on inspection but await environment or client verification. There are 22 partial and seven absent features.

The strongest deliverables are the catalog, provider CRUD, uploads, hours administration, and admin approval. The largest gaps are paid sponsorship, controlled product categories, a unified reservation/cash contract, and complete client integration. Rejection and product favorites exist in the working tree and have offline coverage. Migrations 037 and 038 remain the release gate for those changes.

## 2. Current Development Status

The source is the attached [approved specification](<C:/Users/hp/Documents/Hapinayan/Files/Development Documents/CeylonGo_Artisan_Complete_Development_Specification.pdf>), pp. 1–4, dated 17 November 2026. The supplied file has no “(4)” suffix. Its behavior and acceptance text defines the audit target. Its suggested routes, conceptual entities, implementation directions and deadline are document content. This request authorized inspection and a report.

Evidence came from registered FastAPI routes and OpenAPI, SQLAlchemy mapping, current handlers and guards, SQL migrations and pytest collection. Historical pass counts and live schema evidence are explicitly labeled in §§7 and 9. The audit made no database connection and ran no fixture-writing tests. Angular and Flutter completion is outside the backend score. The scope remains shop pickup with cash; cart, online product checkout, delivery and shipment tracking are excluded.

### Reading the feature rows

Routes link to their registered handlers. Shared dependencies and hidden compatibility aliases are in §6. Coverage lists existing feature or supporting suites; positive acceptance gaps are identified separately. Storage abbreviations resolve to tables, model locations and migration versions in §7. Source links include the audited line as a file anchor.

## 3. Traveler Feature Audit

### T-01 — Browse approved active local businesses

**COMPLETED · 100%**. Public catalog with pagination and provider/listing verification policy; approval filtering tested.

- **API and source** — [GET `/api/v1/artisan-listings`](../../app/routers/artisan_listings.py#L39).
- **Data and migration** — `L` (§7). **Access** — public.
- **Coverage** — test_artisan_listings.py, test_artisan_public_approval_filtering.py.
- **Remaining** — Confirm with product owner whether approval should suffice without location verification.

### T-02 — Search district, location, business and product category

**PARTIALLY COMPLETED · 70%**. Business search, district, craft categories and nearby filters work; product classification is product/service only; catalog filters tested.

- **API and source** — [GET `/api/v1/artisan-listings`](../../app/routers/artisan_listings.py#L39), [GET `/api/v1/artisan-listings/categories`](../../app/routers/artisan_listings.py#L93), [GET `/api/v1/artisan-listings/districts`](../../app/routers/artisan_listings.py#L109), [GET `/api/v1/artisan-listings/nearby`](../../app/routers/artisan_listings.py#L203), [GET `/api/v1/showcase-items`](../../app/routers/showcase_items.py#L30).
- **Data and migration** — `L`, `I` (§7). **Access** — public.
- **Coverage** — test_artisan_listings.py, test_artisan_public_approval_filtering.py; test_interactive_map.py (shared; direct path coverage not confirmed); test_showcase_items.py, test_artisan_public_approval_filtering.py.
- **Remaining** — Add governed product categories and coherent product filter contract.

### T-03 — Browse published local products

**COMPLETED · 100%**. Active approved items under public parents; item type separates products/services; catalog tests exercise publication.

- **API and source** — [GET `/api/v1/showcase-items`](../../app/routers/showcase_items.py#L30), [GET `/api/v1/artisan-listings/{listing_id}/showcase-items`](../../app/routers/showcase_items.py#L150).
- **Data and migration** — `I`, `L` (§7). **Access** — public.
- **Coverage** — test_showcase_items.py, test_artisan_public_approval_filtering.py.
- **Remaining** — Exercise public filters in client smoke tests.

### T-04 — See description, price, stock and product category

**PARTIALLY COMPLETED · 80%**. Description, price, stock, availability and images exist; product category is absent; detail and stock tests exist.

- **API and source** — [GET `/api/v1/showcase-items/{id}`](../../app/routers/showcase_items.py#L119).
- **Data and migration** — `I`, `L`, `PI` (§7). **Access** — public.
- **Coverage** — test_showcase_items.py, test_artisan_public_approval_filtering.py.
- **Remaining** — Expose selected category and client-ready availability.

### T-05 — View multiple product photos

**PARTIALLY COMPLETED · 85%**. Detail and gallery expose multiple ordered photos; raw files have no entity visibility check and gallery predicate differs; gallery tests cover uploads and path traversal.

- **API and source** — [GET `/api/v1/showcase-items/{item_id}/images`](../../app/routers/artisan_images.py#L622), [GET `/uploads/artisan/products/{filename}`](../../app/routers/artisan_images.py#L922).
- **Data and migration** — `PI`, `I`, `L` (§7). **Access** — public; optional JWT for authorized preview where available.
- **Coverage** — test_artisan_gallery.py.
- **Remaining** — Enforce or explicitly approve media visibility and test direct URL access after suspension.

### T-06 — View business story, specialties and products

**PARTIALLY COMPLETED · 80%**. Description and single craft category approximate story and specialties; products are separate API; profile details covered.

- **API and source** — [GET `/api/v1/artisan-listings/{id}`](../../app/routers/artisan_listings.py#L292), [GET `/api/v1/artisan-listings/{listing_id}/showcase-items`](../../app/routers/showcase_items.py#L150).
- **Data and migration** — `L`, `I`, `BI` (§7). **Access** — public.
- **Coverage** — test_artisan_listings.py, test_artisan_public_approval_filtering.py; test_showcase_items.py, test_artisan_public_approval_filtering.py.
- **Remaining** — Confirm description reuse or add explicit story/specialties contract.

### T-07 — Shop map pin and directions

**IMPLEMENTED — VERIFICATION PENDING · 100%**. Coordinates and Artisan map pins plus shared routing exist; map tests cover pin eligibility; Google Maps consumer behavior is unverified.

- **API and source** — [GET `/api/v1/artisan-listings/{id}`](../../app/routers/artisan_listings.py#L292), GET /api/v1/map/locations, /nearby, /search, /categories; POST /api/v1/map/route (§6).
- **Data and migration** — `L` (§7). **Access** — public.
- **Coverage** — test_artisan_listings.py, test_artisan_public_approval_filtering.py; test_interactive_map.py.
- **Remaining** — Validate directions from real Angular and Flutter clients with valid and missing coordinates.

### T-08 — Find nearby shops with location permission

**IMPLEMENTED — VERIFICATION PENDING · 100%**. Coordinates, radius, Haversine ordering and shared nearby search exist; direct route scans qualifying geo rows; shared map tests exist.

- **API and source** — [GET `/api/v1/artisan-listings/nearby`](../../app/routers/artisan_listings.py#L203), GET /api/v1/map/locations, /nearby, /search, /categories; POST /api/v1/map/route (§6).
- **Data and migration** — `L` (§7). **Access** — public.
- **Coverage** — test_interactive_map.py (shared; direct path coverage not confirmed); test_interactive_map.py.
- **Remaining** — Verify consent, denial, and manual-location UX on both clients; add direct route load/geo cases.

### T-09 — Contact seller by phone, WhatsApp or email when supplied

**PARTIALLY COMPLETED · 65%**. Phone/email exposed; no dedicated Artisan WhatsApp field; schema and detail coverage, no contact action test.

- **API and source** — [GET `/api/v1/artisan-listings/{id}`](../../app/routers/artisan_listings.py#L292).
- **Data and migration** — `L` (§7). **Access** — public.
- **Coverage** — test_artisan_listings.py, test_artisan_public_approval_filtering.py.
- **Remaining** — Add/approve safe WhatsApp and phone normalization and verify client contact links.

### T-10 — View published business opening hours

**PARTIALLY COMPLETED · 90%**. Structured schedules and legacy string supported and tested; standalone read checks approval but differs from catalog verification/account eligibility.

- **API and source** — [GET `/api/v1/artisan-listings/{id}`](../../app/routers/artisan_listings.py#L292), [GET `/api/v1/artisan-listings/{listing_id}/business-hours`](../../app/routers/artisan_business_hours.py#L54).
- **Data and migration** — `H`, `L` (§7). **Access** — public; optional JWT for authorized preview where available.
- **Coverage** — test_artisan_listings.py, test_artisan_public_approval_filtering.py; test_artisan_business_hours.py.
- **Remaining** — Unify public visibility and test suspended/inactive accounts.

### T-11 — Reserve quantity and pickup date subject to provider approval

**PARTIALLY COMPLETED · 85%**. Both create shapes check eligible stock and dates; acceptance has different pickup rules and no acceptance-time stock-policy check; legacy expiry gap; reservation/stock tests exist.

- **API and source** — [PATCH `/api/v1/artisan-reservations/{id}/confirm`](../../app/routers/artisan_reservations.py#L320), [POST `/api/v1/artisan-reservations`](../../app/routers/provider_reservations.py#L326), GET /api/v1/reservations/artisan_reservation/me; POST /api/v1/reservations/artisan_reservation/{booking_id}/cancel; GET /api/v1/provider/bookings and /{booking_id}; POST /api/v1/provider/bookings/{booking_id}/action (§6).
- **Data and migration** — `R`, `I`, `L`, `BR` (§7). **Access** — traveler JWT; approved Artisan provider JWT and ownership where applicable.
- **Coverage** — test_artisan_reservations.py, test_provider_phase03.py; test_provider_phase03.py.
- **Remaining** — Unify lifecycle, reject unsupported pricing/currency, settle acceptance and hold policy, test on isolated SQL Server.

### T-12 — View own reservation status/details and cancel

**PARTIALLY COMPLETED · 90%**. List/detail ownership and cancellation exist; dedicated schema omits cash/payment status and legacy cancellation eligibility differs; auth and stock release covered.

- **API and source** — [GET `/api/v1/artisan-reservations/me`](../../app/routers/artisan_reservations.py#L209), [GET `/api/v1/artisan-reservations/{id}`](../../app/routers/artisan_reservations.py#L278), [POST `/api/v1/artisan-reservations/{id}/cancel`](../../app/routers/artisan_reservations.py#L529), GET /api/v1/reservations/artisan_reservation/me; POST /api/v1/reservations/artisan_reservation/{booking_id}/cancel; GET /api/v1/provider/bookings and /{booking_id}; POST /api/v1/provider/bookings/{booking_id}/action (§6).
- **Data and migration** — `R`, `BR`, `PT` (§7). **Access** — traveler JWT; optional JWT for authorized preview where available; detail/cancel also allow authorized provider/admin.
- **Coverage** — test_artisan_reservations.py, test_provider_phase03.py; test_provider_phase03.py.
- **Remaining** — Normalize payment and cancellation contracts and verify 037 before rollout.

### T-13 — Read and submit ratings and moderated feedback

**PARTIALLY COMPLETED · 90%**. Eligibility, aggregates, ownership and moderation implemented/tested; public review predicate is weaker than catalog; concurrent duplicate rule needs proof.

- **API and source** — [POST `/api/v1/artisan-listings/{listing_id}/reviews`](../../app/routers/artisan_reviews.py#L120), [POST `/api/v1/artisan-reviews`](../../app/routers/artisan_reviews.py#L241), [GET `/api/v1/artisan-reviews/listing/{listing_id}`](../../app/routers/artisan_reviews.py#L289), [GET `/api/v1/artisan-listings/{listing_id}/reviews`](../../app/routers/artisan_reviews.py#L289), [GET `/api/v1/artisan-reviews/listing/{listing_id}/summary`](../../app/routers/artisan_reviews.py#L406), [GET `/api/v1/artisan-listings/{listing_id}/reviews/summary`](../../app/routers/artisan_reviews.py#L406), [GET `/api/v1/artisan-reviews/me`](../../app/routers/artisan_reviews.py#L486), [PUT `/api/v1/artisan-reviews/{review_id}`](../../app/routers/artisan_reviews.py#L524), [DELETE `/api/v1/artisan-reviews/{review_id}`](../../app/routers/artisan_reviews.py#L574), [PATCH `/api/v1/admin/artisan/reviews/{id}/publish`](../../app/routers/admin_artisan.py#L901).
- **Data and migration** — `L`, `LR`, `R`, `BR`, `REV` (§7). **Access** — public; traveler JWT; review author JWT and eligible reservation; admin JWT.
- **Coverage** — test_artisan_reviews.py; test_admin_artisan_review_moderation_p2_step16.py.
- **Remaining** — Align publication predicate and prove duplicate review behavior in SQL Server.

### T-14 — Save products and businesses

**IMPLEMENTED — VERIFICATION PENDING · 100%**. Both save flows, owner isolation and duplicate handling implemented; hidden products are removable; favorite endpoint tests pass offline; 038 deployment/SQL tests pending.

- **API and source** — [POST `/api/v1/artisan-listings/{listing_id}/wishlist`](../../app/routers/artisan_wishlists.py#L112), [DELETE `/api/v1/artisan-listings/{listing_id}/wishlist`](../../app/routers/artisan_wishlists.py#L219), [GET `/api/v1/artisan-listings/{listing_id}/wishlist/check`](../../app/routers/artisan_wishlists.py#L276), [GET `/api/v1/artisan-wishlists/me`](../../app/routers/artisan_wishlists.py#L330), [PUT `/api/v1/artisan-product-favorites/{product_id}`](../../app/routers/artisan_wishlists.py#L473), [DELETE `/api/v1/artisan-product-favorites/{product_id}`](../../app/routers/artisan_wishlists.py#L503), [GET `/api/v1/artisan-product-favorites`](../../app/routers/artisan_wishlists.py#L516).
- **Data and migration** — `W`, `LR`, `F`, `I`, `L` (§7). **Access** — traveler JWT.
- **Coverage** — test_artisan_wishlists.py.
- **Remaining** — Rehearse 038, verify database uniqueness and client migration from aliases.

### T-15 — Show badge only when admin verified

**IMPLEMENTED — VERIFICATION PENDING · 100%**. Admin-controlled boolean, provider edits cannot self-verify and location changes reset flag; map/listing tests exist; UI display unverified.

- **API and source** — [GET `/api/v1/artisan-listings`](../../app/routers/artisan_listings.py#L39), [GET `/api/v1/artisan-listings/{id}`](../../app/routers/artisan_listings.py#L292), [PATCH `/api/v1/artisan-listings/{id}/status`](../../app/routers/artisan_listings.py#L481).
- **Data and migration** — `L` (§7). **Access** — public; admin JWT.
- **Coverage** — test_artisan_listings.py, test_artisan_public_approval_filtering.py; test_artisan_listings.py, test_interactive_map.py.
- **Remaining** — Check badge consumption and reconcile bool with workflow status under P-18.

### T-16 — Show valid offers and discounted prices

**PARTIALLY COMPLETED · 65%**. Active offers and validated amounts exist; no resolved nonnegative sale price in product response or offer applied in reservation totals; promo visibility/date tests exist.

- **API and source** — [GET `/api/v1/showcase-items/{id}`](../../app/routers/showcase_items.py#L119), [GET `/api/v1/promotions`](../../app/routers/promotions.py#L31), [GET `/api/v1/promotions/{id}`](../../app/routers/promotions.py#L145), [GET `/api/v1/artisan-listings/{listing_id}/promotions`](../../app/routers/promotions.py#L186), [POST `/api/v1/artisan-reservations`](../../app/routers/provider_reservations.py#L326).
- **Data and migration** — `D`, `I`, `R` (§7). **Access** — public; traveler JWT.
- **Coverage** — test_showcase_items.py, test_artisan_public_approval_filtering.py; test_promotions.py, test_promotions_approval_security.py; test_artisan_reservations.py, test_provider_phase03.py.
- **Remaining** — Define offer priority/stacking, currency and server price preview/snapshot with floor zero.

### T-17 — Label and show paid sponsored placement

**NOT IMPLEMENTED · 0%**. Editorial features and status flag exist; no paid placement or payment-linked ranking; tests cover editorial behavior only.

- **API and source** — [GET `/api/v1/artisan-listings/featured`](../../app/routers/artisan_listings.py#L142), [GET `/api/v1/artisan-listings/sponsored-promotions-status`](../../app/routers/artisan_listings.py#L166).
- **Data and migration** — `L`, `SP` (§7). **Access** — public.
- **Coverage** — test_artisan_sponsored_promotions.py (editorial/capability only).
- **Remaining** — Approve proposals C/D and build relevance-aware labeled selection after payment verification.

### T-18 — Share public product/business link

**NOT IMPLEMENTED · 0%**. JSON lookup routes exist; no agreed canonical client deep-link/share workflow evidenced, no share acceptance tests.

- **API and source** — [GET `/api/v1/artisan-listings/{id}`](../../app/routers/artisan_listings.py#L292), [GET `/api/v1/showcase-items/{id}`](../../app/routers/showcase_items.py#L119).
- **Data and migration** — `L`, `I` (§7). **Access** — public.
- **Coverage** — test_artisan_listings.py, test_artisan_public_approval_filtering.py; test_showcase_items.py, test_artisan_public_approval_filtering.py.
- **Remaining** — Agree public URLs and deep links, verify share intents/auth-free opens and hidden resource handling.

## 4. Provider Feature Audit

### P-01 — Overview of business, products and reservations

**COMPLETED · 100%**. Provider-scoped counts and upcoming lists exist; empty/multi-listing and isolation coverage.

- **API and source** — [GET `/api/v1/artisan/dashboard`](../../app/routers/artisan_dashboard.py#L32), GET /api/v1/provider/workspace, /dashboard, /dashboard/analytics, /dashboard/recent-activity (§6).
- **Data and migration** — `L`, `I`, `R`, `LR`, `REV` (§7). **Access** — approved Artisan provider JWT and ownership where applicable.
- **Coverage** — test_artisan_dashboard.py; test_provider_workspace.py.
- **Remaining** — Verify Phase 03 status presentation in clients.

### P-02 — Create and update owned business profile

**COMPLETED · 100%**. Approved Artisan role, own CRUD, pending default and soft delete are tested.

- **API and source** — [GET `/api/v1/artisan-listings/me/listings`](../../app/routers/artisan_listings.py#L124), [POST `/api/v1/artisan-listings`](../../app/routers/artisan_listings.py#L344), [PUT `/api/v1/artisan-listings/{id}`](../../app/routers/artisan_listings.py#L390), [DELETE `/api/v1/artisan-listings/{id}`](../../app/routers/artisan_listings.py#L448), POST /api/v1/providers/registration/submit; POST /api/v1/admin/providers/{provider_id}/approve and /reject (§6).
- **Data and migration** — `L`, `LR`, `PR` (§7). **Access** — approved Artisan provider JWT and ownership where applicable.
- **Coverage** — test_artisan_listings.py, test_artisan_public_approval_filtering.py; test_provider_registration.py, test_admin_providers.py.
- **Remaining** — Smoke test frontend onboarding and catalog publishing as two distinct steps.

### P-03 — Maintain background/story and specialties

**PARTIALLY COMPLETED · 75%**. Free-text description and single craft category exist; no dedicated multi-specialty representation; CRUD covered, no separate story tests.

- **API and source** — [GET `/api/v1/artisan-listings/me/listings`](../../app/routers/artisan_listings.py#L124), [POST `/api/v1/artisan-listings`](../../app/routers/artisan_listings.py#L344), [PUT `/api/v1/artisan-listings/{id}`](../../app/routers/artisan_listings.py#L390).
- **Data and migration** — `L` (§7). **Access** — approved Artisan provider JWT and ownership where applicable.
- **Coverage** — test_artisan_listings.py, test_artisan_public_approval_filtering.py.
- **Remaining** — Agree whether a single craft category is sufficient; add explicit fields if required.

### P-04 — Create, edit and deactivate owned products

**COMPLETED · 100%**. CRUD and soft deactivation with role, ownership and anti-self-approval tests.

- **API and source** — [GET `/api/v1/showcase-items/me/items`](../../app/routers/showcase_items.py#L96), [POST `/api/v1/showcase-items`](../../app/routers/showcase_items.py#L212), [PUT `/api/v1/showcase-items/{id}`](../../app/routers/showcase_items.py#L271), [DELETE `/api/v1/showcase-items/{id}`](../../app/routers/showcase_items.py#L317).
- **Data and migration** — `I`, `L` (§7). **Access** — approved Artisan provider JWT and ownership where applicable.
- **Coverage** — test_showcase_items.py, test_artisan_public_approval_filtering.py.
- **Remaining** — Confirm whether material edits require re-moderation.

### P-05 — Upload, reorder and remove product photos

**COMPLETED · 100%**. Owner authorization, file validation, ordering, primary and remove implemented; gallery tests cover these actions.

- **API and source** — [POST `/api/v1/showcase-items/{item_id}/images`](../../app/routers/artisan_images.py#L526), [PATCH `/api/v1/showcase-items/{item_id}/images/reorder`](../../app/routers/artisan_images.py#L671), [PATCH `/api/v1/showcase-items/{item_id}/images/{image_id}/set-primary`](../../app/routers/artisan_images.py#L726), [PATCH `/api/v1/showcase-items/{item_id}/images/{image_id}`](../../app/routers/artisan_images.py#L777), [DELETE `/api/v1/showcase-items/{item_id}/images/{image_id}`](../../app/routers/artisan_images.py#L830).
- **Data and migration** — `PI`, `I` (§7). **Access** — approved Artisan provider JWT and ownership where applicable.
- **Coverage** — test_artisan_gallery.py.
- **Remaining** — Add deployed storage and upload throttling smoke coverage.

### P-06 — Set LKR prices

**PARTIALLY COMPLETED · 85%**. Decimal nonnegative price and default LKR; currency is free text up to ten chars and does not enforce LKR or reservation width; schema/API price tests exist.

- **API and source** — [POST `/api/v1/showcase-items`](../../app/routers/showcase_items.py#L212), [PUT `/api/v1/showcase-items/{id}`](../../app/routers/showcase_items.py#L271).
- **Data and migration** — `I`, `R` (§7). **Access** — approved Artisan provider JWT and ownership where applicable.
- **Coverage** — test_showcase_items.py, test_artisan_public_approval_filtering.py.
- **Remaining** — Enforce currency and decimal precision/limits across CRUD, offers and reservation.

### P-07 — Manage available/out-of-stock state

**PARTIALLY COMPLETED · 90%**. Flag and stock count exist, stock edits share SQL lock, checkout checks quantity; no uniform derived sold-out flag or acceptance-time invariant; stock tests exist.

- **API and source** — [POST `/api/v1/showcase-items`](../../app/routers/showcase_items.py#L212), [PUT `/api/v1/showcase-items/{id}`](../../app/routers/showcase_items.py#L271), [POST `/api/v1/artisan-reservations`](../../app/routers/provider_reservations.py#L326).
- **Data and migration** — `I`, `R` (§7). **Access** — approved Artisan provider JWT and ownership where applicable; traveler JWT.
- **Coverage** — test_showcase_items.py, test_artisan_public_approval_filtering.py; test_artisan_reservations.py, test_provider_phase03.py.
- **Remaining** — Agree stock semantics and test provider adjustment versus acceptance/expiry using SQL Server.

### P-08 — Assign approved product categories

**NOT IMPLEMENTED · 0%**. Business craft category and product/service discriminator exist; no approved product category relation, API or assignment test.

- **API and source** — [GET `/api/v1/artisan-listings/categories`](../../app/routers/artisan_listings.py#L93), [POST `/api/v1/showcase-items`](../../app/routers/showcase_items.py#L212), [PUT `/api/v1/showcase-items/{id}`](../../app/routers/showcase_items.py#L271).
- **Data and migration** — `I`, `L` (§7). **Access** — approved Artisan provider JWT and ownership where applicable.
- **Coverage** — test_artisan_listings.py, test_artisan_public_approval_filtering.py; test_showcase_items.py, test_artisan_public_approval_filtering.py.
- **Remaining** — Approve taxonomy, category management, assignment validation/filter and incremental migration.

### P-09 — Maintain address and coordinates

**COMPLETED · 100%**. Coordinate range/pair checks and verification reset on location edits; listing/map tests cover behavior.

- **API and source** — [POST `/api/v1/artisan-listings`](../../app/routers/artisan_listings.py#L344), [PUT `/api/v1/artisan-listings/{id}`](../../app/routers/artisan_listings.py#L390), GET /api/v1/map/locations, /nearby, /search, /categories; POST /api/v1/map/route (§6).
- **Data and migration** — `L` (§7). **Access** — approved Artisan provider JWT and ownership where applicable; admin JWT.
- **Coverage** — test_artisan_listings.py, test_artisan_public_approval_filtering.py; test_interactive_map.py.
- **Remaining** — Validate edits and readback in clients.

### P-10 — Maintain safe phone, WhatsApp and email

**PARTIALLY COMPLETED · 65%**. Email and phone accepted with length/trim; no WhatsApp and phone format is permissive; listing CRUD covered.

- **API and source** — [POST `/api/v1/artisan-listings`](../../app/routers/artisan_listings.py#L344), [PUT `/api/v1/artisan-listings/{id}`](../../app/routers/artisan_listings.py#L390).
- **Data and migration** — `L` (§7). **Access** — approved Artisan provider JWT and ownership where applicable.
- **Coverage** — test_artisan_listings.py, test_artisan_public_approval_filtering.py.
- **Remaining** — Decide WhatsApp number semantics and validate contact data rather than treating URLs as trusted actions.

### P-11 — Set weekly open/close and closure days

**COMPLETED · 100%**. Weekly/day writes, uniqueness, closure/time/order validation and ownership tested.

- **API and source** — [GET `/api/v1/artisan-listings/{listing_id}/business-hours`](../../app/routers/artisan_business_hours.py#L54), [POST `/api/v1/artisan-listings/{listing_id}/business-hours`](../../app/routers/artisan_business_hours.py#L116), [PUT `/api/v1/artisan-listings/{listing_id}/business-hours`](../../app/routers/artisan_business_hours.py#L159), [PUT `/api/v1/artisan-listings/{listing_id}/business-hours/{day_of_week}`](../../app/routers/artisan_business_hours.py#L208), [PATCH `/api/v1/artisan-listings/{listing_id}/business-hours/{day_of_week}`](../../app/routers/artisan_business_hours.py#L257), [DELETE `/api/v1/artisan-listings/{listing_id}/business-hours/{day_of_week}`](../../app/routers/artisan_business_hours.py#L320).
- **Data and migration** — `H`, `L` (§7). **Access** — approved Artisan provider JWT and ownership where applicable.
- **Coverage** — test_artisan_business_hours.py.
- **Remaining** — Confirm overnight and special-calendar scope with product owner.

### P-12 — Accept and reject owned pickup requests

**PARTIALLY COMPLETED · 90%**. Rejection audit, duplicate guard, atomic stock restore, booking status and notice implemented/tested offline; 037 not verified and accept paths differ on pickup/stock policy.

- **API and source** — [GET `/api/v1/artisan-reservations/provider`](../../app/routers/artisan_reservations.py#L238), [PATCH `/api/v1/artisan-reservations/{id}/confirm`](../../app/routers/artisan_reservations.py#L320), [POST `/api/v1/artisan-reservations/{id}/reject`](../../app/routers/artisan_reservations.py#L516), GET /api/v1/reservations/artisan_reservation/me; POST /api/v1/reservations/artisan_reservation/{booking_id}/cancel; GET /api/v1/provider/bookings and /{booking_id}; POST /api/v1/provider/bookings/{booking_id}/action (§6).
- **Data and migration** — `R`, `I`, `BR`, `BH`, `N`, `PT` (§7). **Access** — approved Artisan provider JWT and ownership where applicable.
- **Coverage** — test_artisan_reservations.py, test_provider_phase03.py; test_artisan_reservation_rejection_future_contracts.py, test_provider_phase03.py; test_provider_phase03.py.
- **Remaining** — Rehearse 037 and unify acceptance, expiry and payment-state guards.

### P-13 — Mark collected and cash paid

**PARTIALLY COMPLETED · 85%**. Dedicated completion can record cash and shared actions record cash then complete; dedicated ready permits pending and pickup-date gating differs; lifecycle and cash tests exist.

- **API and source** — [PATCH `/api/v1/artisan-reservations/{id}/ready`](../../app/routers/artisan_reservations.py#L379), [POST `/api/v1/artisan-reservations/{id}/complete-pickup`](../../app/routers/artisan_reservations.py#L438), GET /api/v1/reservations/artisan_reservation/me; POST /api/v1/reservations/artisan_reservation/{booking_id}/cancel; GET /api/v1/provider/bookings and /{booking_id}; POST /api/v1/provider/bookings/{booking_id}/action (§6).
- **Data and migration** — `R`, `BR`, `PT` (§7). **Access** — approved Artisan provider JWT and ownership where applicable.
- **Coverage** — test_artisan_reservations.py, test_provider_phase03.py; test_provider_phase03.py.
- **Remaining** — Enforce documented sequence and consistent cash readback; reconcile zero-price and timing semantics.

### P-14 — Manage dated discounts and special offers

**COMPLETED · 100%**. Ownership, active listing/item checks, exclusive discount types, ranges and dates tested; resolved traveler price is T-16 gap.

- **API and source** — [GET `/api/v1/promotions/me/items`](../../app/routers/promotions.py#L111), [POST `/api/v1/promotions`](../../app/routers/promotions.py#L251), [PUT `/api/v1/promotions/{id}`](../../app/routers/promotions.py#L337), [DELETE `/api/v1/promotions/{id}`](../../app/routers/promotions.py#L425).
- **Data and migration** — `D`, `L`, `I` (§7). **Access** — approved Artisan provider JWT and ownership where applicable.
- **Coverage** — test_promotions.py, test_promotions_approval_security.py.
- **Remaining** — Confirm provider UI status and inactive offers.

### P-15 — Purchase/request paid sponsorship

**NOT IMPLEMENTED · 0%**. No Artisan package selection, quote, campaign request or payment initiation; capability/negative tests only.

- **API and source** — [GET `/api/v1/artisan-listings/sponsored-promotions-status`](../../app/routers/artisan_listings.py#L166), POST /api/v1/payments/payhere/notify (shared only; no Artisan ad handler) (§6).
- **Data and migration** — `SP`, `LR` (§7). **Access** — approved Artisan provider JWT and ownership where applicable.
- **Coverage** — test_artisan_sponsored_promotions.py (editorial/capability only); test_artisan_sponsorship_security_contracts.py (negative/design only).
- **Remaining** — Approve commercial policies and reuse shared infrastructure via an Artisan-specific state machine.

### P-16 — View promotion views, clicks and inquiries where tracked

**NOT IMPLEMENTED · 0%**. Operational dashboard is present; no Artisan sponsored event capture or campaign statistics; no positive analytics tests.

- **API and source** — [GET `/api/v1/artisan/dashboard`](../../app/routers/artisan_dashboard.py#L32), GET /api/v1/provider/workspace, /dashboard, /dashboard/analytics, /dashboard/recent-activity (§6).
- **Data and migration** — `SP` (§7). **Access** — approved Artisan provider JWT and ownership where applicable.
- **Coverage** — test_artisan_dashboard.py; test_provider_workspace.py.
- **Remaining** — Approve event attribution, deduplication, retention, aggregation and tenant isolation.

### P-17 — View customer feedback

**PARTIALLY COMPLETED · 80%**. Published reviews/summary are readable; no dedicated evidenced owner feedback view for nonpublic/in-review content; listing review tests exist.

- **API and source** — [GET `/api/v1/artisan-reviews/listing/{listing_id}`](../../app/routers/artisan_reviews.py#L289), [GET `/api/v1/artisan-listings/{listing_id}/reviews`](../../app/routers/artisan_reviews.py#L289), [GET `/api/v1/artisan-reviews/listing/{listing_id}/summary`](../../app/routers/artisan_reviews.py#L406), [GET `/api/v1/artisan-listings/{listing_id}/reviews/summary`](../../app/routers/artisan_reviews.py#L406), GET /api/v1/provider/workspace, /dashboard, /dashboard/analytics, /dashboard/recent-activity (§6).
- **Data and migration** — `REV`, `LR`, `L` (§7). **Access** — public; approved Artisan provider JWT and ownership where applicable.
- **Coverage** — test_artisan_reviews.py; test_provider_workspace.py.
- **Remaining** — Confirm provider visibility policy and dashboard workflow; add own-listing feedback query if accepted.

### P-18 — See approval and verification state

**PARTIALLY COMPLETED · 90%**. Owner response exposes approval and verification fields; independent bool/status can conflict, dashboard is aggregate; listing/admin tests cover exposure and protection.

- **API and source** — [GET `/api/v1/artisan-listings/me/listings`](../../app/routers/artisan_listings.py#L124), [PATCH `/api/v1/artisan-listings/{id}/status`](../../app/routers/artisan_listings.py#L481), [GET `/api/v1/artisan/dashboard`](../../app/routers/artisan_dashboard.py#L32).
- **Data and migration** — `L`, `PR` (§7). **Access** — approved Artisan provider JWT and ownership where applicable; admin JWT.
- **Coverage** — test_artisan_listings.py, test_artisan_public_approval_filtering.py; test_artisan_listings.py, test_interactive_map.py; test_artisan_dashboard.py.
- **Remaining** — Define one source of truth and test transitions, re-verification, and rejected messages.

## 5. Admin Feature Audit

### A-01 — Approve or reject submitted business profiles

**COMPLETED · 100%**. Admin queue, decision actor/time and rejection reason exist, publish gates tested.

- **API and source** — [GET `/api/v1/admin/artisan/listings`](../../app/routers/admin_artisan.py#L61), [GET `/api/v1/admin/artisan/listings/{id}`](../../app/routers/admin_artisan.py#L105), [POST `/api/v1/admin/artisan/listings/{id}/approve`](../../app/routers/admin_artisan.py#L126), [POST `/api/v1/admin/artisan/listings/{id}/reject`](../../app/routers/admin_artisan.py#L173), POST /api/v1/providers/registration/submit; POST /api/v1/admin/providers/{provider_id}/approve and /reject (§6).
- **Data and migration** — `L`, `PR`, `LR` (§7). **Access** — admin JWT.
- **Coverage** — test_admin_artisan_moderation.py; test_provider_registration.py, test_admin_providers.py.
- **Remaining** — Verify approval/verification UI wording and permissions.

### A-02 — Grant/revoke verified badge with audit

**PARTIALLY COMPLETED · 85%**. Admin bool and stale location snapshot guard implemented/tested; toggle alone does not synchronize verification_status or record dedicated decision history.

- **API and source** — [PATCH `/api/v1/artisan-listings/{id}/status`](../../app/routers/artisan_listings.py#L481), GET /api/v1/map/locations, /nearby, /search, /categories; POST /api/v1/map/route (§6).
- **Data and migration** — `L`, `AL` (§7). **Access** — admin JWT.
- **Coverage** — test_artisan_listings.py, test_interactive_map.py; test_interactive_map.py.
- **Remaining** — Define atomic verified/status sync and append audit actor, reason and timestamp.

### A-03 — Hide or flag inappropriate products

**PARTIALLY COMPLETED · 90%**. Hide/approve/reject and pending visibility tested; no durable product reviewer/reason history evidenced in model/handlers.

- **API and source** — [GET `/api/v1/admin/artisan/products`](../../app/routers/admin_artisan.py#L342), [POST `/api/v1/admin/artisan/products/{id}/approve`](../../app/routers/admin_artisan.py#L374), [POST `/api/v1/admin/artisan/products/{id}/reject`](../../app/routers/admin_artisan.py#L417), [POST `/api/v1/admin/artisan/products/{id}/hide`](../../app/routers/admin_artisan.py#L457).
- **Data and migration** — `I`, `L`, `AL` (§7). **Access** — admin JWT.
- **Coverage** — test_admin_artisan_moderation.py.
- **Remaining** — Persist reason and decision history; verify no access through media links after hide.

### A-04 — Manage ad packages, activation and expiry

**NOT IMPLEMENTED · 0%**. Manual editorial toggle only; no Artisan package/campaign moderation, activation or expiry workflow.

- **API and source** — [GET `/api/v1/artisan-listings/sponsored-promotions-status`](../../app/routers/artisan_listings.py#L166), [POST `/api/v1/admin/artisan/listings/{id}/feature`](../../app/routers/admin_artisan.py#L261), [POST `/api/v1/admin/artisan/listings/{id}/unfeature`](../../app/routers/admin_artisan.py#L303), POST /api/v1/payments/payhere/notify (shared only; no Artisan ad handler) (§6).
- **Data and migration** — `SP`, `AL` (§7). **Access** — admin JWT.
- **Coverage** — test_artisan_sponsored_promotions.py (editorial/capability only); test_artisan_sponsorship_security_contracts.py (negative/design only).
- **Remaining** — Implement approved campaign/ledger flow after C/D approval.

### A-05 — Verify promotion payment

**NOT IMPLEMENTED · 0%**. Other-module PayHere webhook/receipts only; generic AD handler resolves Homestay; no Artisan checkout or verification handler; no successful Artisan transaction tests.

- **API and source** — POST /api/v1/payments/payhere/notify (shared only; no Artisan ad handler) (§6).
- **Data and migration** — `SP`, `PT`, `AL` (§7). **Access** — admin JWT; gateway signature where applicable.
- **Coverage** — test_artisan_sponsorship_security_contracts.py (negative/design only).
- **Remaining** — Add signed, locked, idempotent Artisan payment handler and reconciliation only after approval.

### A-06 — Suspend business and remove public visibility

**PARTIALLY COMPLETED · 90%**. Catalog removal and unfeature are tested; known direct assets remain public and review visibility predicates need parity.

- **API and source** — [GET `/api/v1/artisan-listings/{id}`](../../app/routers/artisan_listings.py#L292), [PATCH `/api/v1/artisan-listings/{id}/status`](../../app/routers/artisan_listings.py#L481), [GET `/uploads/artisan/business/{filename}`](../../app/routers/artisan_images.py#L902), [GET `/uploads/artisan/products/{filename}`](../../app/routers/artisan_images.py#L922), [POST `/api/v1/admin/artisan/listings/{id}/suspend`](../../app/routers/admin_artisan.py#L217).
- **Data and migration** — `L`, `LR`, `BI`, `PI`, `REV` (§7). **Access** — admin JWT; public.
- **Coverage** — test_artisan_listings.py, test_artisan_public_approval_filtering.py; test_artisan_listings.py, test_interactive_map.py; test_artisan_gallery.py; test_admin_artisan_moderation.py.
- **Remaining** — Test every public surface and explicitly define historical and media access.

### A-07 — Basic business, product, reservation and promotion reports

**PARTIALLY COMPLETED · 90%**. Four domains and CSV sanitization tested; promotion results are discounts, no paid ledger and rejected state not separately counted in summary.

- **API and source** — [GET `/api/v1/admin/artisan/reports/summary`](../../app/routers/admin_artisan.py#L537), [GET `/api/v1/admin/artisan/reports/export/{report_type}`](../../app/routers/admin_artisan.py#L639).
- **Data and migration** — `L`, `I`, `R`, `D`, `SP` (§7). **Access** — admin JWT.
- **Coverage** — test_admin_artisan_reports_p2_step16.py.
- **Remaining** — Add rejected aggregation and, after sponsorship approval, paid metrics and reconciliation checks.

## 6. Complete API Inventory

**104 registered Artisan-owned method/path entries**, with 89 documented operations and 15 hidden compatibility routes. They resolve to **87 distinct handlers**. This includes `/promotions`, whose handlers bind to Artisan listings, and the uniquely Artisan create route in the shared reservation router. The **entire backend** has **429 OpenAPI paths and 516 distinct documented operations**, including the 89 Artisan operations. Shared routes below are already part of the backend totals and are excluded from the Artisan counts. A path with different methods counts once per method. Alternate paths never increase the feature count.

Inventory “Implemented” describes handler presence with family coverage. Feature-level completion and limitations are in §§3–5. Family citations do not establish that each alias or response code has an individually asserted test.

| Method and route | Purpose and source | Role and authentication | Status | Existing test evidence |
| --- | --- | --- | --- | --- |
| `GET /api/v1/artisan-listings` | [list artisan listings](../../app/routers/artisan_listings.py#L39) | Public / none | Implemented | test_artisan_listings.py, test_artisan_public_approval_filtering.py |
| `GET /api/v1/artisan-listings/categories` | [get craft categories](../../app/routers/artisan_listings.py#L93) | Public / none | Implemented | test_artisan_listings.py, test_artisan_public_approval_filtering.py |
| `GET /api/v1/artisan-listings/districts` | [get active districts](../../app/routers/artisan_listings.py#L109) | Public / none | Implemented | test_artisan_listings.py, test_artisan_public_approval_filtering.py |
| `GET /api/v1/artisan-listings/me/listings` | [get my artisan listings](../../app/routers/artisan_listings.py#L124) | Approved Artisan provider / JWT + owner for writes | Implemented | test_artisan_listings.py, test_artisan_public_approval_filtering.py |
| `GET /api/v1/artisan-listings/featured` | [list featured artisan listings](../../app/routers/artisan_listings.py#L142) | Public / none | Implemented | test_artisan_sponsored_promotions.py (editorial/capability only) |
| `GET /api/v1/artisan-listings/sponsored-promotions-status` | [get sponsored promotions status](../../app/routers/artisan_listings.py#L166) | Public / none | Capability only | test_artisan_sponsored_promotions.py (editorial/capability only) |
| `GET /api/v1/artisan-listings/nearby` | [list nearby artisan listings](../../app/routers/artisan_listings.py#L203) | Public / none | Implemented / acceptance caveat | test_interactive_map.py (shared; direct path coverage not confirmed) |
| `GET /api/v1/artisan-listings/{id}` | [get artisan listing](../../app/routers/artisan_listings.py#L292) | Public / none | Implemented | test_artisan_listings.py, test_artisan_public_approval_filtering.py |
| `POST /api/v1/artisan-listings` | [create artisan listing](../../app/routers/artisan_listings.py#L344) | Approved Artisan provider / JWT + owner for writes | Implemented | test_artisan_listings.py, test_artisan_public_approval_filtering.py |
| `PUT /api/v1/artisan-listings/{id}` | [update artisan listing](../../app/routers/artisan_listings.py#L390) | Approved Artisan provider / JWT + owner for writes | Implemented | test_artisan_listings.py, test_artisan_public_approval_filtering.py |
| `DELETE /api/v1/artisan-listings/{id}` | [delete artisan listing](../../app/routers/artisan_listings.py#L448) | Approved Artisan provider / JWT + owner for writes | Implemented | test_artisan_listings.py, test_artisan_public_approval_filtering.py |
| `PATCH /api/v1/artisan-listings/{id}/status` | [update listing status](../../app/routers/artisan_listings.py#L481) | Admin / JWT | Implemented | test_artisan_listings.py, test_interactive_map.py |
| `GET /api/v1/showcase-items` | [list showcase items](../../app/routers/showcase_items.py#L30) | Public / none | Implemented | test_showcase_items.py, test_artisan_public_approval_filtering.py |
| `GET /api/v1/showcase-items/me/items` | [get my showcase items](../../app/routers/showcase_items.py#L96) | Approved Artisan provider / JWT + owner for writes | Implemented | test_showcase_items.py, test_artisan_public_approval_filtering.py |
| `GET /api/v1/showcase-items/{id}` | [get showcase item](../../app/routers/showcase_items.py#L119) | Public / none | Implemented | test_showcase_items.py, test_artisan_public_approval_filtering.py |
| `GET /api/v1/artisan-listings/{listing_id}/showcase-items` | [get listing showcase items](../../app/routers/showcase_items.py#L150) | Public / none | Implemented | test_showcase_items.py, test_artisan_public_approval_filtering.py |
| `POST /api/v1/showcase-items` | [create showcase item](../../app/routers/showcase_items.py#L212) | Approved Artisan provider / JWT + owner for writes | Implemented | test_showcase_items.py, test_artisan_public_approval_filtering.py |
| `PUT /api/v1/showcase-items/{id}` | [update showcase item](../../app/routers/showcase_items.py#L271) | Approved Artisan provider / JWT + owner for writes | Implemented | test_showcase_items.py, test_artisan_public_approval_filtering.py |
| `DELETE /api/v1/showcase-items/{id}` | [delete showcase item](../../app/routers/showcase_items.py#L317) | Approved Artisan provider / JWT + owner for writes | Implemented | test_showcase_items.py, test_artisan_public_approval_filtering.py |
| `POST /api/v1/artisan-listings/{listing_id}/images` | [upload artisan business image](../../app/routers/artisan_images.py#L166) | Approved Artisan provider / JWT + owner for writes | Implemented | test_artisan_gallery.py |
| `GET /api/v1/artisan-listings/{listing_id}/images` | [list artisan business images](../../app/routers/artisan_images.py#L260) | Public / optional JWT for authorized preview | Implemented / acceptance caveat | test_artisan_gallery.py |
| `PATCH /api/v1/artisan-listings/{listing_id}/images/reorder` | [reorder artisan business images](../../app/routers/artisan_images.py#L303) | Approved Artisan provider / JWT + owner for writes | Implemented | test_artisan_gallery.py |
| `PUT /api/v1/artisan-listings/{listing_id}/images/{image_id}/set-primary` | [set primary artisan business image](../../app/routers/artisan_images.py#L356) | Approved Artisan provider / JWT + owner for writes | Implemented alias · hidden | test_artisan_gallery.py |
| `PATCH /api/v1/artisan-listings/{listing_id}/images/{image_id}/set-primary` | [set primary artisan business image](../../app/routers/artisan_images.py#L356) | Approved Artisan provider / JWT + owner for writes | Implemented | test_artisan_gallery.py |
| `PATCH /api/v1/artisan-listings/{listing_id}/images/{image_id}` | [update artisan business image metadata](../../app/routers/artisan_images.py#L405) | Approved Artisan provider / JWT + owner for writes | Implemented | test_artisan_gallery.py |
| `DELETE /api/v1/artisan-listings/{listing_id}/images/{image_id}` | [delete artisan business image](../../app/routers/artisan_images.py#L456) | Approved Artisan provider / JWT + owner for writes | Implemented | test_artisan_gallery.py |
| `POST /api/v1/showcase-items/{item_id}/images` | [upload artisan product image](../../app/routers/artisan_images.py#L526) | Approved Artisan provider / JWT + owner for writes | Implemented | test_artisan_gallery.py |
| `GET /api/v1/showcase-items/{item_id}/images` | [list artisan product images](../../app/routers/artisan_images.py#L622) | Public / optional JWT for authorized preview | Implemented / acceptance caveat | test_artisan_gallery.py |
| `PATCH /api/v1/showcase-items/{item_id}/images/reorder` | [reorder artisan product images](../../app/routers/artisan_images.py#L671) | Approved Artisan provider / JWT + owner for writes | Implemented | test_artisan_gallery.py |
| `PUT /api/v1/showcase-items/{item_id}/images/{image_id}/set-primary` | [set primary artisan product image](../../app/routers/artisan_images.py#L726) | Approved Artisan provider / JWT + owner for writes | Implemented alias · hidden | test_artisan_gallery.py |
| `PATCH /api/v1/showcase-items/{item_id}/images/{image_id}/set-primary` | [set primary artisan product image](../../app/routers/artisan_images.py#L726) | Approved Artisan provider / JWT + owner for writes | Implemented | test_artisan_gallery.py |
| `PATCH /api/v1/showcase-items/{item_id}/images/{image_id}` | [update artisan product image metadata](../../app/routers/artisan_images.py#L777) | Approved Artisan provider / JWT + owner for writes | Implemented | test_artisan_gallery.py |
| `DELETE /api/v1/showcase-items/{item_id}/images/{image_id}` | [delete artisan product image](../../app/routers/artisan_images.py#L830) | Approved Artisan provider / JWT + owner for writes | Implemented | test_artisan_gallery.py |
| `GET /api/v1/uploads/artisan/business/{filename}` | [serve artisan business image](../../app/routers/artisan_images.py#L902) | Public / none | Implemented / acceptance caveat · hidden | test_artisan_gallery.py |
| `GET /api/v1/uploads/artisan/products/{filename}` | [serve artisan product image](../../app/routers/artisan_images.py#L922) | Public / none | Implemented / acceptance caveat · hidden | test_artisan_gallery.py |
| `GET /uploads/artisan/business/{filename}` | [serve artisan business image](../../app/routers/artisan_images.py#L902) | Public / none | Implemented / acceptance caveat | test_artisan_gallery.py |
| `GET /uploads/artisan/products/{filename}` | [serve artisan product image](../../app/routers/artisan_images.py#L922) | Public / none | Implemented / acceptance caveat | test_artisan_gallery.py |
| `GET /api/v1/artisan-listings/{listing_id}/opening-hours` | [get artisan business hours](../../app/routers/artisan_business_hours.py#L54) | Public / optional JWT for authorized preview | Implemented / acceptance caveat · hidden | test_artisan_business_hours.py |
| `GET /api/v1/artisan-listings/{listing_id}/business-hours` | [get artisan business hours](../../app/routers/artisan_business_hours.py#L54) | Public / optional JWT for authorized preview | Implemented / acceptance caveat | test_artisan_business_hours.py |
| `POST /api/v1/artisan-listings/{listing_id}/business-hours` | [create artisan business hours](../../app/routers/artisan_business_hours.py#L116) | Approved Artisan provider / JWT + owner for writes | Implemented | test_artisan_business_hours.py |
| `PUT /api/v1/artisan-listings/{listing_id}/business-hours` | [set weekly business hours](../../app/routers/artisan_business_hours.py#L159) | Approved Artisan provider / JWT + owner for writes | Implemented | test_artisan_business_hours.py |
| `PUT /api/v1/artisan-listings/{listing_id}/business-hours/{day_of_week}` | [update or set day schedule](../../app/routers/artisan_business_hours.py#L208) | Approved Artisan provider / JWT + owner for writes | Implemented | test_artisan_business_hours.py |
| `PATCH /api/v1/artisan-listings/{listing_id}/business-hours/{day_of_week}` | [patch day schedule](../../app/routers/artisan_business_hours.py#L257) | Approved Artisan provider / JWT + owner for writes | Implemented | test_artisan_business_hours.py |
| `DELETE /api/v1/artisan-listings/{listing_id}/business-hours/{day_of_week}` | [delete artisan business hours](../../app/routers/artisan_business_hours.py#L320) | Approved Artisan provider / JWT + owner for writes | Implemented | test_artisan_business_hours.py |
| `GET /api/v1/artisan-dashboard` | [get artisan dashboard](../../app/routers/artisan_dashboard.py#L32) | Approved Artisan provider / JWT + owner for writes | Implemented alias · hidden | test_artisan_dashboard.py |
| `GET /api/v1/artisan-listings/me/dashboard` | [get artisan dashboard](../../app/routers/artisan_dashboard.py#L32) | Approved Artisan provider / JWT + owner for writes | Implemented alias · hidden | test_artisan_dashboard.py |
| `GET /api/v1/artisan/dashboard` | [get artisan dashboard](../../app/routers/artisan_dashboard.py#L32) | Approved Artisan provider / JWT + owner for writes | Implemented | test_artisan_dashboard.py |
| `POST /api/v1/artisan-listings/{listing_id}/reviews` | [create artisan listing review](../../app/routers/artisan_reviews.py#L120) | Authenticated / JWT + record access | Implemented | test_artisan_reviews.py |
| `POST /api/v1/artisan-reviews` | [create artisan direct review](../../app/routers/artisan_reviews.py#L241) | Authenticated / JWT + record access | Implemented | test_artisan_reviews.py |
| `GET /api/v1/artisan-reviews/listing/{listing_id}` | [get artisan listing reviews](../../app/routers/artisan_reviews.py#L289) | Public / none | Implemented / acceptance caveat | test_artisan_reviews.py |
| `GET /api/v1/artisan-listings/{listing_id}/reviews` | [get artisan listing reviews](../../app/routers/artisan_reviews.py#L289) | Public / none | Implemented / acceptance caveat | test_artisan_reviews.py |
| `GET /api/v1/artisan-reviews/listing/{listing_id}/summary` | [get artisan listing reviews summary](../../app/routers/artisan_reviews.py#L406) | Public / none | Implemented / acceptance caveat | test_artisan_reviews.py |
| `GET /api/v1/artisan-listings/{listing_id}/reviews/summary` | [get artisan listing reviews summary](../../app/routers/artisan_reviews.py#L406) | Public / none | Implemented / acceptance caveat | test_artisan_reviews.py |
| `GET /api/v1/artisan-reviews/me` | [get my artisan reviews](../../app/routers/artisan_reviews.py#L486) | Authenticated / JWT + record access | Implemented | test_artisan_reviews.py |
| `PUT /api/v1/artisan-reviews/{review_id}` | [update artisan review](../../app/routers/artisan_reviews.py#L524) | Authenticated / JWT + record access | Implemented | test_artisan_reviews.py |
| `DELETE /api/v1/artisan-reviews/{review_id}` | [delete artisan review](../../app/routers/artisan_reviews.py#L574) | Authenticated / JWT + record access | Implemented | test_artisan_reviews.py |
| `POST /api/v1/wishlists/artisan/{listing_id}` | [add artisan listing to wishlist](../../app/routers/artisan_wishlists.py#L112) | Traveler / JWT | Implemented alias · hidden | test_artisan_wishlists.py |
| `POST /api/v1/artisan-listings/{listing_id}/wishlist` | [add artisan listing to wishlist](../../app/routers/artisan_wishlists.py#L112) | Traveler / JWT | Implemented | test_artisan_wishlists.py |
| `DELETE /api/v1/wishlists/artisan/{listing_id}` | [remove artisan listing from wishlist](../../app/routers/artisan_wishlists.py#L219) | Traveler / JWT | Implemented alias · hidden | test_artisan_wishlists.py |
| `DELETE /api/v1/artisan-listings/{listing_id}/wishlist` | [remove artisan listing from wishlist](../../app/routers/artisan_wishlists.py#L219) | Traveler / JWT | Implemented | test_artisan_wishlists.py |
| `GET /api/v1/wishlists/artisan/{listing_id}/check` | [check artisan listing wishlist status](../../app/routers/artisan_wishlists.py#L276) | Traveler / JWT | Implemented alias · hidden | test_artisan_wishlists.py |
| `GET /api/v1/artisan-listings/{listing_id}/wishlist/check` | [check artisan listing wishlist status](../../app/routers/artisan_wishlists.py#L276) | Traveler / JWT | Implemented | test_artisan_wishlists.py |
| `GET /api/v1/wishlists/artisan/me` | [list my artisan wishlist](../../app/routers/artisan_wishlists.py#L330) | Traveler / JWT | Implemented alias · hidden | test_artisan_wishlists.py |
| `GET /api/v1/artisan-listings/me/wishlist` | [list my artisan wishlist](../../app/routers/artisan_wishlists.py#L330) | Traveler / JWT | Implemented alias · hidden | test_artisan_wishlists.py |
| `GET /api/v1/artisan-wishlists/me` | [list my artisan wishlist](../../app/routers/artisan_wishlists.py#L330) | Traveler / JWT | Implemented | test_artisan_wishlists.py |
| `GET /api/v1/artisan-wishlists/product-favorites-compatibility` | [get product favorites compatibility](../../app/routers/artisan_wishlists.py#L461) | Public / none | Capability only | test_artisan_wishlists.py |
| `POST /api/v1/wishlists/artisan/products/{product_id}` | [add product favorite](../../app/routers/artisan_wishlists.py#L473) | Traveler / JWT | Implemented / pending SQL · hidden | test_artisan_wishlists.py |
| `POST /api/v1/artisan-products/{product_id}/favorite` | [add product favorite](../../app/routers/artisan_wishlists.py#L473) | Traveler / JWT | Implemented / pending SQL · hidden | test_artisan_wishlists.py |
| `PUT /api/v1/artisan-product-favorites/{product_id}` | [add product favorite](../../app/routers/artisan_wishlists.py#L473) | Traveler / JWT | Implemented / pending SQL | test_artisan_wishlists.py |
| `DELETE /api/v1/artisan-product-favorites/{product_id}` | [remove product favorite](../../app/routers/artisan_wishlists.py#L503) | Traveler / JWT | Implemented / pending SQL | test_artisan_wishlists.py |
| `GET /api/v1/artisan-product-favorites` | [list product favorites](../../app/routers/artisan_wishlists.py#L516) | Traveler / JWT | Implemented / pending SQL | test_artisan_wishlists.py |
| `GET /api/v1/artisan-reservations/me` | [get my reservations](../../app/routers/artisan_reservations.py#L209) | Traveler / JWT | Implemented | test_artisan_reservations.py, test_provider_phase03.py |
| `GET /api/v1/artisan-reservations/provider` | [get provider reservations](../../app/routers/artisan_reservations.py#L238) | Approved Artisan provider / JWT + owner for writes | Implemented | test_artisan_reservations.py, test_provider_phase03.py |
| `GET /api/v1/artisan-reservations/{id}` | [get artisan reservation](../../app/routers/artisan_reservations.py#L278) | Authenticated / JWT + record access | Implemented | test_artisan_reservations.py, test_provider_phase03.py |
| `PATCH /api/v1/artisan-reservations/{id}/confirm` | [confirm reservation](../../app/routers/artisan_reservations.py#L320) | Approved Artisan provider / JWT + owner for writes | Implemented / acceptance caveat | test_artisan_reservations.py, test_provider_phase03.py |
| `PATCH /api/v1/artisan-reservations/{id}/ready` | [ready for pickup](../../app/routers/artisan_reservations.py#L379) | Approved Artisan provider / JWT + owner for writes | Implemented / acceptance caveat | test_artisan_reservations.py, test_provider_phase03.py |
| `POST /api/v1/artisan-reservations/{id}/complete-pickup` | [complete pickup](../../app/routers/artisan_reservations.py#L438) | Approved Artisan provider / JWT + owner for writes | Implemented / acceptance caveat | test_artisan_reservations.py, test_provider_phase03.py |
| `POST /api/v1/artisan-reservations/{id}/reject` | [reject reservation](../../app/routers/artisan_reservations.py#L516) | Approved Artisan provider / JWT + owner for writes | Implemented / pending SQL | test_artisan_reservation_rejection_future_contracts.py, test_provider_phase03.py |
| `POST /api/v1/artisan-reservations/{id}/cancel` | [cancel reservation](../../app/routers/artisan_reservations.py#L529) | Authenticated / JWT + record access | Implemented | test_artisan_reservations.py, test_provider_phase03.py |
| `GET /api/v1/admin/artisan/listings` | [list artisan listings for review](../../app/routers/admin_artisan.py#L61) | Admin / JWT | Implemented | test_admin_artisan_moderation.py |
| `GET /api/v1/admin/artisan/listings/{id}` | [get artisan listing for review](../../app/routers/admin_artisan.py#L105) | Admin / JWT | Implemented | test_admin_artisan_moderation.py |
| `POST /api/v1/admin/artisan/listings/{id}/approve` | [approve artisan listing](../../app/routers/admin_artisan.py#L126) | Admin / JWT | Implemented | test_admin_artisan_moderation.py |
| `POST /api/v1/admin/artisan/listings/{id}/reject` | [reject artisan listing](../../app/routers/admin_artisan.py#L173) | Admin / JWT | Implemented | test_admin_artisan_moderation.py |
| `POST /api/v1/admin/artisan/listings/{id}/suspend` | [suspend artisan listing](../../app/routers/admin_artisan.py#L217) | Admin / JWT | Implemented | test_admin_artisan_moderation.py |
| `POST /api/v1/admin/artisan/listings/{id}/feature` | [feature artisan listing](../../app/routers/admin_artisan.py#L261) | Admin / JWT | Implemented | test_artisan_sponsored_promotions.py (editorial/capability only) |
| `POST /api/v1/admin/artisan/listings/{id}/unfeature` | [unfeature artisan listing](../../app/routers/admin_artisan.py#L303) | Admin / JWT | Implemented | test_artisan_sponsored_promotions.py (editorial/capability only) |
| `GET /api/v1/admin/artisan/products` | [list showcase products for review](../../app/routers/admin_artisan.py#L342) | Admin / JWT | Implemented | test_admin_artisan_moderation.py |
| `POST /api/v1/admin/artisan/products/{id}/approve` | [approve showcase product](../../app/routers/admin_artisan.py#L374) | Admin / JWT | Implemented | test_admin_artisan_moderation.py |
| `POST /api/v1/admin/artisan/products/{id}/reject` | [reject showcase product](../../app/routers/admin_artisan.py#L417) | Admin / JWT | Implemented | test_admin_artisan_moderation.py |
| `POST /api/v1/admin/artisan/products/{id}/hide` | [hide showcase product](../../app/routers/admin_artisan.py#L457) | Admin / JWT | Implemented | test_admin_artisan_moderation.py |
| `POST /api/v1/admin/artisan/reservations/process-expired` | [admin process expired reservations](../../app/routers/admin_artisan.py#L497) | Admin / JWT | Implemented | test_artisan_reservation_expiry.py |
| `GET /api/v1/admin/artisan/reports/summary` | [get admin reports summary](../../app/routers/admin_artisan.py#L537) | Admin / JWT | Implemented | test_admin_artisan_reports_p2_step16.py |
| `GET /api/v1/admin/artisan/reports/export/{report_type}` | [export admin report csv](../../app/routers/admin_artisan.py#L639) | Admin / JWT | Implemented | test_admin_artisan_reports_p2_step16.py |
| `GET /api/v1/admin/artisan/reviews` | [list artisan reviews for moderation](../../app/routers/admin_artisan.py#L830) | Admin / JWT | Implemented | test_admin_artisan_review_moderation_p2_step16.py |
| `PATCH /api/v1/admin/artisan/reviews/{id}/publish` | [moderate artisan review](../../app/routers/admin_artisan.py#L901) | Admin / JWT | Implemented | test_admin_artisan_review_moderation_p2_step16.py |
| `GET /api/v1/promotions` | [list promotions](../../app/routers/promotions.py#L31) | Public / none | Implemented | test_promotions.py, test_promotions_approval_security.py |
| `GET /api/v1/promotions/me/promotions` | [get my promotions](../../app/routers/promotions.py#L111) | Approved Artisan provider / JWT + owner for writes | Implemented alias · hidden | test_promotions.py, test_promotions_approval_security.py |
| `GET /api/v1/promotions/me/items` | [get my promotions](../../app/routers/promotions.py#L111) | Approved Artisan provider / JWT + owner for writes | Implemented | test_promotions.py, test_promotions_approval_security.py |
| `GET /api/v1/promotions/{id}` | [get promotion](../../app/routers/promotions.py#L145) | Public / none | Implemented | test_promotions.py, test_promotions_approval_security.py |
| `GET /api/v1/artisan-listings/{listing_id}/promotions` | [get listing promotions](../../app/routers/promotions.py#L186) | Public / none | Implemented | test_promotions.py, test_promotions_approval_security.py |
| `POST /api/v1/promotions` | [create promotion](../../app/routers/promotions.py#L251) | Approved Artisan provider / JWT + owner for writes | Implemented | test_promotions.py, test_promotions_approval_security.py |
| `PUT /api/v1/promotions/{id}` | [update promotion](../../app/routers/promotions.py#L337) | Approved Artisan provider / JWT + owner for writes | Implemented | test_promotions.py, test_promotions_approval_security.py |
| `DELETE /api/v1/promotions/{id}` | [delete promotion](../../app/routers/promotions.py#L425) | Approved Artisan provider / JWT + owner for writes | Implemented | test_promotions.py, test_promotions_approval_security.py |
| `POST /api/v1/artisan-reservations` | [request artisan reservation](../../app/routers/provider_reservations.py#L326) | Traveler / JWT | Implemented | test_artisan_reservations.py, test_provider_phase03.py |

### Shared operations used by Artisan

| Method and path | Purpose / authentication | Evidence and status |
| --- | --- | --- |
| POST `/api/v1/auth/login` | Shared account authentication | Implemented, [auth router](../../app/routers/auth.py#L220); `test_auth.py` |
| PUT `/api/v1/providers/registration/personal`; PUT `/business`; POST `/identity`; GET `/draft`; POST `/submit` under the same prefix | Shared provider registration grant, staged identity and business, submit for review | Implemented, [registration](../../app/routers/provider_registration.py#L236); `test_provider_registration.py`, `test_provider_otp.py` |
| POST `/api/v1/admin/providers/{provider_id}/approve`; POST `/api/v1/admin/providers/{provider_id}/reject` | Shared application decisions, admin JWT | Implemented [approval and rejection](../../app/routers/admin_providers.py#L278); `test_admin_providers.py`. Distinct from approval of an Artisan listing. |
| GET `/api/v1/provider/workspace`; GET `/api/v1/provider/dashboard`; GET `/api/v1/provider/dashboard/analytics`; GET `/api/v1/provider/dashboard/recent-activity` | Approved provider workspace/metrics including Artisan | Implemented [workspace](../../app/routers/provider_workspace.py#L51); `test_provider_workspace.py`; shared analytics provides no Artisan paid ad stats. |
| GET `/api/v1/provider/bookings`; GET `/api/v1/provider/bookings/{booking_id}`; POST `/api/v1/provider/bookings/{booking_id}/action` | Provider-owned queue/detail, accept/reject/ready/cash/complete/cancel as applicable | Implemented [provider reservations](../../app/routers/provider_reservations.py#L367); `test_provider_phase03.py`; rejection depends on 037. |
| GET `/api/v1/reservations/{module}/me`; POST `/api/v1/reservations/{module}/{booking_id}/cancel` | Traveler-owned shared reservation view/cancel. Artisan module value is `artisan_reservation`. | Implemented [shared routes](../../app/routers/provider_reservations.py#L339); `test_provider_phase03.py`. |
| GET `/api/v1/map/locations`; GET `/api/v1/map/nearby`; GET `/api/v1/map/search`; GET `/api/v1/map/categories`; POST `/api/v1/map/route` | Public location catalog/search/directions. Catalog supports Artisan. | Implemented [map router](../../app/routers/interactive_map.py#L70) and [catalog filter](../../app/services/map_catalog.py#L260); `test_interactive_map.py`. Deployment/provider quota and client map UI remain verification work. |
| GET `/api/v1/admin/map/locations`; PATCH `/api/v1/admin/map/locations/{module}/{listing_id}`; POST `/api/v1/admin/map/locations/{module}/{listing_id}/decision` | Shared listing-location controls, admin JWT | See actual paths and contracts in [map router](../../app/routers/interactive_map.py#L151); Artisan verification through listing status, the `/decision` route accepts Tour Guide and Rental only. |
| GET `/api/v1/notifications`; GET `/api/v1/notifications/unread-count`; PATCH `/api/v1/notifications/{notification_id}/read`; POST `/api/v1/notifications/read-all` | User JWT, own in-app notices including rejection | Implemented [notification router](../../app/routers/notifications.py#L23); rejection persistence checked by `test_artisan_reservation_rejection_future_contracts.py`. |
| POST `/api/v1/payments/payhere/notify` | Shared gateway callback with merchant/signature validation | Implemented for existing modules. The generic AD branch is Homestay-oriented. No Artisan ad payment activation. [Payment handler](../../app/routers/payments.py#L646); `test_homestay_phase9_advertising_payments.py` and Artisan negative sponsorship contracts. |

The scheduled/CLI expiry workflow is not an API operation. [Expiry service](../../app/services/artisan_reservation_expiry.py#L162) and [CLI](../../scripts/expire_artisan_reservations.py#L38) complement the admin route. This audit did not verify a deployed schedule.

## 7. Database Inventory

### Artisan-owned tables

Eight physical tables are in the **repository target**, of which seven were confirmed live in the preceding read-only audit. Shared business wishlists/reviews are not counted as dedicated Artisan tables.

| Code | Table and model | Migration and database controls |
| --- | --- | --- |
| L | `artisan_listings` / [ArtisanListing](../../app/models/artisan_listing.py#L11) | 012 creates and 024 extends. Required provider FK, nullable reviewer FK, coordinate range, approval and verification checks. Provider, district, craft, active/featured, and discovery indexes. |
| I | `showcase_items` / [ShowcaseItem](../../app/models/showcase_item.py#L11) | 013 + 024. Required listing FK, type/price/duration/stock/approval checks. Parent/type and discovery indexes. Currency storage is wider than the reservation currency. No approved product category FK. |
| D | `promotions` / [Promotion](../../app/models/promotion.py#L28) | 014. Required listing, optional item FK. Dates and mutually exclusive percentage/fixed amounts. Listing/item/schedule indexes, including filtered item index. No requirement that the optional item belongs to the selected listing at FK level; application verifies it. |
| BI | `artisan_business_images` / [ArtisanBusinessImage](../../app/models/artisan.py#L23) | 024. Listing cascade FK, parent index, nonnegative display order. Primary-image uniqueness is application-level. |
| PI | `artisan_product_images` / [ArtisanProductImage](../../app/models/artisan.py#L53) | 024. Item cascade FK, parent index, order check. Primary image constraints rely on application writes. |
| R | `artisan_reservations` / [ArtisanReservation](../../app/models/artisan.py#L92) | 024 + pending 037. Listing/item/traveler/canceller FKs, quantity/price/status checks and listing/traveler/status indexes. 037 adds rejector FK plus timestamp/reason and a checked status/audit invariant. No separate reservation item is needed for the single-product contract. |
| H | `artisan_business_hours` / [ArtisanBusinessHours](../../app/models/artisan.py#L209) | 024. Listing cascade FK and parent index, unique listing/weekday, weekday and open/close consistency checks. |
| F | `artisan_product_favorites` / [ArtisanProductFavorite](../../app/models/artisan.py#L74) | Pending 038. User and item cascade FKs, composite unique user/product, product index. Product-vs-service and visibility checked by API. |

### Shared dependencies and compatibility

| Code | Physical tables and model sources | Migration/expectation |
| --- | --- | --- |
| PR | `users`, `provider_profiles`, registration drafts and authorization state; [User](../../app/models/user.py), [ProviderProfile](../../app/models/provider_profile.py) | 001–006 baseline/auth/provider family, 015–016 category support, plus existing role/approval logic. Account activation and profile approval are distinct from listing approval. |
| LR | `listing_references` / [ListingReference](../../app/models/shared.py#L23) | 027. Unique module/target; Artisan value. Provider FK and discovery metadata. |
| BR, BH | `booking_references`, `booking_status_history` / [BookingReference](../../app/models/shared.py#L46), [BookingStatusHistory](../../app/models/shared.py#L356) | 027 plus module/status evolution in 034–036. Unique reference number and module/booking target. History links booking and actor. 037 alters native reservation only. |
| PT | `payment_transactions`, `payment_refunds`, `provider_settlements` / [shared.py](../../app/models/shared.py#L101) | 027, 030 and 036 affect shared finance. Booking/user FKs and amount/status checks. Artisan uses in-person cash records; shared tables do not confer authorization to accept online product payments. |
| N, W, REV, AL | `notifications`, `wishlists`, `reviews`, `admin_audit_logs` / [shared.py](../../app/models/shared.py#L165) | 027. Wishlist unique user/listing reference. Review rating range and listing/booking/user FKs. Notification and audit user FKs. Audit table existence is not proof the Artisan mutation logs to it. |
| SP | `promotion_packages`, `promotion_campaigns`, `promotion_events`, `advertising_payments` / [shared models](../../app/models/shared.py#L247) | 027 and 030–031. Scheduled campaigns and state checks, event type check and booking attribution index, unique advertising order and filtered gateway transaction ID. Reuse candidates only. No positive Artisan payment or analytics path. |

**Repository vs live.** Repository inventory and runner parsing cover a contiguous 001–038. The preceding verified live ledger was 001–036 with matching checksums, 001–006 baselined and 007–036 executed. That read-only check reported 90 user tables including the migration ledger. The current audit did not query SQL Server, so current live drift or external application of 037/038 is unverified. In this work sequence 037 and 038 are prepared and pending approval/application. At the last verified live schema, reservation status did not permit `rejected` and the favorite table was absent. The ORM now selects the three new rejection fields on existing reads. Deploying this build before 037 would therefore break even older reservation calls. Apply both migrations before enabling this API build.

The prior live catalog comparison matched selected ORM/SQL column sets through 036. It found `notifications.user_id` uses live ON DELETE CASCADE while the ORM omits that action, and `payment_transactions.payment_method` has a DB default of `visa_mastercard` versus the ORM's `cash`. Production indexes and CHECK constraints are richer than SQLite fixtures. Quantities and fixed money types map correctly, but listing item currency permits ten chars and reservation currency three, and reservation create variants accept different quantity limits. Runtime service validation needs parity with SQL constraints. Isolated SQL Server rehearsal of 037/038, table/FK/index checks and transaction contention remains the migration blocker. [037](../../migrations/037_extend_artisan_reservation_rejection.sql) · [038](../../migrations/038_create_artisan_product_favorites.sql)

### Review of migration ranges

| Files | Role in this audit |
| --- | --- |
| 001–006 | Baseline identity, authentication, provider/profile and personal-detail prerequisites. |
| 007–011 | Homestay catalog, bookings and offers. Regression and shared-platform context only. |
| 012–014 | Native Artisan catalog, showcase and discounts. |
| 015–023 | Provider category extensions and other-module domain tables. 017–020 Eco and Rental structures do not supply Artisan taxonomy or paid flows. |
| 024 | Artisan status, stock, galleries, reservations and hours. |
| 025–026 | Other-module extensions. Preserve cross-module behavior. |
| 027 | Shared references, payments, reviews, wishlists, notifications, campaigns/events and audit. |
| 028–029 | Community and AI domain additions. No direct Artisan requirement. |
| 030–031 | Homestay advertising/finance and promotion booking attribution. Shared candidates remain module-scoped. |
| 032–036 | Guide coordinate constraints, Rental changes, Eco reservations, Guide extensions and shared finance status alignment. 036 is the last verified live migration. |
| 037–038 | Working-tree Artisan rejection and product favorites. No existing migration was changed by this audit. |

## 8. Workflow Verification

| Workflow and acceptance condition | Observed result and remaining proof |
| --- | --- |
| Business registration → approved provider → listing review | Shared signup and listing approval are distinct. Approved providers can create own pending listings. Admin approve/reject, location verification, and suspension are separate; catalog requires active, approved, verified plus an active approved provider account. Reconcile the stricter verify-before-discovery policy with the approved spec. [Publication rule](../../app/core/publication.py#L94) |
| Product create → moderation → discovery | Owner CRUD, pending default, approve/reject/hide, active filters and gallery exist. Product category absent. No durable product decision history established. Raw image access and secondary public surfaces deserve approval parity. [Product moderation](../../app/routers/admin_artisan.py#L342) |
| Reservation create and stock | One public create operation dispatches two payload shapes. Product type and approval, listing/provider eligibility, pickup window, duplicate active pickup and server totals are checked. SQL Server uses transaction-owned product locks; shared create uses conditional stock update. Compare priced/null items, quantities, currency and expiry timestamps between shapes. [Dedicated create](../../app/routers/artisan_reservations.py#L80) · [Shared service](../../app/services/provider_reservations.py#L290) |
| Acceptance, rejection and status | Shared acceptance checks future pickup; the dedicated confirm route checks pending and ownership but does not enforce the same date/stock checks. Dedicated ready accepts pending despite its message. Rejection now requires owning approved provider, pending native/shared status, unpaid/no captured transaction, and reason. Stock, status, history and notification share one transaction. Missing matching reference returns 409. Offline rollback/concurrent duplicate verified; SQL isolation pending. [Dedicated routes](../../app/routers/artisan_reservations.py#L320) · [Rejection](../../app/services/provider_reservations.py#L404) |
| Cancel and expiry | Both release eligible stock; compare cutoff and conflict rules. Rejected rows are terminal. The detailed create sets an expiry timestamp; the legacy create omits it and expiry requires it. A CLI/admin trigger exists, with deployed scheduling unverified. Ensure timezone and confirmed/uncollected policies are approved. [Expiry](../../app/services/artisan_reservation_expiry.py#L70) |
| Pickup and cash | Both record in-person cash. The shared route distinguishes cash and completion and gates pickup timing; the detailed endpoint can complete from confirmed or ready and marks the shared payment paid. Detailed response lacks separate payment status. Unify dates and cash-payment state; verify no pre-visit, duplicate, paid cancellation, or invalid-state effects. [Detailed pickup](../../app/routers/artisan_reservations.py#L438) |
| Reviews and favorites | Listing ratings/moderation and author-owned management exist. Review public visibility differs from catalog. Product favorites rely on 038 and check catalog status, and business wishlist uses shared reference ownership. Test approval loss and concurrency on SQL Server. [Reviews](../../app/routers/artisan_reviews.py#L120) · [Favorites](../../app/routers/artisan_wishlists.py#L444) |
| Discounts | Amount ranges, date order, target ownership and public time filtering exist. Product display and reservation totals do not compute an effective discounted price. Define eligibility, precedence and floor-zero total, then test percentage, fixed amount greater than price, currency and expiry. [Offer validation](../../app/schemas/promotion.py#L67) |
| Sponsorship purchase → verified payment → activation → labeled discovery → expiry | No Artisan end-to-end implementation. Editorial featuring and discount expiry do not pass this workflow. Shared PayHere verifies signature, merchant and amount/currency in existing pathways, but generic AD target lookup resolves Homestay. Approve an independent Artisan order namespace, locked capture/reversal, eligibility on read, and schedules before implementation. [Callback](../../app/routers/payments.py#L314) · [Dispatch](../../app/routers/payments.py#L646) |
| Sponsorship statistics | No Artisan event collection or performance response. Shared event types are reusable; record only actual attributable impressions/clicks/inquiries, deduplicate, enforce retention and tenant isolation. |
| Admin suspension and reports | Suspension clears active/featured and main discovery hides listings. Public file serving does not recheck status when a previously known URL is used. Summary and CSV exist with sanitization/privacy tests; report discounts separately from any future paid ledger. [Suspension](../../app/routers/admin_artisan.py#L217) · [Reports](../../app/routers/admin_artisan.py#L524) |

## 9. Security and Testing

### Validation and security matrix

| PDF acceptance topic | Evidence and assessment |
| --- | --- |
| Public eligibility | Strong shared predicate in catalog, showcase, map and product favorites. Gallery/hours use explicit checks that do not fully mirror it, review reads primarily check the listing, and direct files are unauthenticated. Test provider inactivity, listing verification revocation, suspension and item hiding across every public surface. |
| Ownership and roles | JWT dependencies, active account check, approved providers, item/listing ownership, and admin-only decisions. Read review ownership and write authorship are separate. Shared provider action uses scoped workspace. Existing negative tests are present; retest new fields and aliases in isolated acceptance. |
| Client-trusted fields | Listing provider writes strip moderation fields and updates guard the review snapshot. Showcase writes strip approval. Reservation totals are server-calculated. Verification boolean/status remain independent, and paid sponsorship controls are absent. Backend authorization is enforced by role and ownership checks. |
| Price, quantity, state and time | Nonnegative Decimal prices, positive quantities, date and range validation exist. LKR and money precision are not uniform across routes, accept/ready/complete behavior diverges, and local pickup expiry should be explicitly converted to UTC. Confirm date inclusivity, conditional stock policy and release windows. |
| Contacts, uploads, abuse | Email format and phone length/trim exist; WhatsApp and robust phone policies are missing. Uploads enforce format, size (5 MiB), dimension and count, with traversal checks. Shared onboarding and map route quota have controls. No consistent Artisan rate limit evidenced on favorites/reviews/reservations/uploads; decide app or gateway enforcement and load-test. |
| Audit, payment and CSV | Booking history/audit fields and traveler rejection notice are transactional. Listing moderation records actor/time in key actions, but verification and product audit are incomplete. CSV injection and reservation privacy are covered. Cash totals are recorded; signed sponsorship success, replay/reversal and reconciliation remain unsupported. |
| Concurrency | SQLite conditional update regression covers duplicate rejection. SQL Server applocks are transaction owned and run under one product key. Rehearse create/accept/reject/cancel/expiry/cash and manual stock races plus rollback/lock-release. New opt-in tests cover rejection/favorites and lock contention, but are not exhaustive across request variants. |

### Evidence and results

| Check | Result and interpretation |
| --- | --- |
| Fresh Artisan collection | **244 collected** across 20 Artisan/admin/catalog/discount files, including tests for disabled sponsorship. Related shared tests such as map, provider workspace and booking action are outside that count; no assumption that each collected case is a positive requirement. |
| Fresh full offline collection | **1,411 collected**, SQL Server modules excluded with the stand-alone offline rental arithmetic check selected explicitly. No fresh pass/fail/skip outcomes because no fixture-writing execution was performed. Historic Phase 03 full run **1,411 passed, 0 failed, 0 skipped** in its selected set, and final focused run **75 passed, 0 failed, 2 skipped** for SQL Server opt-ins. The audit independently checked the current collection count, not an assertion-by-assertion rerun. |
| SQL Server | Previous read-only inspection verified 001–036. [Phase 03 integration tests](../../tests/test_artisan_phase03_sqlserver.py#L28) require configured opt-in, verified server/database identity and migrations. They were not run in this audit. |
| OpenAPI and ORM | Fresh import with SQL Server connections blocked produced 429 paths, 516 unique operation IDs and 90 configured mappers. Fallback and alias behavior must be considered separately from schema generation. |
| Angular 21 / Flutter 3 readiness | Source searches for the audited Artisan route names in Angular `src` and Flutter `lib` did not locate direct integrations. Angular registration has Artisan category references. Indirect/runtime bindings and apps were not validated. Build client contract tests for maps/consent, public sharing, images, badges, favorites, status/cash, errors and admin workflows. Sponsored contracts are intentionally unavailable. |

## 10. Remaining Development

Estimates are engineering person-days, including targeted tests, for one developer familiar with the repository. Separate client effort is shown where relevant. Estimates exclude approvals, procurement, infrastructure and waiting for access. Work in each row is grouped across shared gaps to avoid counting a single fix repeatedly.

| Priority | Work and affected IDs | Estimate | Dependencies and release gate |
| --- | --- | --- | --- |
| P0 | Rehearse 037/038 and run guarded SQL matrix. Confirm constraint/index/version identity, booking read compatibility, rejection rollback/duplicates and favorite uniqueness. T-11/12/14, P-12/13 | 1–2 days | DBA approval, approved isolated server and prior migration baseline. Deploy schema before API. |
| P0 | Converge reservation state, quantity/date/currency, holds and expiry, staff actions, payment display and cross-route behavior. T-11/12/16, P-06/07/12/13 | 4–7 days | Product owner pickup, cancellation, expiry, stock, zero-price and cash policy. Test concurrent SQL writers. |
| P0 | Unify public visibility, media access, reviews, hours, status/verification audit and moderation/suspension. T-05/10/13/15, P-17/18, A-02/03/06/07 | 3–6 days | Confirm listing approval vs verified publishing rule, media privacy, review eligibility and retention. |
| P0 | Product categories and search/filter contract, business story/specialties and catalog data compatibility. T-02/04/06, P-03/08 | 3–5 days | Taxonomy/specialty decision and migration approval if necessary. |
| P1 | Contacts and price/offer calculation, approved anti-abuse limits, logs and owner/customer/status contract checks. T-09/16, P-06/10/14/17/18 | 3–5 days | Verify WhatsApp consent, price/currency, offers precedence, rate limits and reporting policy. |
| P1 | Client integration for maps, consent, directions, catalog, badge, favorites, photos, cash/pickup UX, and share URLs. T-07/08/14/15/18 and client-facing P/A contracts | 5–9 client days plus 1–2 backend contract days | Verified environments, route binding, roles, URL/deep-link policy, accessibility and stable negative fixtures. |
| P1 deferred | Paid campaign packages, provider purchase/admin moderation, verified payment, eligibility-aware placement and expiry. T-17, P-15, A-04/05 and sponsored reporting in A-07 | 8–14 days | Separate approval for C/D and new migrations as needed. Pricing, ranking, budget, schedule, refund/chargeback and merchant policies. Gateway sandbox and production configuration. |
| P2 deferred | Sponsored analytics, privacy, attribution and performance, extended provider/admin reports. P-16, A-07 | 3–5 days | Implemented paid campaign workflow and agreed event schema/retention. |

## 11. Completion Percentage

Each of the 43 features carries equal weight. The implementation score is the arithmetic mean of the row percentages. A row receives 100 when its behavior is present at the backend contract boundary, even if release evidence is pending. Partial scores reflect the share of the specified behavior that source evidence supports and the relative importance of missing pieces, not the number of endpoints. Zero means the required behavior is absent even when adjacent primitives exist. These are audit estimates, not measured code coverage. Verified completion counts only **COMPLETED** rows based on source and established automated evidence for the backend scope. It does not certify client or production acceptance.

| Scope | Completed | Implemented — verification pending | Partial | Not implemented | Implementation points / max | Implementation | Verified completion |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Traveler (18) | 2 | 4 | 10 | 2 | 1,400 / 1,800 | 77.8% | 2 / 18 = 11.1% |
| Provider (18) | 7 | 0 | 8 | 3 | 1,360 / 1,800 | 75.6% | 7 / 18 = 38.9% |
| Admin (7) | 1 | 0 | 4 | 2 | 455 / 700 | 65.0% | 1 / 7 = 14.3% |
| **Overall (43)** | **10** | **4** | **22** | **7** | **3,215 / 4,300** | **74.8%** | **10 / 43 = 23.3%** |

## 12. Final Recommendations

1. Treat schema rollout and SQL Server concurrency as the first release gate for Phase 03. The current API cannot safely be deployed against the last verified live schema without 037 and 038.
2. Align reservation behavior and authorization visibility before client acceptance. The state machine mismatch, legacy expiry gap and public media visibility matter more than adding routes.
3. Agree product taxonomy, verification semantics and price/discount calculation before promising feature completeness. Verify both clients directly.
4. Keep sponsorship deferred until commercial, payment, reporting and DBA decisions are approved. Treat earlier contract tests and shared gateway primitives as evidence of groundwork, not paid launch readiness.

Only this report was created. No source, migrations, Git index, branch, or database state was changed by this audit.
