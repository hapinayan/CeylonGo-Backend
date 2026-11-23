# Phase 02B Stage 01 retirement candidates

**No route is cleared to unmount.** The exact first review batch is the standalone Homestay, Rental and Eco wishlist surface below. It is outside approved V1.1 scope. Read-only searches in the adjacent Angular and Flutter source did not establish callers. This cannot rule out dynamic clients or released versions.

| Method | Full path | Handler | OpenAPI |
|---|---|---|---|
| GET | `/api/v1/rental-wishlist` | `app.routers.rental_wishlists.get_my_rental_wishlist` | Documented |
| DELETE | `/api/v1/rental-wishlist/{vehicle_id}` | `app.routers.rental_wishlists.remove_rental_from_wishlist` | Documented |
| POST | `/api/v1/rental-wishlist/{vehicle_id}` | `app.routers.rental_wishlists.add_rental_to_wishlist` | Documented |
| GET | `/api/v1/rental-wishlist/{vehicle_id}/status` | `app.routers.rental_wishlists.check_rental_wishlist_status` | Documented |
| DELETE | `/api/v1/wishlists/eco/{experience_id}` | `app.routers.wishlists.remove_from_eco_wishlist` | Documented |
| POST | `/api/v1/wishlists/eco/{experience_id}` | `app.routers.wishlists.add_to_eco_wishlist` | Documented |
| GET | `/api/v1/wishlists/homestays` | `app.routers.wishlists.get_my_homestay_wishlist` | Documented |
| DELETE | `/api/v1/wishlists/homestays/{homestay_id}` | `app.routers.wishlists.remove_homestay_from_wishlist` | Documented |
| POST | `/api/v1/wishlists/homestays/{homestay_id}` | `app.routers.wishlists.add_homestay_to_wishlist` | Documented |
| GET | `/api/v1/wishlists/homestays/{homestay_id}/status` | `app.routers.wishlists.check_homestay_wishlist_status` | Documented |
| GET | `/api/v1/wishlists/me` | `app.routers.wishlists.list_my_wishlist` | Documented |
| GET | `/api/v1/wishlists/rentals` | `app.routers.rental_wishlists.get_my_rental_wishlist` | Hidden compatibility alias |
| DELETE | `/api/v1/wishlists/rentals/{vehicle_id}` | `app.routers.rental_wishlists.remove_rental_from_wishlist` | Hidden compatibility alias |
| POST | `/api/v1/wishlists/rentals/{vehicle_id}` | `app.routers.rental_wishlists.add_rental_to_wishlist` | Hidden compatibility alias |
| GET | `/api/v1/wishlists/rentals/{vehicle_id}/status` | `app.routers.rental_wishlists.check_rental_wishlist_status` | Hidden compatibility alias |


## Preconditions and rollback

- The handlers in [wishlists.py](../../../app/routers/wishlists.py) and [rental_wishlists.py](../../../app/routers/rental_wishlists.py) use listing eligibility and wishlist persistence. Keep saved data, shared listing references, and ORM models. Withdrawal of write endpoints would stop future saves or removals through these paths.
- Verify retained discovery and detail responses for wishlist flags and run authenticated catalog, listing, booking, Map and Admin regressions with wishlist APIs omitted in an isolated test configuration. Check deployed consumers and the four hidden aliases before proposing an unmount. In particular, a missing OpenAPI entry is not evidence that an alias is unused.
- Hold Artisan listings, reservations and wishlists until Map, Admin, shared reservation history and media dependencies are handled. Hold payment-backed promotions, sponsorship and advertising until callback and transaction-history impact is approved. Review analytics routes individually for provider and Admin reporting dependencies.
- Once approved, isolate the router registration change and verify only the agreed method/path pairs disappear. Restore prior router registration if any retained contract fails. Keep models, data, migrations and gateway behavior outside the retirement batch.
