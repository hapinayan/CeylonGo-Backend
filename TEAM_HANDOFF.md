# CeylonGo Artisan Module – Team Handoff Documentation

**Date:** November 20, 2026  
**Repository:** `CeylonGo-Backend`  
**Branch:** `feature/artisan-p0-orm-alignment`  
**Status:** Backend P0-P2 Implementation Complete & Verified Offline (100% Test Pass Rate)  

---

## 1. Executive Summary

This handoff document transitions the **CeylonGo Artisan Module** backend implementation to incoming engineers.

All completed backend features have been implemented, secured, and validated with offline unit/integration tests without touching or modifying the live SQL Server database. The backend code adheres strictly to the database safety guardrails, repository architecture, and existing team code conventions.

---

## 2. Implemented Backend Features (36 Features at Code Level)

The following feature groups are fully implemented and verified in the backend application layer:

1. **Artisan Business Listings & Discovery:**
   - Provider profile registration, listing creation, updates, and soft deletes.
   - Public listing catalog with district, category, and approval filtering (`is_active = True`, `is_approved = True`).
2. **Showcase Products & Inventory Management:**
   - Creation and maintenance of products and services.
   - Inventory tracking (`stock_quantity`), active status toggles, and service vs product discrimination.
3. **Business Operating Hours & Schedules:**
   - Day-of-week operating hours definition, open/close time validation, and unique weekday constraints.
4. **Artisan Image Galleries:**
   - Multi-image uploads, thumbnail/primary image selection, and provider ownership validation.
5. **Reservations & Pickup Workflow:**
   - Traveler pickup reservation creation with atomic stock decrement.
   - Concurrency locking (`acquire_product_lock` via `sp_getapplock` on MSSQL, in-process fallback in SQLite).
   - Strict state machine transitions: `pending -> confirmed -> ready_for_pickup -> completed`.
   - Traveler cancellation with automated stock restoration.
6. **Automated Reservation Expiry:**
   - Idempotent batch auto-expiry service ([app/services/artisan_reservation_expiry.py](file:///C:/ceylongo%20clone%2018.11/CeylonGo-Backend/app/services/artisan_reservation_expiry.py)) restoring stock for past-due reservations.
   - CLI runner script: `scripts/expire_artisan_reservations.py`.
7. **Ratings & Verified Reviews:**
   - Traveler reviews with 1–5 star ratings, public visibility filtering, and rating aggregate calculation summaries.
8. **Traveler Business Wishlists:**
   - Listing-level wishlist additions, removals, and traveler isolation.
9. **Provider Dashboard:**
   - Aggregated metrics including active listings, pending reviews, reservation counts, and pickup volume.
10. **Admin Governance & Moderation:**
    - Pending listing review queue, business approval, rejection with mandatory notes, and business suspension.
    - Review visibility moderation (hide/republish).
    - Basic administrative reports with injection-safe CSV export (preventing spreadsheet formula injection).
11. **Shared Infrastructure:**
    - Booking reference and listing reference cross-synchronization with core platform schemas.

---

## 3. Latest Test Verification Results

All tests execute offline without requiring a live SQL Server instance (using SQLite in-memory mock fixtures and ASGI TestClient):

- **Command:**
  ```powershell
  pytest tests\test_artisan_*.py tests\test_admin_artisan_*.py tests\test_showcase_items.py tests\test_promotions_approval_security.py tests\test_database_guard_and_openapi.py -q
  ```
- **Results:** **230 passed, 3 skipped, 0 failed in ~56s (100% Green)**.
- **Skipped Tests (3):** Gated future contract tests in `tests/test_artisan_reservation_rejection_future_contracts.py` that depend on DBA schema migration approval for status enum extension.

---

## 4. The Seven Incomplete Artisan Features

Per project governance, the following 7 features are **intentionally not completed in production** because they require formal DBA schema changes and Product Owner commercial policies:

| Feature Code | Feature Name | Status & Blocking Dependency |
| :---: | :--- | :--- |
| **T-14** | Product Favorites | Business wishlists are implemented. Individual product favorites require **DBA Proposal B** (dedicated product favorites table). |
| **T-17** | Sponsored Artisan Placement | Requires **DBA Proposal C/D** (sponsorship bidding/ranking schema) and commercial sponsorship guidelines. |
| **P-12** | Provider Reservation Rejection | Rejection status requires **DBA Proposal A** (extending SQL Server `CK_artisan_reservations_status` constraint). |
| **P-15** | Sponsored Listing Creation | Requires **DBA Proposal C** schema tables and payment gateway commercial agreement. |
| **P-16** | Promotion Analytics | Requires **DBA Proposal C** (promotion impressions/clicks tracking tables). |
| **A-04** | Promotion Moderation | Requires **DBA Proposal C/D** admin sponsorship management schema. |
| **A-05** | Sponsored Promotion Payment Verification | Requires payment gateway integration and financial transaction tables. |

---

## 5. Database & Business-Policy Dependencies (DBA Proposals)

Do **NOT** execute migrations without DBA review. The required future database proposals are documented in `docs/`:

1. **DBA Proposal A (Reservation Rejection Enum):**
   - Extends check constraint `CK_artisan_reservations_status` to include `'rejected'`.
2. **DBA Proposal B (Product Favorites Schema):**
   - New table `dbo.artisan_product_favorites (id, user_id, product_id, created_at)`.
3. **DBA Proposal C & D (Sponsorship & Promotion Commercial Ledger):**
   - Tables for sponsored campaigns, impression telemetry, payment receipts, and moderation logs.

---

## 6. Key API Contracts & Endpoints

| Resource | Method | Path | Auth Required | Description |
| :--- | :---: | :--- | :---: | :--- |
| **Listings** | `GET` | `/api/v1/artisan-listings` | Public | Search approved active listings |
| | `GET` | `/api/v1/artisan-listings/{id}` | Public | Listing details |
| | `POST` | `/api/v1/artisan-listings` | Provider | Create business listing |
| | `PUT` | `/api/v1/artisan-listings/{id}` | Provider | Update business listing |
| | `DELETE`| `/api/v1/artisan-listings/{id}` | Provider | Soft delete listing |
| **Showcase** | `GET` | `/api/v1/artisan-listings/{id}/items` | Public | Active showcase items |
| | `POST` | `/api/v1/artisan-listings/{id}/items` | Provider | Add product or service |
| **Reservations** | `POST`| `/api/v1/artisan-reservations` | Traveler | Place pickup order (holds stock) |
| | `GET` | `/api/v1/artisan-reservations/my` | Traveler | Traveler reservation history |
| | `GET` | `/api/v1/artisan-reservations/provider` | Provider | Provider incoming orders |
| | `PUT` | `/api/v1/artisan-reservations/{id}/status` | Provider | State transitions |
| | `POST`| `/api/v1/artisan-reservations/{id}/cancel` | Traveler | Cancel reservation (restores stock) |
| **Reviews** | `GET` | `/api/v1/artisan-listings/{id}/reviews` | Public | Published customer reviews |
| | `GET` | `/api/v1/artisan-reviews/listing/{id}` | Public | Aliased review endpoint |
| | `GET` | `/api/v1/artisan-reviews/listing/{id}/summary`| Public | Rating summary stats |
| | `POST`| `/api/v1/artisan-listings/{id}/reviews` | Traveler | Submit verified review |
| **Wishlists** | `GET` | `/api/v1/artisan-wishlists` | Traveler | Traveler wishlist items |
| | `POST`| `/api/v1/artisan-wishlists/{listing_id}` | Traveler | Add business to wishlist |
| **Dashboard** | `GET` | `/api/v1/artisan-dashboard/summary` | Provider | Provider dashboard metrics |
| **Admin** | `GET` | `/api/v1/admin/artisan-listings/pending`| Admin | Listings awaiting approval |
| | `POST`| `/api/v1/admin/artisan-listings/{id}/approve` | Admin | Approve listing |
| | `POST`| `/api/v1/admin/artisan-listings/{id}/reject` | Admin | Reject listing with notes |
| | `POST`| `/api/v1/admin/artisan-listings/{id}/suspend`| Admin | Suspend listing |
| | `PUT` | `/api/v1/admin/artisan-reviews/{id}/visibility`| Admin | Moderate review visibility |
| | `GET` | `/api/v1/admin/artisan-reports/export` | Admin | Export CSV report |

---

## 7. Instructions for the Next Developer

1. **Environment Setup:**
   - Python 3.12+ virtual environment is located at `.venv` (or recreate using `requirements.txt`).
   - Run tests offline:
     ```powershell
     pytest tests/test_artisan_*.py tests/test_admin_artisan_*.py tests/test_showcase_items.py tests/test_promotions_approval_security.py tests/test_database_guard_and_openapi.py -q
     ```
2. **Database Migrations:**
   - Do **NOT** apply any schema migration directly against staging or production without obtaining written DBA sign-off.
   - Reference `docs/DATABASE_MIGRATION_GUIDE.md` for proper sequencing if live database integration testing is authorized.
3. **Frontend Integration:**
   - Frontend mock demo services and production endpoints in `CeylonGo-Frontend` (Angular) and `CeylonGo-Flutter` (Flutter) align with the above API contracts.
   - Both frontends have verified offline demo modes that run independently of backend servers.

