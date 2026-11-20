# Database Coordinator Review Document — Migration 034 & Shared Payment CHECK Constraint Alignment

**Module:** Local Tour Guide (Module 05)  
**Branch:** `feature/tour-guide-backend-complete`  
**Status:** Promoted & Renumbered after `origin/develop` (`24c7dca`, PR #25) added `034_create_eco_reservations.sql` — now promoted as **`migrations/035_extend_tour_guide_module_final_gaps.sql`** and **`migrations/036_align_shared_payment_status_constraints.sql`** (with rollbacks in `migrations/rollback/035_*` and `036_*`). Superseded `PROPOSED_*.sql` drafts removed.

---

## 1. Version Availability & Migration Safety Summary

- **Highest applied migration in repository:** `migrations/033_extend_rental_module_final_gaps.sql`.
- **Conflict check:** Verified `migrations/` and all local and remote-tracking refs (`origin/*`) — version **`034` is free with zero conflicts**.
- **Applied migrations `001`–`033`:** Untouched (zero checksum changes).
- **Target files upon approval:**
  - Forward: `migrations/034_extend_tour_guide_module_final_gaps.sql` (drafted at `docs/tour-guide/PROPOSED_034_extend_tour_guide_module_final_gaps.sql`)
  - Rollback: `migrations/rollback/034_extend_tour_guide_module_final_gaps_rollback.sql` (drafted at `docs/tour-guide/PROPOSED_034_extend_tour_guide_module_final_gaps_rollback.sql`)
- **Application guard:** All application code referencing Migration `034` columns is gated behind BOTH runtime SQLAlchemy `inspect()` column detection AND `TOUR_GUIDE_ENABLE_M034_FIELDS=true`, ensuring zero `Invalid column name` failures on existing Migration `033` databases.

---

## 2. Migration 034 Schema Inventory & Existing-Data Compatibility

### Table 1: `dbo.tour_guide_bookings` (Online Payment & Financial Snapshots)
| Column | Type | Nullability / Default | Constraint / Index | Existing Data Compatibility |
| --- | --- | --- | --- | --- |
| `approved_at` | `DATETIME2` | `NULL` | Filtered index `IX_tour_guide_bookings_approved_at` (`WHERE approved_at IS NOT NULL`) | 100% safe (NULL on existing rows) |
| `payment_expires_at` | `DATETIME2` | `NULL` | Filtered index `IX_tour_guide_bookings_payment_expiry` (`WHERE payment_expires_at IS NOT NULL`) | 100% safe (NULL on existing rows) |
| `cash_received_at` | `DATETIME2` | `NULL` | — | 100% safe (NULL on existing rows) |
| `commission_rate` | `DECIMAL(5,2)` | `NULL` | `CK_tour_guide_bookings_commission_rate` (`NULL` or `0..100`) | 100% safe |
| `commission_amount` | `DECIMAL(18,2)` | `NULL` | `CK_tour_guide_bookings_commission_amount` (`NULL` or `>= 0`); matches `total_price DECIMAL(18,2)` | 100% safe |
| `provider_payable_amount` | `DECIMAL(18,2)` | `NULL` | `CK_tour_guide_bookings_provider_payable` (`NULL` or `>= 0`); matches `total_price DECIMAL(18,2)` | 100% safe |
| *(Constraint update)* `CK_tour_guide_bookings_status` | `CHECK` | — | Adds `'awaiting_payment'` to existing 7 statuses (`WITH CHECK`) | 100% safe (existing statuses are a strict subset); rollback includes preflight `THROW 50340` |

### Table 2: `dbo.tour_guide_packages` (`GS-03`, `GS-06`, Explicit Package Type)
| Column | Type | Nullability / Default | Constraint | Existing Data Compatibility |
| --- | --- | --- | --- | --- |
| `package_type` | `NVARCHAR(20)` | `NULL` | `CK_tour_guide_packages_type` (`NULL` or `half_day`, `full_day`, `custom`) | 100% safe (NULL on existing rows; app falls back to duration classification) |
| `meeting_instructions` | `NVARCHAR(1000)` | `NULL` | — | 100% safe (`GS-03`) |
| `special_requirements` | `NVARCHAR(2000)` | `NULL` | — (stores JSON or structured fitness/clothing/equipment/other notes, matching `included_services NVARCHAR(2000)`) | 100% safe (`GS-06`) |

### Table 3: `dbo.tour_guides` (`TR-18`/`PR-17` WhatsApp Opt-In, `GS-01` Verified Experience)
| Column | Type | Nullability / Default | Constraint | Existing Data Compatibility |
| --- | --- | --- | --- | --- |
| `whatsapp_number` | `NVARCHAR(30)` | `NULL` | Checked jointly by `CK_tour_guides_whatsapp_opt_in` | 100% safe |
| `allow_whatsapp_contact` | `BIT` | `NOT NULL` `DF_tour_guides_allow_whatsapp DEFAULT 0` | `CK_tour_guides_whatsapp_opt_in`: `allow_whatsapp_contact = 0 OR (whatsapp_number IS NOT NULL AND LEN(LTRIM(RTRIM(whatsapp_number))) >= 7)` | 100% safe (defaults to `0` for all existing guides — privacy-preserving opt-in!) |
| `verified_years_of_experience` | `INT` | `NULL` | `CK_tour_guides_verified_experience` (`NULL` or `0..80`) | 100% safe (NULL distinguishes unverified from admin-verified years) |

### Rollback Safety Checklist
- `SET ANSI_NULLS ON; SET QUOTED_IDENTIFIER ON;` included for SQL Server filtered-index safety.
- Strictly idempotent (`IF EXISTS` / `IF COL_LENGTH(...) IS NOT NULL` / `IF OBJECT_ID(...) IS NOT NULL`).
- Reverse dependency order verified: drops filtered indexes → drops CHECK & DEFAULT constraints → verifies preflight `THROW 50340` (blocks unsafe rollback if `awaiting_payment` rows exist) → restores Migration `022` `CK_tour_guide_bookings_status` → drops all 12 columns.

---

## 3. Pre-Existing Shared Payment CHECK Constraint Incompatibilities (Migration `027`)

An audit of `migrations/001`–`033` confirmed that **no migration after `027_create_shared_infrastructure.sql`** updated the three shared payment CHECK constraints below. Because shared `app/routers/payments.py` (used by Homestay, Rental, and Tour Guide) writes status strings not listed in Migration `027`, live SQL Server execution of online payment confirmations, admin refund approvals/completions, and `'completed'` settlements will fail until the Database Coordinator approves a dedicated shared alignment migration (e.g., proposed `035_align_shared_payment_status_constraints.sql`):

| # | Shared Table & Constraint | Migration `027` Definition | Values Written by `app/routers/payments.py` | Impacted Modules | Recommended SQL Server Alignment |
| :-: | --- | --- | --- | --- | --- |
| 1 | `dbo.payment_transactions`<br>`CK_payment_transactions_status` | `('pending', 'completed', 'failed', 'refunded', 'cancelled')` | `'paid'` (lines 208, 749, and Tour Guide callback) | Homestay, Rental, Tour Guide | Extend CHECK to `('pending', 'paid', 'completed', 'failed', 'refunded', 'cancelled')` (`WITH CHECK`) |
| 2 | `dbo.payment_refunds`<br>`CK_payment_refunds_status` | `('pending', 'processed', 'failed', 'rejected')` | `'approved'`, `'completed'` (lines 1155, 1175) | Homestay, Rental, Tour Guide | Extend CHECK to `('pending', 'approved', 'processed', 'completed', 'failed', 'rejected')` (`WITH CHECK`) |
| 3 | `dbo.provider_settlements`<br>`CK_provider_settlements_status` | `('pending', 'approved', 'paid', 'on_hold')` | `'completed'` (lines 1590, 1606) | Homestay, Tour Guide | Extend CHECK to `('pending', 'approved', 'paid', 'completed', 'on_hold')` (`WITH CHECK`) |

> **Why we did NOT bundle these into Migration `034`:** These three tables are shared platform infrastructure owned across modules. Keeping `034` strictly scoped to `dbo.tour_guide*` tables isolates Module 05 risk and lets the Database Coordinator approve shared payment constraint alignment independently.

---

## 4. Optional Additional Coordinator Items (Non-Blocking)

1. **`AD-TG-` Advertising Webhook Routing (3 lines in `app/routers/payments.py`):** Route `order_id.startswith("AD-TG-")` in `handle_payhere_callback` to `app.services.tour_guide_phase5.handle_tour_guide_advertising_payment_callback` so live PayHere advertising IPNs for Tour Guide campaigns bypass Homestay lookup.
2. **Interactive Map Deep-Link (1 line in `app/services/map_catalog.py`):** Add `"tour_guide": "/api/v1/tour-guides"` to `DETAIL_PATHS`.
3. **Optional Review Text Replies (`dbo.reviews`):** Original Module 05 spec defines `PR-16` strictly as *"Read eligible traveler feedback"* (already satisfied). Only add `provider_response` / `provider_responded_at` to `dbo.reviews` in a future shared migration if the product lead adds persistent provider replies to the scope.

---

## 5. Required Approvals Before Live SQL Server Rollout

- [ ] **Approval 1 (DB Coordinator):** Approve promoting `PROPOSED_034` & rollback SQL into `migrations/` and `migrations/rollback/` and applying to dev/staging SQL Server, then enabling `TOUR_GUIDE_ENABLE_M034_FIELDS=true`.
- [ ] **Approval 2 (DB Coordinator):** Approve a shared migration (`035`) aligning `CK_payment_transactions_status`, `CK_payment_refunds_status`, and `CK_provider_settlements_status` on live SQL Server.
- [ ] **Approval 3 (Team Lead, Optional for PR #1):** Approve the 3-line `AD-TG-` advertising webhook dispatch in `app/routers/payments.py` and `"tour_guide"` detail path in `app/services/map_catalog.py`.
