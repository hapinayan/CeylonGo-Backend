# CeylonGo Module 04 — E06: Weather & Seasonal Restrictions
## Live End-to-End Verification Plan

**Document Version**: 1.0  
**Date**: 20 November 2026  
**Project**: CeylonGo-Backend (`develop` branch)  
**Requirement**: **E06 — Weather & Seasonal Restrictions**  
**Authoritative Specification**: `CeylonGo_Eco_Tourism_Complete_Development_Specification.pdf` (17 November 2026)  
**Current Baseline**: PARTIAL (Implementation complete; verified on test doubles; live verification gated)  

---

## 1. Overview & Objectives

Requirement **E06 (Weather & Seasonal Restrictions)** protects travelers and natural habitats by enforcing:
1. Planned seasonal closures (monsoon season, wildlife breeding, park closures).
2. Urgent weather & environmental emergency cancellations initiated by authorized providers.
3. 100% full refund queueing for affected travelers (without assuming external PayHere completion).
4. Automated capacity restoration and bidirectional notification dispatch.

This verification plan specifies the exact execution steps, assertions, and safety measures for all **15 E06 verification scenarios** on live Microsoft SQL Server infrastructure.

---

## 2. End-to-End Verification Scenarios (15 Scenarios)

| # | Scenario | Endpoint & Method | Role | Key Input Parameters | Expected Response | Database State Assertions |
|---|---|---|---|---|---|---|
| **E01** | Authorized provider declares weather emergency | `POST /api/v1/eco-experiences/{id}/weather-emergency` | `provider` (approved, owner) | `hazard_type="weather_alert"`, reason $\ge 5$ chars, valid dates. | `HTTP 200 OK`<br>`status="success"` | New `EcoSeasonRestriction` created; restriction ID returned. |
| **E02** | Unauthorized provider is rejected | `POST /api/v1/eco-experiences/{id}/weather-emergency` | Non-owner or unapproved | Valid payload. | `HTTP 403 Forbidden` | Zero database modifications; no slots deactivated. |
| **E03** | Affected slots become inactive | Verified via DB query & API | `provider` | Target date window. | Slots in window return `is_active=0` | `SELECT is_active FROM dbo.activity_slots WHERE id = :slot_id` returns `0`. |
| **E04** | Pending & confirmed bookings cancelled | Verified via DB query & API | `provider` | Emergency declaration execution. | Bookings marked `cancelled` | `SELECT booking_status, cancellation_reason FROM dbo.activity_bookings WHERE id = :b_id` returns `cancelled` with hazard explanation. |
| **E05** | Reserved capacity is released | Verified via DB query | `provider` | Emergency declaration execution. | `reserved_count` decremented | `reserved_count` equals previous count minus cancelled participants. |
| **E06** | Paid bookings create pending refund records | Verified via DB query | `provider` | Bookings with `payment_status="paid"` | `refunds_initiated_count > 0` | Row inserted into `dbo.payment_refunds` with `refund_status="pending"` (NEVER completed). |
| **E07** | Duplicate refunds are prevented | Sequential re-execution | `provider` | Same date window. | `refunds_initiated_count = 0` | Sum of refunds in `dbo.payment_refunds` never exceeds `PaymentTransaction.amount`. |
| **E08** | Traveler & provider notifications generated | Verified via `GET /api/v1/notifications` | `traveler` / `provider` | Emergency declaration execution. | New unread notifications | Traveler receives `eco_weather_cancellation` (100% refund note); Provider receives operational summary. |
| **E09** | Seasonal closures block slot creation | `POST /api/v1/eco-slots` | `provider` | Slot timing inside active restriction. | `HTTP 409 Conflict` (or 422) | `ECO_SEASON_RESTRICTED`: slot creation rejected; zero rows added to `dbo.activity_slots`. |
| **E10** | Seasonal closures block booking submissions | `POST /api/v1/eco-bookings` | `traveler` | Slot falling within restriction dates. | `HTTP 409 Conflict` | `ECO_SEASON_RESTRICTED`: booking rejected; zero rows added to `dbo.activity_bookings`. |
| **E11** | T07 public discovery hides restricted slots | `GET /api/v1/eco-experiences/{id}/slots` | Public / Anonymous | Experience ID. | List of available slots | Slots overlapping active seasonal restrictions are completely omitted from response. |
| **E12** | Post-slot closure immediately hides & blocks | Applied after slots pre-exist | `provider` / Public | Declare restriction on pre-existing slots. | T07 hides slots; T08 rejects bookings | Dynamically enforced without requiring manual slot deletion. |
| **E13** | Repeated emergency calls remain idempotent | `POST /api/v1/eco-experiences/{id}/weather-emergency` | `provider` | Identical payload sent twice. | `HTTP 200 OK` on retry | `affected_slots_count=0`, `cancelled_bookings_count=0`, `refunds_initiated_count=0`. |
| **E14** | Transaction rollback preserves state on failure | Simulated DB error in atomic block | `provider` | Trigger deliberate SQL error. | `HTTP 500` / Exception | Savepoint rollback: zero slots deactivated, zero bookings cancelled. |
| **E15** | Concurrent emergency requests remain safe | Concurrent threads on same experience | `provider` | Simultaneous emergency declarations. | One succeeds; second handles idempotently | `sp_getapplock` serializes execution; zero race conditions or double cancellations. |

---

## 3. Financial Refund Integrity Rule (Critical Mandate)

```
+---------------------------------------------------------------------------------------------------+
|                                  STRICT FINANCIAL SAFETY NOTICE                                   |
+---------------------------------------------------------------------------------------------------+
|  1. A pending refund record in dbo.payment_refunds (refund_status = 'pending') represents an       |
|     authorized traveler entitlement to a 100% full refund due to environmental disruption.        |
|                                                                                                   |
|  2. It is strictly FORBIDDEN to treat a pending refund as a completed or disbursed PayHere refund.|
|                                                                                                   |
|  3. Under no circumstances should live outbound PayHere API refund calls be made during           |
|     verification tests.                                                                           |
|                                                                                                   |
|  4. Live gateway disbursement requires finance administrator processing through verified payment  |
|     disbursement infrastructure.                                                                  |
+---------------------------------------------------------------------------------------------------+
```

---

## 4. Scenario Execution Specifications

### Scenario E01, E03, E04, E05, E06, E08: Full Emergency Declaration
```bash
# Provider declares weather emergency affecting upcoming weekend
curl -X POST "https://api.staging.ceylongo.com/api/v1/eco-experiences/101/weather-emergency" \
  -H "Authorization: Bearer $PROVIDER_JWT" \
  -H "Content-Type: application/json" \
  -d '{
    "hazard_type": "weather_alert",
    "reason": "Department of Meteorology issued Red Alert for torrential rain and landslide risk.",
    "start_date": "2026-12-05",
    "end_date": "2026-12-07"
  }'

# Expected Response:
# HTTP 200 OK
# {
#   "eco_experience_id": 101,
#   "hazard_type": "weather_alert",
#   "reason": "Department of Meteorology issued Red Alert...",
#   "restriction_id": 402,
#   "affected_slots_count": 3,
#   "cancelled_bookings_count": 5,
#   "released_capacity_total": 12,
#   "refunds_initiated_count": 2,
#   "total_refund_amount": 24000.00,
#   "cancelled_booking_ids": [501, 502, 503, 504, 505],
#   "status": "success",
#   "message": "Weather emergency declared successfully. 3 slot(s) deactivated, 5 booking(s) cancelled, 2 refund(s) initiated."
# }
```

### Scenario E02: Unauthorized Emergency Declaration
```bash
# Non-owner provider attempts to declare emergency on listing 101
curl -X POST "https://api.staging.ceylongo.com/api/v1/eco-experiences/101/weather-emergency" \
  -H "Authorization: Bearer $UNAUTHORIZED_PROVIDER_JWT" \
  -H "Content-Type: application/json" \
  -d '{
    "hazard_type": "weather_alert",
    "reason": "Unauthorized closure attempt.",
    "start_date": "2026-12-05",
    "end_date": "2026-12-07"
  }'

# Expected Response: HTTP 403 Forbidden
# {"detail": "Access denied: You do not own this eco-experience listing."}
```

### Scenario E09: Seasonal Closure Blocks Slot Creation
```bash
# Provider attempts to schedule slot during breeding closure
curl -X POST "https://api.staging.ceylongo.com/api/v1/eco-slots" \
  -H "Authorization: Bearer $PROVIDER_JWT" \
  -H "Content-Type: application/json" \
  -d '{
    "eco_experience_id": 101,
    "starts_at": "2026-12-06T08:00:00Z",
    "ends_at": "2026-12-06T12:00:00Z",
    "capacity": 8
  }'

# Expected Response: HTTP 409 Conflict (or 422)
# {"detail": "[ECO_SEASON_RESTRICTED] Slot date range overlaps with active seasonal restriction 'weather_alert'..."}
```

### Scenario E10: Seasonal Closure Blocks Booking Submission
```bash
# Traveler attempts to book pre-existing slot during closure
curl -X POST "https://api.staging.ceylongo.com/api/v1/eco-bookings" \
  -H "Authorization: Bearer $TRAVELER_JWT" \
  -H "Content-Type: application/json" \
  -d '{
    "eco_slot_id": 10502,
    "participants": 2,
    "payment_method": "pay_at_location"
  }'

# Expected Response: HTTP 409 Conflict
# {"detail": "Cannot book slot: experience has an active seasonal or environmental restriction..."}
```

### Scenario E11 & E12: Public Availability Calendar Hides Closed Dates
```bash
# Public user queries available slots
curl -X GET "https://api.staging.ceylongo.com/api/v1/eco-experiences/101/slots"

# Expected Response: HTTP 200 OK
# Response array contains ONLY slots outside 2026-12-05 to 2026-12-07.
# Closed slots (even if is_active was not yet updated) are strictly excluded.
```

---

## 5. Verification Fixtures & Test Data Matrix

| Fixture | Properties | Purpose |
|---|---|---|
| `exp_monsoon_target` | Approved, published, owned by provider | Target for emergency declaration and seasonal closure. |
| `slot_in_hazard_window_1` | Future slot, `starts_at = 2026-12-05 08:00` | Will be deactivated (`is_active = 0`). |
| `slot_in_hazard_window_2` | Future slot, `starts_at = 2026-12-06 09:00` | Will be deactivated (`is_active = 0`). |
| `slot_outside_hazard_window` | Future slot, `starts_at = 2026-12-15 08:00` | Must remain active (`is_active = 1`). |
| `booking_paid_online` | `booking_status="confirmed"`, `payment_status="paid"` | Generates `PaymentRefund(refund_status="pending")`. |
| `booking_unpaid_cash` | `booking_status="pending"`, `payment_status="unpaid"` | Cancels booking; zero refund record generated. |

---

## 6. Rollback & Cleanup Procedures

```sql
-- Teardown script for E06 live verification tests
DELETE FROM dbo.payment_refunds WHERE refund_reason LIKE 'Weather Emergency (%): TEST_E06_%';
DELETE FROM dbo.notifications WHERE title LIKE 'Weather Emergency%' AND user_id IN (SELECT id FROM dbo.users WHERE email LIKE 'test_e2e_%');
DELETE FROM dbo.eco_season_restrictions WHERE reason LIKE 'TEST_E06_%';
UPDATE dbo.activity_slots SET is_active = 1, reserved_count = 0 WHERE eco_experience_id IN (SELECT id FROM dbo.eco_experiences WHERE title LIKE 'TEST_E2E_%');
UPDATE dbo.activity_bookings SET booking_status = 'confirmed' WHERE eco_experience_id IN (SELECT id FROM dbo.eco_experiences WHERE title LIKE 'TEST_E2E_%');
```

---

## 7. Approval Dependencies & Gate Status

- **Experience Emergency Declaration** (`POST /api/v1/eco-experiences/{id}/weather-emergency`): **Already Mounted in `app/main.py`**; scenarios E01–E08 can be verified on staging SQL Server immediately with test listings.
- **Slot Emergency Cancellation** (`POST /api/v1/provider/eco-experiences/{id}/slots/{id}/weather-cancel`): In unmounted `eco_slots.py`; gated behind Gate 4.
- **Booking Blocking (E10)**: In unmounted `eco_bookings.py`; gated behind Gate 4.
