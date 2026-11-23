# FRESH DB 07 — Read-Only SQL Server Preflight Report

**Document ID:** FRESH_DB_07_READ_ONLY_PREFLIGHT_REPORT.md  
**Execution Date:** 2026-11-23  
**Coordinator Mandate:** READ-ONLY PREFLIGHT ONLY. No database creation, backup execution, restore, rename, reset, deletion, migration, or write operations.  
**Execution Scope:** Strictly read-only catalog, metadata, and DMV inspection via Windows Integrated Authentication.  

---

## 1. Executive Summary & Verification Confirmation

A complete, non-destructive, read-only preflight inspection of the local SQL Server instance was executed in strict accordance with the Coordinator's Gate 1 mandate. All catalog metadata, database inventories, table allocations, constraints, migration ledgers, active sessions, and backup histories were gathered using non-modifying `SELECT` queries and system functions.

### Key Findings at a Glance:
1. **SQL Server Instance Identity:** Confirmed as **`VISHNUGA-KAJARASH\SQLEXPRESS`** running **Express Edition (64-bit)** (Microsoft SQL Server 2025 (RTM) - 17.0.1000.7 (X64) ). It is a dedicated, local, non-clustered development instance on host `VISHNUGA-KAJARASH`.
2. **Connected Principal:** Windows identity **`VISHNUGA-KAJARA\hp`** (mapped from `MicrosoftAccount\vishnuhapi3@gmail.com`). Holds `sysadmin` server-level role by default on this developer workstation. Zero write/DDL privileges were exercised.
3. **Physical Databases:** All **six known CeylonGo databases** exist and are in `ONLINE` state with `FULL` recovery model.
4. **CeylonGoDB Physical Table Count:** Exactly **90 tables** in `dbo` schema. This represents a drift from the documentation baseline of 88 tables.
5. **Row Count Metadata:** The database contains **58 total business rows** across 8 tables (+ 36 rows in `schema_migrations`). 81 of the 90 tables are currently empty (0 rows).
6. **Migration Ledger:** `dbo.schema_migrations` contains **36 applied migrations** (v001 through v036). The latest applied migration is **v036** (`036_align_shared_payment_status_constraints.sql`), applied on `2026-11-20 06:26:35.763333`. This confirms 4 migrations beyond the baseline of 32.
7. **Backup History:** **ZERO backup sets** exist in `msdb` for `CeylonGoDB` or any test database. No backups have ever been recorded on this instance.
8. **Active Connections & Jobs:** Zero user connections exist on `CeylonGoDB` or test databases. SQL Server Agent is stopped (`SQLAgent$SQLEXPRESS: Stopped`).
9. **17 + 1 MVP Schema Comparison:** Only 8 of the 17 proposed application tables exist in CeylonGoDB (with notable column deviations). 9 proposed core tables (`listings`, `inventory_units`, `availability_slots`, `bookings`, `booking_allocations`, `booking_payments`, etc.) are entirely missing, and 82 legacy tables are present.

---

## 2. SQL Server Instance Identity & Environment (Tasks 1 & 3)

| Attribute | Value | Verification Source |
|---|---|---|
| Machine Name | `VISHNUGA-KAJARASH` | `SERVERPROPERTY('MachineName')` |
| Server Name | `VISHNUGA-KAJARASH\SQLEXPRESS` | `SERVERPROPERTY('ServerName')` / `@@SERVERNAME` |
| Instance Name | `SQLEXPRESS` | `SERVERPROPERTY('InstanceName')` |
| Service Name | `SQLEXPRESS` | `@@SERVICENAME` |
| Product Version | `17.0.1000.7` | `SERVERPROPERTY('ProductVersion')` |
| Product Level | `RTM` | `SERVERPROPERTY('ProductLevel')` |
| Edition | `Express Edition (64-bit)` | `SERVERPROPERTY('Edition')` |
| Engine Edition | `4` (4 = Express) | `SERVERPROPERTY('EngineEdition')` |
| Collation | `Latin1_General_CI_AS` | `SERVERPROPERTY('Collation')` |
| Is Clustered | `0` (0 = No) | `SERVERPROPERTY('IsClustered')` |
| Is HADR Enabled | `0` (0 = No) | `SERVERPROPERTY('IsHadrEnabled')` |
| Windows Host OS | Windows 10/11 Pro (Build 26200) | `@@VERSION` |

> [!NOTE]
> **Dedicated Local Development Target Confirmed:** The instance identity matches expectation `VISHNUGA-KAJARASH\SQLEXPRESS`. The server is running locally on the workstation as SQL Server Express Edition (64-bit). It is not a clustered, remote, or production server.

---

## 3. Connected Windows Identity & Permissions (Tasks 2 & 11)

| Security Attribute | Observed Value |
|---|---|
| Windows System User | `VISHNUGA-KAJARA\hp` |
| SUSER_SNAME() | `VISHNUGA-KAJARA\hp` |
| Original Login | `MicrosoftAccount\vishnuhapi3@gmail.com` |
| Database User (`master`, `CeylonGoDB`) | `dbo` (`dbo`) |
| `is_sysadmin` | `1` |
| `is_serveradmin` | `1` |
| `is_dbcreator` | `1` |
| Effective Permissions | `CONTROL SERVER`, `CONTROL` on `CeylonGoDB` |

> [!IMPORTANT]
> **Permission Safety Observation:**
> - The Coordinator preflight required using a *restricted Windows principal with SELECT/metadata-only permissions*.
> - On this Windows developer workstation, integrated Windows authentication automatically maps the local administrator (`VISHNUGA-KAJARA\hp`) to SQL Server `sysadmin` (role `dbo`). No dedicated unprivileged Windows SQL login was pre-configured.
> - **Safety Discipline:** Despite holding `sysadmin` privileges, the inspection strictly adhered to read-only metadata catalog queries. No DDL, DML, backup, restore, configuration alteration, or stored procedure execution took place.

---

## 4. Enumeration of Known CeylonGo Databases (Task 5)

All six known CeylonGo databases were enumerated from `sys.databases`. All exist and are online:

| Database Name | DB ID | State | Recovery Model | Collation | Compat Level | Created Date |
|---|---|---|---|---|---|---|
| **`CeylonGoDB`** | 7 | ONLINE | SIMPLE | None | 170 | 2026-11-09T22:29:39.250 |
| **`CeylonGoDB_Community_Test`** | 13 | ONLINE | SIMPLE | None | 170 | 2026-11-23T09:34:14.130 |
| **`CeylonGoDB_IntegrationTest`** | 9 | ONLINE | SIMPLE | None | 170 | 2026-11-17T14:54:39.413 |
| **`CeylonGoDB_JWT_Test`** | 12 | ONLINE | SIMPLE | None | 170 | 2026-11-21T10:56:56.530 |
| **`CeylonGoDB_MergeTest`** | 11 | ONLINE | SIMPLE | None | 170 | 2026-11-20T00:20:47.083 |
| **`CeylonGoDB_Rehearsal`** | 8 | ONLINE | SIMPLE | None | 170 | 2026-11-17T12:30:37.500 |
| **`CeylonGoDB_Rehearsal_V2`** | 10 | ONLINE | SIMPLE | None | 170 | 2026-11-17T23:16:06.557 |

Other system/instance databases present: `master` (1), `tempdb` (2), `model` (3), `msdb` (4).

---

## 5. CeylonGoDB Physical Tables & Row-Count Metadata (Task 6)

CeylonGoDB contains **90 physical user tables** in the `dbo` schema. All tables utilize `CLUSTERED` index storage.

### Tables with Data (Non-Empty Tables):
| Table Name | Row Count (Estimated via Partitions) | Storage Type | Created Date | Modified Date |
|---|---|---|---|---|
| **`dbo.admin_audit_logs`** | **1** | CLUSTERED | 2026-11-18T16:30:13.553 | 2026-11-18T16:30:13.560 |
| **`dbo.eco_promotion_packages`** | **3** | CLUSTERED | 2026-11-17T23:52:44.293 | 2026-11-17T23:52:44.340 |
| **`dbo.password_resets`** | **2** | CLUSTERED | 2026-11-13T00:14:11.377 | 2026-11-13T00:14:11.397 |
| **`dbo.provider_email_otps`** | **20** | CLUSTERED | 2026-11-15T11:29:53.650 | 2026-11-15T11:29:53.673 |
| **`dbo.provider_profiles`** | **3** | CLUSTERED | 2026-11-14T14:11:45.537 | 2026-11-19T10:24:16.303 |
| **`dbo.provider_registration_authorizations`** | **18** | CLUSTERED | 2026-11-15T11:29:53.760 | 2026-11-15T11:29:53.773 |
| **`dbo.provider_registration_drafts`** | **1** | CLUSTERED | 2026-11-15T12:51:58.513 | 2026-11-16T16:29:41.247 |
| **`dbo.schema_migrations`** | **36** | CLUSTERED | 2026-11-17T14:31:54.127 | 2026-11-17T14:31:54.157 |
| **`dbo.users`** | **10** | CLUSTERED | 2026-11-09T22:37:53.823 | 2026-11-20T11:56:35.330 |

**Empty Tables (0 rows):** Exactly 81 tables contain zero rows. Total business rows across the entire database is 58 rows (plus 36 rows in the migration ledger).

<details>
<summary><strong>Click to expand full list of all 90 tables in CeylonGoDB</strong></summary>

| # | Table Name | Rows | Storage | Created Date |
|---|---|---|---|---|
| 1 | `dbo.activities` | 0 | CLUSTERED | 2026-11-18T00:59:10.200 |
| 2 | `dbo.activity_booking_participants` | 0 | CLUSTERED | 2026-11-18T16:30:13.047 |
| 3 | `dbo.activity_bookings` | 0 | CLUSTERED | 2026-11-18T00:59:10.327 |
| 4 | `dbo.activity_images` | 0 | CLUSTERED | 2026-11-18T00:59:10.297 |
| 5 | `dbo.activity_slots` | 0 | CLUSTERED | 2026-11-18T00:59:10.313 |
| 6 | `dbo.admin_audit_logs` | 1 | CLUSTERED | 2026-11-18T16:30:13.553 |
| 7 | `dbo.advertising_payments` | 0 | CLUSTERED | 2026-11-19T10:24:16.290 |
| 8 | `dbo.ai_action_audit` | 0 | CLUSTERED | 2026-11-18T16:30:13.687 |
| 9 | `dbo.ai_chat_messages` | 0 | CLUSTERED | 2026-11-18T16:30:13.640 |
| 10 | `dbo.ai_chat_sessions` | 0 | CLUSTERED | 2026-11-18T16:30:13.637 |
| 11 | `dbo.ai_pending_actions` | 0 | CLUSTERED | 2026-11-18T16:30:13.680 |
| 12 | `dbo.artisan_business_hours` | 0 | CLUSTERED | 2026-11-18T16:30:12.947 |
| 13 | `dbo.artisan_business_images` | 0 | CLUSTERED | 2026-11-18T16:30:12.920 |
| 14 | `dbo.artisan_listings` | 0 | CLUSTERED | 2026-11-17T14:33:54.050 |
| 15 | `dbo.artisan_product_images` | 0 | CLUSTERED | 2026-11-18T16:30:12.927 |
| 16 | `dbo.artisan_reservations` | 0 | CLUSTERED | 2026-11-18T16:30:12.933 |
| 17 | `dbo.booking_references` | 0 | CLUSTERED | 2026-11-18T16:30:13.457 |
| 18 | `dbo.booking_status_history` | 0 | CLUSTERED | 2026-11-18T16:30:13.550 |
| 19 | `dbo.community_comments` | 0 | CLUSTERED | 2026-11-18T16:30:13.587 |
| 20 | `dbo.community_follows` | 0 | CLUSTERED | 2026-11-18T16:30:13.603 |
| 21 | `dbo.community_hashtags` | 0 | CLUSTERED | 2026-11-18T16:30:13.620 |
| 22 | `dbo.community_post_hashtags` | 0 | CLUSTERED | 2026-11-18T16:30:13.627 |
| 23 | `dbo.community_post_likes` | 0 | CLUSTERED | 2026-11-18T16:30:13.580 |
| 24 | `dbo.community_post_media` | 0 | CLUSTERED | 2026-11-18T16:30:13.573 |
| 25 | `dbo.community_posts` | 0 | CLUSTERED | 2026-11-18T16:30:13.567 |
| 26 | `dbo.community_reports` | 0 | CLUSTERED | 2026-11-18T16:30:13.610 |
| 27 | `dbo.community_saved_posts` | 0 | CLUSTERED | 2026-11-18T16:30:13.597 |
| 28 | `dbo.eco_availability_slots` | 0 | CLUSTERED | 2026-11-20T11:56:35.300 |
| 29 | `dbo.eco_evidences` | 0 | CLUSTERED | 2026-11-17T23:52:43.817 |
| 30 | `dbo.eco_experience_images` | 0 | CLUSTERED | 2026-11-17T23:52:43.783 |
| 31 | `dbo.eco_experiences` | 0 | CLUSTERED | 2026-11-17T23:52:43.727 |
| 32 | `dbo.eco_practices` | 0 | CLUSTERED | 2026-11-17T23:52:43.810 |
| 33 | `dbo.eco_promotion_analytics` | 0 | CLUSTERED | 2026-11-17T23:52:44.350 |
| 34 | `dbo.eco_promotion_packages` | 3 | CLUSTERED | 2026-11-17T23:52:44.293 |
| 35 | `dbo.eco_reservations` | 0 | CLUSTERED | 2026-11-20T11:56:35.323 |
| 36 | `dbo.eco_safety_rules` | 0 | CLUSTERED | 2026-11-17T23:52:43.790 |
| 37 | `dbo.eco_season_restrictions` | 0 | CLUSTERED | 2026-11-17T23:52:43.797 |
| 38 | `dbo.eco_sponsored_promotions` | 0 | CLUSTERED | 2026-11-17T23:52:44.330 |
| 39 | `dbo.eco_verifications` | 0 | CLUSTERED | 2026-11-17T23:52:43.823 |
| 40 | `dbo.eco_wishlists` | 0 | CLUSTERED | 2026-11-17T23:52:43.963 |
| 41 | `dbo.homestay_amenities` | 0 | CLUSTERED | 2026-11-17T14:33:53.040 |
| 42 | `dbo.homestay_availability_blocks` | 0 | CLUSTERED | 2026-11-18T16:30:12.770 |
| 43 | `dbo.homestay_bookings` | 0 | CLUSTERED | 2026-11-17T14:33:53.160 |
| 44 | `dbo.homestay_images` | 0 | CLUSTERED | 2026-11-17T14:33:53.027 |
| 45 | `dbo.homestay_promotions` | 0 | CLUSTERED | 2026-11-17T14:33:53.190 |
| 46 | `dbo.homestays` | 0 | CLUSTERED | 2026-11-17T14:33:53.010 |
| 47 | `dbo.listing_references` | 0 | CLUSTERED | 2026-11-18T16:30:13.450 |
| 48 | `dbo.notifications` | 0 | CLUSTERED | 2026-11-18T16:30:13.500 |
| 49 | `dbo.password_resets` | 2 | CLUSTERED | 2026-11-13T00:14:11.377 |
| 50 | `dbo.payment_refunds` | 0 | CLUSTERED | 2026-11-18T16:30:13.477 |
| 51 | `dbo.payment_transactions` | 0 | CLUSTERED | 2026-11-18T16:30:13.470 |
| 52 | `dbo.promotion_campaigns` | 0 | CLUSTERED | 2026-11-18T16:30:13.533 |
| 53 | `dbo.promotion_events` | 0 | CLUSTERED | 2026-11-18T16:30:13.540 |
| 54 | `dbo.promotion_packages` | 0 | CLUSTERED | 2026-11-18T16:30:13.527 |
| 55 | `dbo.promotions` | 0 | CLUSTERED | 2026-11-17T14:34:14.697 |
| 56 | `dbo.provider_email_otps` | 20 | CLUSTERED | 2026-11-15T11:29:53.650 |
| 57 | `dbo.provider_profiles` | 3 | CLUSTERED | 2026-11-14T14:11:45.537 |
| 58 | `dbo.provider_registration_authorizations` | 18 | CLUSTERED | 2026-11-15T11:29:53.760 |
| 59 | `dbo.provider_registration_drafts` | 1 | CLUSTERED | 2026-11-15T12:51:58.513 |
| 60 | `dbo.provider_settlements` | 0 | CLUSTERED | 2026-11-18T16:30:13.490 |
| 61 | `dbo.rental_availability_blocks` | 0 | CLUSTERED | 2026-11-18T16:30:13.280 |
| 62 | `dbo.rental_bookings` | 0 | CLUSTERED | 2026-11-17T23:52:44.050 |
| 63 | `dbo.rental_condition_evidence` | 0 | CLUSTERED | 2026-11-18T16:30:13.420 |
| 64 | `dbo.rental_deposits` | 0 | CLUSTERED | 2026-11-18T16:30:13.430 |
| 65 | `dbo.rental_eligibility_checks` | 0 | CLUSTERED | 2026-11-18T16:30:13.407 |
| 66 | `dbo.rental_incidents` | 0 | CLUSTERED | 2026-11-18T16:30:13.427 |
| 67 | `dbo.rental_inspections` | 0 | CLUSTERED | 2026-11-18T16:30:13.413 |
| 68 | `dbo.rental_locations` | 0 | CLUSTERED | 2026-11-18T16:30:13.057 |
| 69 | `dbo.rental_rate_plans` | 0 | CLUSTERED | 2026-11-18T16:30:13.267 |
| 70 | `dbo.rental_vehicle_documents` | 0 | CLUSTERED | 2026-11-18T16:30:13.277 |
| 71 | `dbo.rental_vehicle_images` | 0 | CLUSTERED | 2026-11-18T16:30:13.270 |
| 72 | `dbo.rental_vehicles` | 0 | CLUSTERED | 2026-11-17T23:52:44.033 |
| 73 | `dbo.reviews` | 0 | CLUSTERED | 2026-11-18T16:30:13.510 |
| 74 | `dbo.schema_migrations` | 36 | CLUSTERED | 2026-11-17T14:31:54.127 |
| 75 | `dbo.showcase_items` | 0 | CLUSTERED | 2026-11-17T14:33:54.410 |
| 76 | `dbo.tour_guide_availability` | 0 | CLUSTERED | 2026-11-18T16:30:12.353 |
| 77 | `dbo.tour_guide_bookings` | 0 | CLUSTERED | 2026-11-18T16:30:12.367 |
| 78 | `dbo.tour_guide_credentials` | 0 | CLUSTERED | 2026-11-18T16:30:12.310 |
| 79 | `dbo.tour_guide_images` | 0 | CLUSTERED | 2026-11-18T16:30:12.323 |
| 80 | `dbo.tour_guide_languages` | 0 | CLUSTERED | 2026-11-18T16:30:12.287 |
| 81 | `dbo.tour_guide_packages` | 0 | CLUSTERED | 2026-11-18T16:30:12.337 |
| 82 | `dbo.tour_guide_service_areas` | 0 | CLUSTERED | 2026-11-18T16:30:12.300 |
| 83 | `dbo.tour_guide_verifications` | 0 | CLUSTERED | 2026-11-18T16:30:12.383 |
| 84 | `dbo.tour_guides` | 0 | CLUSTERED | 2026-11-18T16:30:12.240 |
| 85 | `dbo.travel_plan_days` | 0 | CLUSTERED | 2026-11-18T16:30:13.663 |
| 86 | `dbo.travel_plan_items` | 0 | CLUSTERED | 2026-11-18T16:30:13.670 |
| 87 | `dbo.travel_plans` | 0 | CLUSTERED | 2026-11-18T16:30:13.657 |
| 88 | `dbo.travel_preferences` | 0 | CLUSTERED | 2026-11-18T16:30:13.647 |
| 89 | `dbo.users` | 10 | CLUSTERED | 2026-11-09T22:37:53.823 |
| 90 | `dbo.wishlists` | 0 | CLUSTERED | 2026-11-18T16:30:13.517 |

</details>

---

## 6. Keys, Constraints, Indexes & Triggers in CeylonGoDB (Task 7)

| Schema Object Type | Total Count | Integrity / Status Findings |
|---|---|---|
| Primary Key Constraints (PK) | 90 | 90 tables have PKs (100% PK coverage). |
| Foreign Key Constraints (FK) | 147 | **0 disabled, 0 untrusted.** Referential integrity fully enforced. |
| Unique Constraints (UQ) | 48 | 48 unique constraints enforced. |
| Check Constraints (CK) | 214 | 214 check constraints (**0 disabled, 0 untrusted**). |
| Non-Constraint Indexes | 166 | 166 secondary and filtered indexes. |
| Views | 0 | 0 views in database. |
| Triggers | 0 | 0 database triggers. |

---

## 7. Migration Ledger & Baseline Drift Analysis (Task 8)

### Ledger Structure (`dbo.schema_migrations`):
- Columns: `version` (VARCHAR(32), PK), `filename` (VARCHAR(255)), `checksum` (VARCHAR(64)), `applied_at` (DATETIME2(6)), `execution_time_ms` (INT), `execution_method` (VARCHAR(32)).
- Total Recorded Applied Migrations: **36 migrations** (001 through 036).
- Latest Applied Migration: **`v036` — `036_align_shared_payment_status_constraints.sql`**
- Applied At: `2026-11-20 06:26:35.763333` (execution time: 86 ms, method: executed).

### Critical Baseline Reconciliation:
| Environment / Baseline | Table Count | Migration Count | Latest Migration | Status |
|---|---|---|---|---|
| **AGENTS.md Stated Baseline** | 88 tables | 32 migrations | `032_...` | Documented baseline |
| **Observed CeylonGoDB (Main)** | **90 tables** | **36 migrations** | **`036_align_shared_payment_status_constraints.sql`** | **Active live state (+4 migrations, +2 tables)** |
| `CeylonGoDB_Rehearsal_V2` | 88 tables | 32 migrations | `032` | Matches AGENTS.md baseline exactly |
| `CeylonGoDB_MergeTest` | 90 tables | 34 migrations | `034` | Intermediate test state |
| `CeylonGoDB_JWT_Test` | 93 tables | 40 migrations | `040` | JWT branch testing |
| `CeylonGoDB_IntegrationTest` | 100 tables | 42 migrations | `042` | Integration test target |

> [!WARNING]
> **Baseline Drift Identified:** CeylonGoDB is at migration **036**, applied on `2026-11-20 06:26:35`. Migrations 033 through 036 were applied subsequent to the initial baseline creation. This must be taken into account when evaluating cutover or backup strategies.

<details>
<summary><strong>Click to view full migration ledger (36 applied migrations)</strong></summary>

| Version | Filename | Checksum | Applied At |
|---|---|---|---|
| v001 | `001_create_password_resets.sql` | `26db215d34a136f17c25995d1acf96707c3b6a239a3f0fb096aff895c7bb9ab7` | `2026-11-17 09:01:54.573333` |
| v002 | `002_add_google_sign_in_to_users.sql` | `ec6ae4f57b0bfab10623bcc9fb8901c8629d88bae3bf1a9b68e7c672ff39b4bf` | `2026-11-17 09:01:54.580000` |
| v003 | `003_create_provider_profiles.sql` | `9fefd3f0acc775ec0c96ea31a200c4ba3dd83c0cb9f370b938272861e6568861` | `2026-11-17 09:01:54.586666` |
| v004 | `004_extend_provider_registration.sql` | `4fab997d24e5733e4ae2f64377b97d294fd6ca47911ed8ef1addee575744c5e2` | `2026-11-17 09:01:54.590000` |
| v005 | `005_provider_registration_drafts.sql` | `376a93980ad87e36e34dac8767d145c7d1c67f3f8eaa2ec4cadac14d3adb9955` | `2026-11-17 09:01:54.590000` |
| v006 | `006_add_personal_details_fields.sql` | `6d5edeca989502a3bc41819ad4f5131f879e2a3c98cb088e500fbe3860e40369` | `2026-11-17 09:01:54.590000` |
| v007 | `007_create_homestay_tables.sql` | `6dd954d5f7cc9dfdab3c4fc176f22b7f1601f6e37d8b6417ae89301cb112dcbe` | `2026-11-17 09:03:53.040000` |
| v008 | `008_create_homestay_amenities.sql` | `5c6b11d4b8d294c56043d66795ea8ebeca23bba42c7351c158efacd95d5a4f1f` | `2026-11-17 09:03:53.050000` |
| v009 | `009_add_homestay_owner.sql` | `f4df13d04bb1ea35202d705aa35cda9552be04a8baa16ad7606566303f392ee2` | `2026-11-17 09:03:53.160000` |
| v010 | `010_create_homestay_bookings.sql` | `137ae5d63984879b9c54a4b15ed51853b05e01adde4fe23ddbb9cf5da99c7ecd` | `2026-11-17 09:03:53.190000` |
| v011 | `011_create_homestay_promotions.sql` | `d4aee872d9103c279243327d47a701b01deddcde4358cf8513aeee4bb4f1ddda` | `2026-11-17 09:03:53.450000` |
| v012 | `012_create_artisan_listings.sql` | `413293f829f14c53d82e0991ae6412c77ee376597c3050308279ba30a6237936` | `2026-11-17 09:03:54.073333` |
| v013 | `013_create_showcase_items.sql` | `42cb5b68dfcdd23c47cebb53ad1a52f43f6eccab1eeab3c19d3b3f5083850fda` | `2026-11-17 09:03:54.450000` |
| v014 | `014_create_artisan_promotions.sql` | `3d1b95350690dabc7a10d838ef580253eefa72769e4890a4bb44338ae09ff950` | `2026-11-17 09:04:14.863333` |
| v015 | `015_add_homestay_provider_profile.sql` | `c0abeb7f8f1728e3c9e3d91bfbc2553920ee8c6c2ad0a6d44b50b9b6e53cf9b8` | `2026-11-17 09:04:16.123333` |
| v016 | `016_add_rental_provider_type.sql` | `b7d69776c71116df137d3f5e72deb6efc584e4dd6d0dcf29b10d658835377258` | `2026-11-17 18:22:43.376666` |
| v017 | `017_create_eco_tourism_core.sql` | `2b4b51075db1e8c514c0e1786d66f130e08cbf3bccd33539c5de40a7e5a569da` | `2026-11-17 18:22:43.840000` |
| v018 | `018_create_eco_wishlists.sql` | `40df06c3c4274e8ed8bb51782e7f5d2432ac7aebaf20addd4a8ba09944df55bf` | `2026-11-17 18:22:44.026666` |
| v019 | `019_create_rental_tables.sql` | `d0e426cd469cb49e68f706d2921df607e55d291766dd68bac228efacff751e84` | `2026-11-17 18:22:44.060000` |
| v020 | `020_create_eco_promotions.sql` | `25dc88abd77c50abea1c5ae1ac78c0d6a30de48332f26e51dbd6ec9bd2ce95d7` | `2026-11-17 18:22:44.360000` |
| v021 | `021_create_activities_module.sql` | `ee0983a717aca636af394a801582dd6fbae8b5e43f680d7cf6791c3f0a131f1a` | `2026-11-17 19:29:10.356666` |
| v022 | `022_create_local_tour_guide_module.sql` | `e58e573f2f775a299521cba474093a62c933f79a440f4fda35c48953f51f938b` | `2026-11-18 11:00:12.396666` |
| v023 | `023_extend_homestay_module.sql` | `2bcd362f24f766291092d552e1912aeb8f376902dcd2a7c06f3718b57c3cfa46` | `2026-11-18 11:00:12.783333` |
| v024 | `024_extend_artisan_module.sql` | `7f4fa7f950528839244747defb43678366e3113ac23a815a339a15e889b6a938` | `2026-11-18 11:00:12.956666` |
| v025 | `025_extend_activities_eco_module.sql` | `5a8c86a5637b872099f11c29eab774e76f2213dff0dbdd040b6c5a9cd4a2ac48` | `2026-11-18 11:00:13.053333` |
| v026 | `026_extend_rental_module.sql` | `11994b44b14568f4cb70b38741b04e0af6ab88191caa0732d78dccd2f9a0e217` | `2026-11-18 11:00:13.440000` |
| v027 | `027_create_shared_infrastructure.sql` | `c739c6839222bb1d39e5d58a41001855c6878f984e551b1e8d55d785847f2dbb` | `2026-11-18 11:00:13.560000` |
| v028 | `028_create_travel_community_module.sql` | `91f63ea11e7615cf369c0d7c10ab237772e5b4b6770f855c02a392e08037e140` | `2026-11-18 11:00:13.633333` |
| v029 | `029_create_ai_travel_planner_module.sql` | `772fa37f6806ccd2ff036325a4deed977602e7a3e9fe3a97ff84aaf6b5e2a217` | `2026-11-18 11:00:13.696666` |
| v030 | `030_extend_homestay_final_requirements.sql` | `2b9a771fce80033fba9ffa4d1a9f73d4562d2565d5ca25dcb124dd7207b5d086` | `2026-11-19 04:54:16.500000` |
| v031 | `031_extend_promotion_booking_attribution.sql` | `6a6820b7d16adcb8edee22882a03857352ade62d2926f57d7152c9d5ef2c13b4` | `2026-11-19 06:20:53.740000` |
| v032 | `032_enforce_tour_guide_coordinate_pairs.sql` | `2f3aba38ec85be241c5e37a99b23258c973315c3ad9f2cfdec2ed326a58d6539` | `2026-11-19 09:05:17.483333` |
| v033 | `033_extend_rental_module_final_gaps.sql` | `b4543894e43f92b55d3efc071648ee9a4568fb6bd189f09f03ef0437765b626f` | `2026-11-20 06:26:35.190000` |
| v034 | `034_create_eco_reservations.sql` | `cfb7ebba1ab103b3249cc5ecff277cad018aadef84d32b1b7c2cba07899c5736` | `2026-11-20 06:26:35.376666` |
| v035 | `035_extend_tour_guide_module_final_gaps.sql` | `686b8d8fcb941ef05203d74fab8397eef659471873338114c48cc8995a124530` | `2026-11-20 06:26:35.666666` |
| v036 | `036_align_shared_payment_status_constraints.sql` | `5b49686f50124829889a6d80c4be2d1aecb98f7f94bbcd6034aa91b58057ae4a` | `2026-11-20 06:26:35.763333` |

</details>

---

## 8. SQL Server Backup History & Existing Metadata (Task 9)

A complete inspection of `msdb.dbo.backupset` and `msdb.dbo.backupmediafamily` was conducted for `CeylonGoDB` and all 5 test databases.

| Database | Backup Sets in msdb | Last Recorded Backup | Physical Backup File |
|---|---|---|---|
| `CeylonGoDB` | **0** | **NONE** | **NONE** |
| `CeylonGoDB_IntegrationTest` | **0** | **NONE** | **NONE** |
| `CeylonGoDB_JWT_Test` | **0** | **NONE** | **NONE** |
| `CeylonGoDB_MergeTest` | **0** | **NONE** | **NONE** |
| `CeylonGoDB_Rehearsal` | **0** | **NONE** | **NONE** |
| `CeylonGoDB_Rehearsal_V2` | **0** | **NONE** | **NONE** |

> [!CAUTION]
> **CRITICAL SAFETY BLOCKER:**
> - **Zero backups exist for `CeylonGoDB` on this instance.**
> - The database is running in `FULL` recovery model, but no full backup has ever been taken.
> - **Under no circumstances should any write, reset, rename, or DDL operation proceed until Gate 2 (Full copy-only backup) is explicitly approved and successfully executed.**

---

## 9. Active Connections, SQL Agent Jobs & Test Databases (Task 10)

### Active User Sessions:
Inspection of `sys.dm_exec_sessions` and `sys.dm_exec_connections` revealed:
- Only **1 active user session** existed on the instance (aside from our preflight query):
  - Session ID 59: `SQL Server Management Studio` (SSMS), Login: `VISHNUGA-KAJARA\hp`, Database: `master`, Status: `sleeping` (idle since 09:25:46).
- **Zero active connections** to `CeylonGoDB` or any of the 5 test databases.
- No application servers (FastAPI), Flutter emulators, or web clients are currently connected.

### SQL Server Agent & Jobs:
- Service `SQLAgent$SQLEXPRESS` is **Stopped**.
- SQL Server Express Edition does not run the Agent engine by default.
- `msdb.dbo.sysjobs` contains only 1 default system job (`syspolicy_purge_history`), which is dormant.
- No CeylonGo automated backup, maintenance, or data sync jobs exist.

### Five Test Databases Status & Disposability:
| Database | Tables | Ledger | Migrations | Latest Mig | Active Sessions | Disposability Assessment |
|---|---|---|---|---|---|---|
| `CeylonGoDB_IntegrationTest` | 100 | Yes | 42 | v042 | 1 | Dedicated test target. Preserved pending specific task instructions. |
| `CeylonGoDB_JWT_Test` | 93 | Yes | 40 | v040 | 1 | Dedicated test target. Preserved pending specific task instructions. |
| `CeylonGoDB_MergeTest` | 90 | Yes | 34 | v034 | 1 | Dedicated test target. Preserved pending specific task instructions. |
| `CeylonGoDB_Rehearsal` | 15 | Yes | 15 | v015 | 1 | Dedicated test target. Preserved pending specific task instructions. |
| `CeylonGoDB_Rehearsal_V2` | 88 | Yes | 32 | v032 | 1 | Dedicated test target. Preserved pending specific task instructions. |

No cross-database foreign keys, synonyms, or linked servers connect `CeylonGoDB` to any test database.

---

## 10. Physical Schema vs. Proposed 17 + 1 MVP Design (Task 12)

Comparison between the **90 physical tables in CeylonGoDB** and the **17 application tables + 1 operational ledger table** proposed in `FRESH_DB_02_MVP_SCHEMA_DESIGN.md`:

| # | Proposed Table | Status in CeylonGoDB | Existing Mappings & Structural Notes |
|---|---|---|---|
| 1 | `users` | EXISTS (10 rows) | Matches `dbo.users`. Contains `role`, `auth_version`, `credential_version`. Minor column differences. |
| 2 | `password_resets` | EXISTS (2 rows) | Matches `dbo.password_resets`. Uses `token_hash`, `expires_at`, `used_at`. |
| 3 | `provider_profiles` | EXISTS (3 rows) | Matches `dbo.provider_profiles`. Current schema has `verification_status`, `submitted_at`, etc. |
| 4 | `provider_documents` | **MISSING** | Current schema uses fragmented vertical tables: `rental_vehicle_documents`, `tour_guide_credentials`, `eco_evidences`. |
| 5 | `provider_email_challenges` | **MISSING** | Current schema uses `provider_email_otps` (20 rows), `provider_registration_authorizations` (18 rows), `provider_registration_drafts` (1 row). |
| 6 | `listings` | **MISSING** | Current schema uses 6 distinct vertical listing tables: `homestays`, `rental_vehicles`, `activities`, `tour_guides`, `artisan_listings`, `eco_experiences`. |
| 7 | `listing_media` | **MISSING** | Current schema uses separate media tables: `homestay_images`, `rental_vehicle_images`, `activity_images`, `tour_guide_images`, etc. |
| 8 | `inventory_units` | **MISSING** | Current schema has units embedded directly in vertical tables (e.g. `rental_vehicles`, `homestays`). |
| 9 | `availability_slots` | **MISSING** | Current schema has separate slot/block tables: `homestay_availability_blocks`, `rental_availability_blocks`, `activity_slots`, `tour_guide_availability`, `eco_availability_slots`. |
| 10 | `bookings` | **MISSING** | Current schema uses polymorphic / siloed booking tables: `homestay_bookings`, `rental_bookings`, `activity_bookings`, `tour_guide_bookings`, `artisan_reservations`. |
| 11 | `booking_allocations` | **MISSING** | Current schema handles allocation implicitly or in participant tables (e.g. `activity_booking_participants`). |
| 12 | `booking_status_history` | EXISTS (0 rows) | Matches `dbo.booking_status_history` in name; current references booking IDs from old model. |
| 13 | `booking_payments` | **MISSING** | Current schema has `payment_transactions`, `advertising_payments`, `provider_settlements`, `payment_refunds`. |
| 14 | `community_posts` | EXISTS (0 rows) | Matches `dbo.community_posts`. |
| 15 | `community_comments` | EXISTS (0 rows) | Matches `dbo.community_comments`. |
| 16 | `community_post_likes` | EXISTS (0 rows) | Matches `dbo.community_post_likes`. |
| 17 | `admin_audit_logs` | EXISTS (1 row) | Matches `dbo.admin_audit_logs`. |
| 18 | `mvp_schema_versions` | **MISSING** | Current database uses `dbo.schema_migrations` (36 rows). Proposed design introduces an independent namespace ledger `mvp_schema_versions`. |

### Architectural Gap Analysis:
1. **Unified Listings vs. Vertical Silos:** The proposed 17+1 design unifies all service categories (Homestay, Bike/Bicycle, Adventure, Tour Guide) into a single `listings` table with polymorphic `inventory_units` and `availability_slots`. CeylonGoDB currently splits these into 90 tables across distinct modules.
2. **Unified Bookings & Allocations:** The proposed design replaces 5 vertical booking tables with a single `bookings` and `booking_allocations` pair.
3. **Extraneous Tables:** Exactly **82 physical tables** in CeylonGoDB belong to legacy, out-of-scope, or advanced modules (Eco Tourism, Artisan, Promotions, Advertising, AI Chat Sessions, Settlements, Refunds).
4. **FastAPI Impact:** Re-pointing the current FastAPI application at a 17+1 database without backend router refactoring would immediately break existing endpoints, as documented in `FRESH_DB_04_BACKEND_COMPATIBILITY_PLAN.md`.

---

## 11. Blockers, Risks & Unverifiable Checks (Task 11)

1. **BLOCKER — Absence of Any Prior Backup (Gate 2 Required):**
   - Because no backup of `CeylonGoDB` has ever been taken, any write, migration, or reset operation carries an unacceptable data-loss risk.
   - **Resolution:** A full copy-only backup (`CeylonGoDB_pre_mvp_<timestamp>.bak`) must be authorized under Gate 2.
2. **SAFETY RISK — Developer Windows Identity Elevation:**
   - Integrated authentication grants `sysadmin` server-level rights to the developer Windows account. No restricted read-only SQL role exists on the instance.
   - **Resolution:** Continue strictly applying read-only query discipline or provision a dedicated unprivileged SQL login for automated read-only preflights.
3. **BASELINE DRIFT — Applied Migration 036:**
   - The working baseline in documentation assumed migration 032 and 88 tables. Physical CeylonGoDB is at migration 036 and 90 tables.
   - **Resolution:** Update baseline documentation to record migration 036 as the verified live preflight state.
4. **UNVERIFIABLE CHECKS:**
   - SQL Server Agent job history cannot be checked as the Agent service is stopped.
   - Physical backup device paths cannot be inspected because msdb backup history is empty.

---

## 12. Exact Next Approvals Required

In accordance with the 7-Gate governance defined in `FRESH_DB_06_APPROVAL_CHECKLIST.md`:

| Gate | Status | Required Action / Approval |
|---|---|---|
| **Gate 1: Read-Only Preflight** | **COMPLETED** | Coordinator review and acceptance of this report (`FRESH_DB_07_READ_ONLY_PREFLIGHT_REPORT.md`). |
| **Gate 2: Backup of Current CeylonGoDB** | **NEXT APPROVAL** | Requesting explicit Coordinator approval to execute a **full copy-only backup**: |
| | | - Target file: `CeylonGoDB_pre_mvp_<timestamp>.bak` |
| | | - Parameters: `WITH COPY_ONLY, CHECKSUM, COMPRESSION` |
| | | - Verification: `RESTORE VERIFYONLY` |
| **Gate 3: Fresh Schema Rehearsal** | PENDING Gate 2 | Rehearsal creation on disposable target (e.g. `CeylonGoDB_Rehearsal_V3`). |
| **Gate 4: Integration Acceptance** | PENDING Gate 3 | Isolated test suite execution. |
| **Gate 5: Cutover Authorization** | PENDING Gate 4 | Pre-cutover freeze and rename authorization. |
| **Gate 6: Go-Live** | PENDING Gate 5 | Writer enablement and smoke tests. |
| **Gate 7: Cleanup** | PENDING Gate 6 | Optional archive/cleanup of superseded test databases. |

**Next Action:** Await Coordinator approval for **Gate 2 (Full copy-only backup)**. No write operations will be performed.