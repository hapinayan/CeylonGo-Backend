# CeylonGo Module 03 – Phase 5E API Contract
## Promotions Discovery, Special Offers, Provider Analytics & Admin Moderation

**Module:** Module 03 – Activities & Experiences<br/>
**Phase:** Phase 5E – End-to-End Promotion Discovery, Special Offers, Analytics, and Moderation<br/>
**Requirements Covered:**
- **A17 – Sponsored Activities Discovery & Badging:** Active campaigns boost activity rankings, display `"Sponsored"` badges, and support event tracking (impressions, clicks, inquiries).
- **A18 – Special Offers & Discounts:** Server-side promotional discounted price calculation, percentage/fixed savings, special offer metadata, and discount filtering.
- **B18 – Provider Promotion Analytics:** Performance metrics endpoint (`/analytics` and `/stats`), tracking impressions, clicks, inquiries, bookings, Click-Through Rate (CTR%), conversion rate, and spend totals.
- **C06 – Admin Promotion Moderation:** Full administrative write lifecycle (`approve`, `reject`, `pause`, `resume`) and system-wide promotion campaign analytics.

**Document Version:** 1.0<br/>
**Date:** 21 November 2026<br/>
**Repository Branch:** `feature/m03-promotions-platform-reporting`<br/>
**Database Compatibility:** Microsoft SQL Server 2025 Express RTM 17.0.1000.7 (`CeylonGoDB_Development`)

---

## 1. Executive & Architectural Summary

Phase 5E implements the missing and partial promotion components of CeylonGo Module 03. All capabilities operate in 100% compliance with the existing physical database schema (Migrations 001–029 applied), strictly avoiding unapplied migration columns (`listing_reference_id` from Migration 030, `booking_reference_id` from Migration 031).

```
+---------------------------------------------------------------------------------------------------+
|                                     CEYLONGO PLATFORM PROMOTIONS                                 |
+---------------------------------------------------------------------------------------------------+
|  TRAVELER (Public)              |  PROVIDER (Self-Service)            |  ADMIN (Moderation)       |
|  - GET /activities              |  - GET /promotions/analytics        |  - GET /promotions/       |
|    (Sponsored Badging & Boost)  |    (Aggregated Spend & CTR)         |    campaigns/analytics    |
|    (Discounted Price & Offers)  |  - GET /promotions/campaigns/{id}/  |  - POST /promotions/      |
|  - POST /activities/campaigns/  |    analytics & /stats               |    campaigns/{id}/review  |
|    {id}/events                  |    (Impressions, Clicks, CTR, CVR)  |    (Approve/Reject/Pause) |
|    (Impression / Click tracking)|                                     |                           |
+---------------------------------------------------------------------------------------------------+
|                                  PHYSICAL SQL SERVER 2025 EXPRESS                                 |
|  - dbo.promotion_campaigns (11 physical columns; provider-scoped targeting)                        |
|  - dbo.promotion_packages  (11 physical columns; provider-scoped discounts)                        |
|  - dbo.promotion_events    (6 physical columns: id, campaign_id, event_type, user_id, ip, created) |
|  - dbo.admin_audit_logs    (8 physical columns: admin_user_id, action_type, entity, details, etc.)  |
+---------------------------------------------------------------------------------------------------+
```

### Core Architectural Guarantees

1. **Physical SQL Server Column Safety:**
   - Physical table `dbo.promotion_campaigns` strictly contains 11 columns. Queries project only these 11 columns (`CAMPAIGN_PHYSICAL_COLUMNS`).
   - Physical table `dbo.promotion_events` strictly contains 6 columns (`id`, `campaign_id`, `event_type`, `user_id`, `ip_address`, `created_at`). Column `booking_reference_id` is never selected or inserted.
2. **Check Constraint Conformance (`CK_promotion_campaigns_status`):**
   - The physical constraint restricts status to: `'draft', 'pending_approval', 'active', 'paused', 'completed', 'cancelled'`.
   - Admin rejection actions safely transition status to `'cancelled'`, avoiding SQL Server check constraint violation `547`.
3. **Deterministic Search Boosting & Pagination:**
   - In public discovery (`GET /api/v1/activities`), activities belonging to providers with an active activity campaign receive `is_sponsored = True` and are boosted to the top of results when `boost_sponsored = True` (default).
   - Within sponsored and organic groups, secondary deterministic sort order (`newest`, `price_asc`, `price_desc`, `duration_asc`, `duration_desc`) is strictly preserved.
4. **Accurate Decimal Rounding & Price Capping:**
   - Discount calculations use `ROUND_HALF_UP` arithmetic (`(base_price * discount_percent / 100).quantize(Decimal("0.01"))`).
   - Discounted price is strictly capped at `0.00 LKR` (`max(Decimal("0.00"), base_price - savings)`), preventing negative prices.
5. **Anti-Fraud Event Tracking:**
   - Public event tracking endpoints reject fabricated booking conversions with `403 Forbidden` (`"Fabricated booking conversions cannot be recorded directly via public event endpoints"`).
   - Events are only accepted for campaigns in `active` status.

---

## 2. API Endpoints Specification

### 2.1 Public Activity Discovery (A17, A18)
- **HTTP Method:** `GET`
- **Route:** `/api/v1/activities`
- **Authentication:** Optional
- **Query Parameters:**
  - `search` (str, optional): Keyword search in title, description, or city.
  - `category` (str, optional): `cultural`, `adventure`, `local`, `community`.
  - `district` (str, optional): District name filter.
  - `city` (str, optional): City name filter.
  - `min_price`, `max_price` (Decimal, optional): Price range bounds.
  - `min_duration`, `max_duration` (int, optional): Duration range in minutes.
  - `sort_by` (str, default `"newest"`): `newest`, `price_asc`, `price_desc`, `duration_asc`, `duration_desc`.
  - `is_sponsored` (bool, optional): Filter activities by active sponsorship status (A17).
  - `has_discount` (bool, optional): Filter activities by active promotional discount (A18).
  - `boost_sponsored` (bool, default `True`): Boost sponsored listings to top of results (A17).
  - `page` (int, default `1`), `page_size` (int, default `12`, max `100`).
- **Response Model:** `ActivityPagination`
  ```json
  {
    "items": [
      {
        "id": 101,
        "title": "Kitulgala White Water Rafting",
        "category": "adventure",
        "district": "Kegalle",
        "city": "Kitulgala",
        "duration_minutes": 180,
        "pricing_type": "per_person",
        "price": "5000.00",
        "currency": "LKR",
        "minimum_age": 12,
        "cover_image_url": "https://cdn.ceylongo.test/rafting.jpg",
        "short_description": "Experience world class white water rafting...",
        "provider_business_name": "Lanka Adventures Pvt Ltd",
        "is_sponsored": true,
        "sponsored_campaign_id": 1,
        "has_discount": true,
        "discount_percent": "20.00",
        "discount_amount": null,
        "discounted_price": "4000.00",
        "special_offer_title": "Early Bird 20% Off"
      }
    ],
    "total": 1,
    "page": 1,
    "page_size": 12,
    "total_pages": 1
  }
  ```

---

### 2.2 Public Promotion Event Tracking (A17)
- **HTTP Method:** `POST`
- **Route:** `/api/v1/activities/campaigns/{campaign_id}/events`
- **Alternative Convenience Route:** `/api/v1/activities/{activity_id}/events`
- **Authentication:** Optional (Traveler user ID captured if authenticated)
- **Request Body:**
  ```json
  {
    "event_type": "impression"
  }
  ```
  *Allowed values:* `"impression"`, `"click"`, `"inquiry"`.
- **Response Model:** `ActivityPromotionEventResponse` (`201 Created`)
  ```json
  {
    "id": 42,
    "campaign_id": 1,
    "event_type": "impression",
    "user_id": 4,
    "ip_address": "192.168.1.10",
    "created_at": "2026-11-21T19:00:00Z"
  }
  ```
- **Error Responses:**
  - `400 Bad Request`: Campaign is not active.
  - `403 Forbidden`: Attempting to submit `event_type = "booking"`.
  - `404 Not Found`: Campaign or activity not found.

---

### 2.3 Provider Activity Promotions Performance Analytics (B18)

#### 2.3.1 Provider Aggregate Promotion Analytics
- **HTTP Method:** `GET`
- **Route:** `/api/v1/provider/activities/promotions/analytics`
- **Authorization:** `require_activity_provider`
- **Response Model:** `ActivityProviderPromotionsAnalytics`
  ```json
  {
    "total_campaigns": 2,
    "active_campaigns": 1,
    "total_budget": "25000.00",
    "total_spent": "7000.00",
    "currency": "LKR",
    "total_impressions": 1500,
    "total_clicks": 120,
    "total_inquiries": 15,
    "total_bookings": 8,
    "overall_ctr": 8.0,
    "overall_conversion_rate": 6.67,
    "campaigns": [
      {
        "campaign_id": 1,
        "campaign_name": "Summer Rafting Campaign",
        "status": "active",
        "budget": "15000.00",
        "spent_amount": "5000.00",
        "currency": "LKR",
        "impressions": 1000,
        "clicks": 80,
        "inquiries": 10,
        "bookings": 6,
        "ctr": 8.0,
        "conversion_rate": 7.5
      }
    ]
  }
  ```

#### 2.3.2 Single Campaign Performance Analytics & Stats
- **HTTP Method:** `GET`
- **Routes:**
  - `/api/v1/provider/activities/promotions/campaigns/{campaign_id}/analytics`
  - `/api/v1/provider/activities/promotions/campaigns/{campaign_id}/stats` (canonical alias)
- **Authorization:** `require_activity_provider` (strict tenant ownership)
- **Response Model:** `ActivityCampaignAnalyticsSummary`
  ```json
  {
    "campaign_id": 1,
    "campaign_name": "Summer Rafting Campaign",
    "status": "active",
    "budget": "15000.00",
    "spent_amount": "5000.00",
    "currency": "LKR",
    "impressions": 1000,
    "clicks": 80,
    "inquiries": 10,
    "bookings": 6,
    "ctr": 8.0,
    "conversion_rate": 7.5
  }
  ```

---

### 2.4 Admin Campaign Moderation Write Actions (C06)
- **HTTP Method:** `POST`
- **Route:** `/api/v1/admin/activities/promotions/campaigns/{campaign_id}/review`
- **Authorization:** `require_admin`
- **Request Body:** `AdminCampaignReviewRequest`
  ```json
  {
    "action": "approve",
    "notes": "Approved for seasonal white water promo"
  }
  ```
  *Rejection example:*
  ```json
  {
    "action": "reject",
    "reason": "Campaign creative does not comply with safety standards"
  }
  ```
- **State Transition Matrix:**
  | Action | Allowed Source Status | Resulting Physical Status | Mandatory Fields |
  | :--- | :--- | :--- | :--- |
  | `approve` | `pending_approval` | `active` | Optional notes |
  | `reject` | `pending_approval` | `cancelled` | Mandatory `reason` (>= 10 chars) |
  | `pause` | `active` | `paused` | Optional notes |
  | `resume` | `paused` | `active` | Optional notes |
- **Audit Trail:** Every moderation action automatically logs an immutable record in `dbo.admin_audit_logs`.
- **Response Model:** `ActivityCampaignResponse` (`200 OK`)

---

### 2.5 Admin Activity Promotion Analytics Overview (C06)
- **HTTP Method:** `GET`
- **Route:** `/api/v1/admin/activities/promotions/campaigns/analytics`
- **Authorization:** `require_admin`
- **Response Model:** `list[ActivityCampaignAnalyticsSummary]`
