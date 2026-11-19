# CeylonGo Homestay Module — Backend API Guide

**Repository:** CeylonGo-Backend  
**Module:** Homestay Management, Booking Lifecycle & Financial Operations  
**Base Path:** `/api/v1`  
**Current Architecture:** FastAPI, SQLAlchemy 2.0, Microsoft SQL Server (Transact-SQL)  
**Database Tables:** 88 | **Database Migrations:** 001–031  

---

## 1. Module Overview

The CeylonGo Homestay Backend Module provides end-to-end management of homestay accommodations in Sri Lanka. It governs the complete property lifecycle, availability calendar management, multi-step booking workflows (both online and cash-at-location), server-authoritative pricing and promotions, PayHere payment gateway integration, automatic 30-minute payment expiration, financial accounting (10% CeylonGo commission / 90% provider share), proportional refund accounting, provider settlement payouts, and cryptographically verified advertising campaign attribution.

---

## 2. Authentication & Authorization

All protected endpoints require an HTTP `Authorization` header containing a valid JSON Web Token (JWT):

```http
Authorization: Bearer <access_token>
```

### Roles & Access Control
| Role | Description | Permissions |
| :--- | :--- | :--- |
| `traveler` | Authenticated traveler / tourist | Can search listings, request bookings, pay for bookings, request cancellations & refunds. |
| `provider` | Homestay host / property owner | Can manage listings, set blackout dates, accept/reject bookings, record cash payments, view earnings & settlement history. |
| `admin` | CeylonGo platform administrator | Can review & approve provider listings, approve advertising campaigns, process refunds, create provider settlements, inspect financial summaries. |

---

## 3. Booking Lifecycle & State Transitions

### Status State Machine
```
                       +-------------------+
                       |      pending      |
                       +---------+---------+
                                 |
                 +---------------+---------------+
                 | (pay_at_location)             | (online payment)
                 v                               v
         +---------------+             +--------------------+
         |   confirmed   |             |  awaiting_payment  |
         +-------+-------+             +----+----------+----+
                 |                          |          |
                 |               (30m paid) |          | (30m expired)
                 |                          v          v
                 |                   +-----------+  +---------+
                 +------------------>| confirmed |  | expired |
                 |                   +-----+-----+  +---------+
                 |                         |
                 | (check_in reached)      |
                 v                         v
         +---------------+         +---------------+
         |  in_progress  |         |   cancelled   |
         +-------+-------+         +---------------+
                 |
                 | (check_out passed)
                 v
         +---------------+
         |   completed   |
         +---------------+
```

### Transition Matrix
- **`pending`**: Initial state upon traveler booking creation.
- **`awaiting_payment`**: Set when provider approves an `online` payment booking. Starts the 30-minute payment window (`payment_expires_at = approved_at + 30m`). Inventory is strictly reserved during this window.
- **`confirmed`**: 
  - For `pay_at_location`: Set immediately upon provider acceptance.
  - For `online`: Set automatically upon verified PayHere gateway callback or zero-value voucher confirmation.
- **`expired`**: Set automatically by the background worker if 30 minutes elapse without verified payment. Releases inventory reservations immediately.
- **`cancelled`**: Can be initiated by traveler or provider prior to check-in. Applies stored cancellation policy snapshot.
- **`in_progress`**: Activated on check-in date.
- **`completed`**: Allowed only on or after check-out date. Unlocks provider earnings for settlement eligibility.

---

## 4. API Endpoints Reference

### 4.1 Traveler Booking Endpoints

#### Create Booking Request
```http
POST /api/v1/bookings/
```
**Headers:** `Authorization: Bearer <traveler_token>`  
**Request Payload:**
```json
{
  "homestay_id": 14,
  "check_in": "2026-12-10",
  "check_out": "2026-12-15",
  "guests": 2,
  "payment_method": "online",
  "attribution_token": "eyJjaWQiOjQsImhpZCI6MTR9.signature..."
}
```
*Note: `attribution_token` is optional and cryptographically ties the booking to an active advertising campaign.*

**Response (201 Created):**
```json
{
  "id": 105,
  "homestay_id": 14,
  "user_id": 8,
  "check_in": "2026-12-10",
  "check_out": "2026-12-15",
  "guests": 2,
  "total_price": "50000.00",
  "currency": "LKR",
  "status": "pending",
  "payment_method": "online",
  "payment_status": "unpaid",
  "payment_expires_at": null,
  "cancellation_policy_snapshot": "flexible",
  "free_cancellation_hours_snapshot": 24,
  "created_at": "2026-11-19T07:30:00"
}
```

#### Initiate Online Booking Checkout
```http
POST /api/v1/payments/initiate-booking/{booking_id}
```
**Headers:** `Authorization: Bearer <traveler_token>`  
**Requirements:** Booking must be in `awaiting_payment` status and `payment_expires_at > now()`.

**Response (200 OK):**
```json
{
  "booking_id": 105,
  "booking_reference": "BK-HOMESTAY-105",
  "merchant_id": "1211111",
  "checkout_url": "https://sandbox.payhere.lk/pay/checkout",
  "order_id": "BK-HOMESTAY-105",
  "items": "Homestay: Kandy River Breeze (2026-12-10 to 2026-12-15)",
  "amount": "50000.00",
  "currency": "LKR",
  "hash": "A3F8B...SHA256",
  "first_name": "Traveler",
  "last_name": "User",
  "email": "traveler@example.com",
  "phone": "+94770000000",
  "address": "123 Colombo Rd",
  "city": "Colombo",
  "country": "Sri Lanka"
}
```

#### Cancel Booking
```http
POST /api/v1/bookings/{booking_id}/cancel
```
**Headers:** `Authorization: Bearer <traveler_token>` or `<provider_token>` or `<admin_token>`  
**Request Payload:**
```json
{
  "cancellation_reason": "Flight schedule changed"
}
```

---

### 4.2 Service Provider Endpoints

#### Accept Booking Request
```http
POST /api/v1/bookings/{booking_id}/accept
```
**Headers:** `Authorization: Bearer <provider_token>`  
- If `payment_method == "online"`: Status becomes `awaiting_payment`, `payment_expires_at` is set to UTC `now + 30 minutes`.
- If `payment_method == "pay_at_location"`: Status becomes `confirmed`.

#### Reject Booking Request
```http
POST /api/v1/bookings/{booking_id}/reject
```
**Headers:** `Authorization: Bearer <provider_token>`  
**Request Payload:**
```json
{
  "reason": "Property undergoing emergency plumbing maintenance"
}
```

#### Record Cash-At-Location Payment
```http
POST /api/v1/bookings/{booking_id}/cash-payment
```
**Headers:** `Authorization: Bearer <provider_token>`  
**Request Payload:**
```json
{
  "amount": "50000.00",
  "notes": "Collected cash in full at reception"
}
```

#### Complete Booking (Check-Out)
```http
POST /api/v1/bookings/{booking_id}/complete
```
**Headers:** `Authorization: Bearer <provider_token>`  
*Enforcement: Rejects completion if `check_out > today`.*

#### Provider Earnings Summary
```http
GET /api/v1/payments/provider/earnings/summary
```
**Headers:** `Authorization: Bearer <provider_token>`  
**Response (200 OK):**
```json
{
  "provider_profile_id": 5,
  "currency": "LKR",
  "gross_booking_revenue": "100000.00",
  "platform_commission": "10000.00",
  "net_earned_revenue": "90000.00",
  "total_completed_earnings": "72000.00",
  "pending_earnings": "18000.00",
  "total_settled_amount": "50000.00",
  "available_settlement_balance": "22000.00",
  "verified_refunds": "0.00",
  "provider_refund_reduction": "0.00",
  "commission_reversal": "0.00",
  "outstanding_liability": "0.00"
}
```

---

### 4.3 Administrator Financial Endpoints

#### Process Refund Request
```http
POST /api/v1/payments/refunds/{refund_id}/process
```
**Headers:** `Authorization: Bearer <admin_token>`  
**Request Payload:**
```json
{
  "status": "approved",
  "notes": "Approved per flexible 24h cancellation policy"
}
```
*Note: False completion without verifiable independent payment execution evidence is strictly rejected.*

#### Create Provider Settlement
```http
POST /api/v1/payments/admin/settlements
```
**Headers:** `Authorization: Bearer <admin_token>`  
**Request Payload:**
```json
{
  "provider_profile_id": 5,
  "period_start": "2026-11-01",
  "period_end": "2026-11-15",
  "gross_amount": "20000.00",
  "platform_fee": "2000.00",
  "net_payable": "18000.00",
  "settlement_status": "pending",
  "payout_reference": "Awaiting bank payout transmission"
}
```

#### Admin Platform Financial Overview
```http
GET /api/v1/payments/admin/summary
```
**Headers:** `Authorization: Bearer <admin_token>`  
**Response (200 OK):**
```json
{
  "total_gross_booking_volume": "1500000.00",
  "total_platform_commission": "150000.00",
  "total_provider_payable": "1350000.00",
  "total_refunds_processed": "25000.00",
  "total_commission_reversed": "2500.00",
  "total_settlements_disbursed": "900000.00",
  "total_outstanding_provider_liabilities": "422500.00",
  "total_advertising_revenue": "75000.00",
  "currency": "LKR"
}
```

---

### 4.4 Advertising & Attribution Endpoints

#### Issue Signed Attribution Token (Active Campaign)
```http
POST /api/v1/homestays/campaigns/{campaign_id}/attribution-token
```
**Response (200 OK):**
```json
{
  "campaign_id": 4,
  "homestay_id": 14,
  "attribution_token": "eyJjaWQiOjQsImhpZCI6MTR9.7d98ab...hmac",
  "expires_in_hours": 48
}
```

#### Get Active Attribution Token by Homestay Listing
```http
GET /api/v1/homestays/{homestay_id}/attribution-token
```
**Response (200 OK):**
```json
{
  "campaign_id": 4,
  "homestay_id": 14,
  "attribution_token": "eyJjaWQiOjQsImhpZCI6MTR9.7d98ab...hmac",
  "expires_in_hours": 48
}
```

---

## 5. Financial & Commission Rules

### 10% Platform Commission / 90% Provider Share
- **Online Bookings:** Calculated server-side on the final discounted booking total:
  $$\text{Commission} = \text{round}(\text{total\_price} \times 0.10, 2)$$
  $$\text{Provider Share} = \text{total\_price} - \text{Commission}$$
- **Cash Bookings:** Excluded from online payment ledger. Collected directly at location.
- **Zero-Value Promotional Bookings:** Total price is 0.00 LKR. Commission and provider share are 0.00 LKR. Confirmed without gateway charge.

### Proportional Refund Accounting
When a verified partial or full refund is approved:
- Platform commission is reversed proportionally:
  $$\text{Commission Reversal} = \text{round}(\text{Refund Amount} \times 0.10, 2)$$
- Provider deduction is proportional:
  $$\text{Provider Deduction} = \text{Refund Amount} - \text{Commission Reversal}$$
- The provider is never penalized for platform commission on refunded amounts.

---

## 6. Frontend & Mobile (Flutter) Integration Guidelines

1. **Payment Window Timer:** Display a live 30-minute countdown timer on the traveler payment screen driven by `payment_expires_at`.
2. **Expired State Handling:** If the countdown reaches zero, display "Reservation Expired — Dates Released" and offer a "Book Again" button.
3. **Advertising Tracking:** When displaying a sponsored listing badge, fetch the `attribution_token` and pass it in the `BookingCreate` request payload.
4. **Commission Display:** Providers see transparent gross, 10% platform fee, and 90% net earnings breakdown on their earnings tab.
