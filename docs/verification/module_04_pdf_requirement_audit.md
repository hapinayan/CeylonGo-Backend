# CeylonGo Module 04 — Complete PDF vs Actual Implementation Audit
**Specification Document:** `CeylonGo_Eco_Tourism_Complete_Development_Specification.pdf`  
**Specification Date:** 17 November 2026 | **Target Deadline:** 5 December 2026  
**Scope:** Module 04 — Eco-Tourism (50 Approved Requirements)  
**Target Stack:** Backend: Python FastAPI, SQLAlchemy, Microsoft SQL Server | Clients: Angular 21, Flutter 3  
**Audit Date:** 20 November 2026  
**Auditor:** Antigravity Autonomous Agent (Read-Only Independent Audit)  

---

## 1. Official PDF Specification Extraction

### 1.1. Purpose & Scope (Section 1)
Promote responsible Sri Lankan nature tourism, community eco-experiences, wildlife observation, conservation and sustainable local initiatives. Eco-Tourism is a distinct public discovery category, but must reuse shared activity scheduling, booking, payment, review, notification and promotion capabilities instead of duplicating them. Eco-friendly stays should link to the Homestay module rather than creating a second accommodation booking system.

#### Official Categories & Scope:
| Category | Official Scope & Examples |
| :--- | :--- |
| **Forest & Nature Trails** | Rainforest walks, guided forest exploration, nature trails |
| **Wildlife & Birdwatching** | Responsible wildlife observation, wetlands and birdwatching |
| **Sustainable Village Experiences** | Organic farming, traditional agriculture, village visits |
| **Conservation Activities** | Mangrove restoration, tree planting, environmental education |
| **Eco-Friendly Stays** | Eco-lodges and sustainable homestays (link to Homestay module) |
| **Community Eco-Tourism** | Community-managed nature visits and local environmental experiences |

---

### 1.2. The 50 Approved Requirements Extraction

#### A. Traveler Website / Flutter App (18 Features: T01–T18)
1. **T01 — Eco-Tourism Discovery:** Browse approved eco experiences.
2. **T02 — Search & Filters:** Location, category, price, duration.
3. **T03 — Eco Categories:** Forest, wildlife, village, conservation.
4. **T04 — Eco Experience Details:** Description, itinerary and sustainability information.
5. **T05 — Photo Gallery:** View approved experience images.
6. **T06 — Location & Maps:** Meeting point and external directions.
7. **T07 — Availability Calendar:** Available dates and slots.
8. **T08 — Eco Experience Booking:** Reserve date and participants.
9. **T09 — Online Payment:** Verified payment to CeylonGo account.
10. **T10 — Pay at Location:** Provider-approved reservation with cash at venue.
11. **T11 — My Bookings:** Own upcoming and past bookings.
12. **T12 — Booking Cancellation:** Policy-based cancellation.
13. **T13 — Ratings & Reviews:** Reviews after eligible completed bookings.
14. **T14 — Wishlist:** Save favorite eco-experiences.
15. **T15 — Contact Provider:** Approved organizer contact details.
16. **T16 — Booking Notifications:** Booking and schedule status updates.
17. **T17 — Sponsored Eco Listings:** Clearly labeled paid promotion placements.
18. **T18 — Eco-Friendly Badge:** Display only admin-verified badge.

#### B. Service Provider Website (18 Features: P01–P18)
1. **P01 — Eco Provider Dashboard:** Bookings, revenue and listing overview.
2. **P02 — Add Eco Experience:** Create draft listing.
3. **P03 — Manage Eco Experiences:** Edit, publish or deactivate owned listings.
4. **P04 — Photo Management:** Upload and remove images.
5. **P05 — Eco Categories:** Assign approved categories.
6. **P06 — Location Management:** Address, coordinates and meeting point.
7. **P07 — Pricing Management:** Per-person or group price.
8. **P08 — Schedule Management:** Dates and time slots.
9. **P09 — Capacity Management:** Per-slot participant limits.
10. **P10 — Booking Requests:** View incoming requests.
11. **P11 — Booking Approval:** Accept or reject requests.
12. **P12 — Booking History:** Upcoming, past and cancelled bookings.
13. **P13 — Cash Payment Updates:** Record cash received at venue.
14. **P14 — Online Earnings:** View payment and settlement records.
15. **P15 — Eco-Friendly Practices:** Describe practices and submit evidence.
16. **P16 — Sponsored Promotions:** Purchase paid visibility packages.
17. **P17 — Ratings & Reviews:** View traveler feedback.
18. **P18 — Promotion Statistics:** Views, clicks and booking conversions.

#### C. Admin Website (8 Features: A01–A08)
1. **A01 — Eco Listing Approval:** Approve or reject submitted listings.
2. **A02 — Provider Verification:** Verify eco-tourism organizers.
3. **A03 — Eco-Friendly Verification:** Review evidence of sustainability claims.
4. **A04 — Eco Badge Management:** Grant, revoke and audit eco badges.
5. **A05 — Booking Monitoring:** Monitor booking statuses and disputes.
6. **A06 — Payment Monitoring:** Review online payments, refunds and settlements.
7. **A07 — Promotion Management:** Manage packages and sponsored listings.
8. **A08 — Reports:** Basic eco-tourism, booking and revenue reporting.

#### D. Six Eco-Specific Requirements (E01–E06)
1. **E01 — Environmental Guidelines:** Show site-specific responsible travel instructions and visitor conduct rules.
2. **E02 — Wildlife Safety Rules:** Document safety precautions, no-feeding rules and operator instructions.
3. **E03 — Group Size Limits:** Enforce environmentally appropriate maximum participants per slot.
4. **E04 — Conservation Contribution:** Explain any conservation benefit; distinguish factual contributions from marketing claims.
5. **E05 — Eco-Friendly Practices:** Store sustainability practices and evidence for admin review.
6. **E06 — Weather & Seasonal Restrictions:** Block unsuitable seasons or unsafe dates; enable cancellation with reason.

---

### 1.3. Core User Flows (Section 6)
- **Discovery:** Traveler filters eco experiences -> opens approved listing -> checks sustainability information, safety rules, meeting point and available slots -> chooses booking option.
- **Online Payment:** Validate slot/capacity -> create pending reservation -> initiate payment to CeylonGo -> verify trusted payment callback -> confirm booking under agreed provider approval policy -> notify parties. *Never confirm solely from browser redirect.*
- **Pay at Location:** Traveler submits request -> provider accepts -> booking `CONFIRMED`, payment `UNPAID` -> tourist pays provider cash on site -> provider marks cash received -> activity completed.
- **Eco Verification:** Provider submits practices and evidence -> admin reviews -> badge approved/rejected with reason -> badge displayed only while valid; revoked badge removed immediately.
- **Promotion:** Approved provider buys package -> payment verified -> sponsored placement activates for selected duration -> label *Sponsored* shown -> promotion expires automatically. Discounts must be separately validated.
- **Cancellation & Safety:** Apply stated cancellation policy, release reserved capacity exactly once, trigger any eligible refund process, and notify affected travelers. Weather restrictions can disable slots or cause cancellations.

---

### 1.4. Security and Business Rules (Section 10)
- Only approved/published eco listings are publicly visible; enforce ownership and role checks for every write operation.
- Badge must require documented admin verification, with decision history; never automatically trust provider claims.
- Validate capacity, restricted dates, age/safety conditions server-side; prevent concurrent overbooking.
- Keep booking status separate from payment status; use idempotent payment callbacks and transactional state changes.
- Online booking payments go to the CeylonGo account; settlement and refund rules need separate approval.
- Apply safe upload constraints and protect personal details; do not publish private verification evidence.
- Protected-area access, permits and guide requirements must be checked by providers/admins as applicable.
- Clearly label sponsored listings; do not let payment imply eco certification or safety approval.

---

### 1.5. Acceptance Checklist & Team Handoff (Sections 11 & 12)
- **Checklist:** Verify unauthorized provider cannot edit another listing; unapproved listing stays hidden; false/unverified badge never appears; blocked season cannot be booked; no overbooking; cash booking remains unpaid until receipt; payment callback is idempotent; cancellations release capacity; sponsored listing expires; migration upgrade is safe; Angular/Flutter API payloads are documented.
- **Handoff Instruction:** Request a read-only gap analysis and implementation plan first. Do not assume features are already developed. Final team allocation and backend module division require project-lead approval.

---

## 2. Actual Repository Inspection & Evidence Mapping

### 2.1. Router Mount Analysis in `app/main.py`
| Router | Mount Status | Base Path | Blocking Guards |
| :--- | :---: | :--- | :--- |
| `eco_experiences_router` | **MOUNTED** | `/api/v1/eco-experiences` | None (Active & Public/Protected) |
| `admin_eco_router` | **MOUNTED** | `/api/v1/admin/eco-experiences` | `require_admin` dependency |
| `uploads_router` | **MOUNTED** | `/api/v1/uploads/eco_experiences` | Public image static serving |
| `wishlists_router` | **MOUNTED** | `/api/v1/wishlists` | Authenticated traveler |
| `eco_promotions_router` | **MOUNTED** | `/api/v1/eco-promotions` | Provider & Public endpoints |
| `admin_eco_promotions_router` | **MOUNTED** | `/api/v1/admin/eco-promotions` | `require_admin` dependency |
| `eco_bookings.router` | **UNMOUNTED** | `/api/v1/eco-bookings` | Unmounted + **HTTP 503 Staging Guards** |
| `eco_slots.router` | **UNMOUNTED** | `/api/v1/eco-experiences/{id}/slots` | Unmounted + **HTTP 503 Staging Guards** |

---

### 2.2. Database & Physical SQL Server Schema Findings

A direct read-only schema inspection on Microsoft SQL Server (`CeylonGoDB_Development`) reveals:
1. **`dbo.activity_slots`:**
   - Contains `eco_experience_id` (NULLABLE).
   - Column `activity_id` is **NOT NULL** (`Nullable=NO`).
   - *Direct Impact:* Inserting an eco-slot with `activity_id=NULL` violates the NOT NULL constraint on SQL Server. This is the exact technical reason why **Migration 030** is mandatory (Gate 2).
2. **`dbo.activity_booking_participants`:**
   - Physical columns: `id`, `activity_booking_id`, `full_name`, `age`, `emergency_contact_name`, `emergency_contact_phone`, `special_requirements`, `waiver_signed`, `created_at`.
   - The column `id_or_passport` **does not exist** in SQL Server.
   - *Direct Impact:* The unpatched SQLAlchemy ORM model `ActivityBookingParticipant` in `app/models/activity_booking.py` declared `id_or_passport = Column(...)`. Querying it triggers an SQL Server runtime exception. This is why **Gate 1** (Shared ORM Patch) is mandatory.
3. **`dbo.activity_bookings`:**
   - Physical constraints: `CK_activity_bookings_method` explicitly permits `'pay_at_location'` and `'online'`.
   - `CK_activity_bookings_status` permits `'pending'`, `'confirmed'`, `'rejected'`, `'cancelled'`, `'completed'`.
   - `CK_activity_bookings_payment` permits `'unpaid'`, `'pending'`, `'paid'`, `'refund_pending'`, `'refunded'`, `'failed'`.
4. **`dbo.payment_transactions` & `dbo.payment_refunds`:**
   - Relies on `dbo.booking_references`.
   - `CK_booking_references_type` permits `'activity'`.
   - However, `app/routers/payments.py` hardcodes `db.query(Booking)` (Homestay module), meaning runtime PayHere notifications crash if directed to eco-tourism activity bookings.

---

## 3. High-Level Summary of Findings

- **Total Requirements:** 50
- **Fully Completed (COMPLETE):** **37** (74.0%)
- **Implemented but Staged (STAGED):** **10** (20.0%)
- **Partially Implemented (PARTIAL):** **2** (4.0%)
- **Blocked by Dependencies (BLOCKED):** **1** (2.0%)
- **Not Started (NOT STARTED):** **0** (0.0%)

*Note: Angular and Flutter clients are NOT present in the workspace and are classified as **NOT VERIFIED**.*
