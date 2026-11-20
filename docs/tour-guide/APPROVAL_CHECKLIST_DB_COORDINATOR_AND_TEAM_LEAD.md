# Final Approval Checklist — Local Tour Guide Module 05 (Path to 54/54 Verified Completion)

**Branch:** `feature/tour-guide-backend-complete`  
**Updated After `origin/develop` (`24c7dca`, PR #25) Integration:** `034_create_eco_reservations.sql` was merged on `origin/develop`. Module 05 migrations have been promoted and renumbered cleanly to **`migrations/035_extend_tour_guide_module_final_gaps.sql`** and **`migrations/036_align_shared_payment_status_constraints.sql`** (with matching rollback scripts under `migrations/rollback/`). The superseded `PROPOSED_*.sql` drafts were removed.

---

## Part A — Database Coordinator Sign-Off

### 1. Module 05 Extension Migration (`034`) — Unlocks `TR-18`, `PR-17`, `GS-01`, `GS-03`, `GS-06`
- **Forward SQL:** `docs/tour-guide/PROPOSED_034_extend_tour_guide_module_final_gaps.sql`
- **Rollback SQL:** `docs/tour-guide/PROPOSED_034_extend_tour_guide_module_final_gaps_rollback.sql`
- **Production Safety Verification Completed:**
  - [x] `SET ANSI_NULLS ON; SET QUOTED_IDENTIFIER ON;` declared for filtered-index compliance.
  - [x] All 12 new columns are either `NULL` or `BIT NOT NULL DEFAULT 0` (`allow_whatsapp_contact`), ensuring zero breaking impact on existing rows.
  - [x] `commission_amount` and `provider_payable_amount` match `tour_guide_bookings.total_price` (`DECIMAL(18, 2)`).
  - [x] `special_requirements` sized at `NVARCHAR(2000)` matching `included_services`/`excluded_services`.
  - [x] `CK_tour_guides_whatsapp_opt_in` enforces opt-in integrity (`allow_whatsapp_contact = 0` OR non-empty phone >= 7 chars).
  - [x] `CK_tour_guides_verified_experience` enforces `NULL` or `0..80`.
  - [x] `CK_tour_guide_bookings_status` expands existing 7 statuses to include `'awaiting_payment'` (`WITH CHECK`); rollback guards tightening with `THROW 50340`.
  - [x] Rollback script is strictly idempotent and drops indexes → constraints → default constraints → columns in exact reverse order.
- **Coordinator Actions Upon Approval:**
  - [ ] Confirm version `034` is still free on target `develop`.
  - [ ] Move forward SQL to `migrations/034_extend_tour_guide_module_final_gaps.sql` and rollback SQL to `migrations/rollback/034_extend_tour_guide_module_final_gaps_rollback.sql`.
  - [ ] Apply to dev/staging SQL Server via `python scripts/migrate.py upgrade` and validate via `python scripts/migrate.py validate`.
  - [ ] Set `TOUR_GUIDE_ENABLE_M034_FIELDS=true` in dev/staging/prod environment configs.

### 2. Shared Payment CHECK-Constraint Alignment Migration (Unnumbered — Assign Next Free Version) — Unlocks Live SQL Server `TR-12`, `TR-15`, `PR-15`, `AD-06`
- **Forward SQL:** `docs/tour-guide/PROPOSED_NEXT_align_shared_payment_status_constraints.sql`
- **Rollback SQL:** `docs/tour-guide/PROPOSED_NEXT_align_shared_payment_status_constraints_rollback.sql`
- **Pre-Existing Incompatibilities Resolved (Across Homestay, Rental & Tour Guide):**
  - [ ] `dbo.payment_transactions.CK_payment_transactions_status`: add `'paid'`.
  - [ ] `dbo.payment_refunds.CK_payment_refunds_status`: add `'approved'`, `'completed'`.
  - [ ] `dbo.provider_settlements.CK_provider_settlements_status`: add `'completed'`.
- **Coordinator Actions Upon Approval:**
  - [ ] Check latest team migration sequence on `origin/develop` and assign the next sequential number (e.g., `035` if no other module claimed it).
  - [ ] Move forward & rollback SQL into `migrations/` and `migrations/rollback/` and apply to dev/staging SQL Server.
  - [ ] Confirm rollback preflight guards (`THROW 50351`, `50352`, `50353`) satisfy team rollback policy.

---

## Part B — Team Lead Sign-Off

### 1. Minimal `AD-TG-` Advertising Webhook Delegation Patch — Completes `PR-19`
- **Patch File:** `docs/tour-guide/PROPOSED_PR19_AD_TG_WEBHOOK_DELEGATION.patch` (+9 lines / -0 lines in `app/routers/payments.py`).
- **Scope:** Adds `if order_id.startswith("AD-TG-"): return handle_tour_guide_advertising_payment_callback(...)` directly ahead of Homestay's `if order_id.startswith("AD-"):` branch. Zero lines of Homestay or Rental logic altered.
- **Team Lead Actions:**
  - [ ] Approve applying `PROPOSED_PR19_AD_TG_WEBHOOK_DELEGATION.patch` to `app/routers/payments.py`.
  - [ ] Verify Homestay advertising payment regression (`tests/test_homestay_phase9_advertising_payments.py`) continues to pass 100%.

### 2. Optional Cross-Module Polish (Not Blocking 54/54)
- [ ] Approve adding `"tour_guide": "/api/v1/tour-guides"` to `DETAIL_PATHS` in `app/services/map_catalog.py` for Interactive Map deep-linking.
- [ ] Confirm `PR-16` scope: original Module 05 spec defines `PR-16` strictly as *"Read eligible traveler feedback"* (already complete). Only request a separate `dbo.reviews` migration if persistent guide text replies are added as a post-spec enhancement.

### 3. PR Creation & Release Readiness
- [ ] Approve committing local changes on `feature/tour-guide-backend-complete`, pushing branch, and opening PR to `develop`.
- [ ] After DB Coordinator applies Migration `034` + shared payment CHECK alignment + Team Lead approves the 9-line `AD-TG-` patch, re-run full test suite (`pytest -q`) and SQL Server staging smoke verification to certify **54/54 (100%)** verified completion.
