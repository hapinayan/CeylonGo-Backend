# Module 03 — Asynchronous Multi-Channel Notification Delivery Architecture (A16)

- **Project:** CeylonGo Backend
- **Module:** Module 03 – Activities & Experiences
- **Requirement Reference:** Canonical Requirement **A16 (Email/SMS Notification Delivery)**
- **Status:** **PARTIAL** (Foundation and domain dispatch abstraction complete; durable distributed worker and live external provider credentials remain future production dependencies)
- **Date:** November 2026

---

## 1. Executive Summary

Requirement **A16** specifies multi-channel notification capabilities for travelers and providers across the activity lifecycle:
- In-App push/feed notifications
- Email confirmations, cancellations, schedule updates, and reminders
- SMS alerts for urgent booking updates and time-sensitive reminders

This document specifies the architecture, interfaces, failure classification taxonomy, idempotency controls, and security practices implemented in Phase 5E-2. It also documents why Requirement A16 remains classified as **PARTIAL** in the canonical requirements baseline.

---

## 2. Multi-Channel Architecture & Interfaces

The notification system decouples database state persistence from external network delivery through strongly-typed provider adapters and a central dispatcher:

```
+-------------------------------------------------------------------------------+
|                       Module 03 Activity Event Triggers                        |
|  (Booking Confirmed, Booking Cancelled, Schedule Changed, Session Reminder)   |
+---------------------------------------+---------------------------------------+
                                        |
                                        v
+-------------------------------------------------------------------------------+
|             Activity Notification Service (app/services/activity_notifications.py)
|  - Validates recipient email & E.164 phone syntax                             |
|  - Constructs rich HTML & plain text templates                                |
|  - Generates deterministic idempotency keys                                   |
+---------------------------------------+---------------------------------------+
                                        |
                                        v
+-------------------------------------------------------------------------------+
|         Notification Dispatch Service (app/services/notification_dispatch.py) |
|  - Idempotency Gate (InMemoryIdempotencyStore)                                |
|  - Structured PII-masked logging (kasun@... -> k***n@...)                     |
|  - Bounded exponential backoff retry policy                                   |
+----------+----------------------------+-----------------------------+---------+
           |                            |                             |
           v                            v                             v
+--------------------+        +--------------------+        +--------------------+
|   In-App Channel   |        |   Email Channel    |        |    SMS Channel     |
| (dbo.notifications)|        | (EmailProviderAdapter)      | (SMSProviderAdapter)|
+--------------------+        +--------------------+        +--------------------+
                                        |                             |
                       +----------------+---------------+             |
                       |                                |             |
                       v                                v             v
             [MockEmailProvider]              [NoOpEmailProvider] [MockSMSProvider]
            (Zero Network Dev/Test)           (Unconfigured Prod) (Zero Network Dev/Test)
```

### 2.1 Provider Adapter Abstractions

1. **`EmailProviderAdapter`**:
   - `send_email(payload: EmailPayload, idempotency_key: str | None) -> DeliveryResult`
   - Handles connection to outbound mail gateways (SMTP, AWS SES, SendGrid).
   - Concrete implementations:
     - `MockEmailProvider`: In-memory recording of messages, simulated network timeouts, and permanent rejection scenarios. Zero network calls.
     - `NoOpEmailProvider`: Graceful skip when external email delivery is unconfigured.
2. **`SMSProviderAdapter`**:
   - `send_sms(payload: SMSPayload, idempotency_key: str | None) -> DeliveryResult`
   - Handles connection to outbound SMS gateways (Twilio, Dialog IdeaBiz, Mobitel).
   - Concrete implementations:
     - `MockSMSProvider`: In-memory recording, simulated rate-limits, and phone format rejections. Zero network calls.
     - `NoOpSMSProvider`: Graceful skip when external SMS delivery is unconfigured.

---

## 3. Transaction Boundary & Non-Blocking Invariant

> [!IMPORTANT]
> **Zero Network Calls Inside Database Transactions:**
> Under no circumstances may an external HTTP or SMTP call occur while an active SQL Server database transaction (`db.begin()`) is held open.
>
> Network delays or provider outages would hold database locks (such as row-level or page-level locks on `activity_bookings` and `activity_slots`), leading to thread pool starvation and deadlocks.
>
> In-app notifications are written within or immediately adjacent to the local transaction. Outbound email and SMS dispatches are executed asynchronously *after* database transactions commit.

---

## 4. Failure Classification & Retry Policy

Delivery attempts return a structured `DeliveryResult` with an explicit `failure_classification`:

| Failure Classification | Category | Behavior |
| :--- | :--- | :--- |
| `transient_network_error` | Transient | Retried with exponential backoff up to `max_attempts`. |
| `transient_rate_limit` | Transient | Retried with exponential backoff up to `max_attempts`. |
| `permanent_invalid_recipient` | Permanent | Fails closed immediately. **Zero** retries attempted. |
| `permanent_blacklisted` | Permanent | Fails closed immediately. **Zero** retries attempted. |
| `provider_disabled` | Configuration | Skipped immediately (`status="skipped"`). |
| `provider_unconfigured` | Configuration | Skipped immediately (`status="skipped"`). |
| `duplicate_suppressed` | Idempotency | Suppressed cleanly (`status="skipped"`). |

### 4.1 Bounded Exponential Backoff

The `RetryPolicy` computes inter-attempt delays using the formula:
$$\text{delay} = \min(\text{initial\_delay} \times (\text{backoff\_factor})^{\text{attempt} - 2}, \text{max\_delay})$$
- `max_attempts`: Clamped between 1 and 10 (default: 3).
- `initial_delay_seconds`: Minimum 0.01s (default: 0.5s).
- `backoff_factor`: Minimum 1.0 (default: 2.0).
- `max_delay_seconds`: Default 30.0s.

---

## 5. Idempotency & Deduplication

To prevent duplicate emails or SMS messages when webhooks or API requests are retried:
- Every dispatch event computes a deterministic `idempotency_key`:
  - Booking Confirmation: `act_book_conf_{booking_id}`
  - Booking Cancellation: `act_book_cancel_{booking_id}_{cancelled_by}`
  - Schedule Change: `act_slot_sched_{slot_id}_{booking_id}`
  - Session Reminder: `act_reminder_{booking_id}_{reminder_window}`
- The `InMemoryIdempotencyStore` checks whether the key has been seen within its sliding TTL window (default: 86,400 seconds / 24 hours). If already present, dispatch is suppressed and classified as `duplicate_suppressed`.

---

## 6. PII Masking & Audit Hygiene

In compliance with data protection principles:
- Raw email addresses and phone numbers are **never** logged to stdout, files, or audit tables.
- `mask_email("kasun.perera@example.com")` -> `"k***a@example.com"`
- `mask_phone("+94771234567")` -> `"+947****567"`
- Audit logs only output masked recipients and metadata.

---

## 7. Canonical Activity Domain Events

### 7.1 Booking Confirmation (`dispatch_activity_booking_confirmation`)
- **Trigger:** Activity reservation approved and paid.
- **Channels:** In-App, Email, SMS.
- **Content:** Booking reference number, activity title, formatted date & start time, session duration, number of participants, total amount paid, currency, payment method, meeting point instructions, and 15-minute arrival reminder.

### 7.2 Booking Cancellation (`dispatch_activity_booking_cancellation`)
- **Trigger:** Booking cancelled by traveler, provider, or administrator.
- **Channels:** In-App, Email, SMS.
- **Content:** Booking reference number, cancellation initiator (`traveler` / `provider` / `admin`), stated cancellation reason, and refund eligibility policy notification.

### 7.3 Schedule Change (`dispatch_activity_schedule_change`)
- **Trigger:** Provider or admin reschedules or modifies an activity session slot.
- **Channels:** In-App, Email, SMS to all active booking holders.
- **Content:** Activity title, affected booking ID, schedule-change reason, updated start date & time, meeting point, and support/cancellation options.

### 7.4 Session Reminder (`dispatch_activity_reminder`)
- **Trigger:** Pre-session reminder window reached (`24h_before` or `2h_before`).
- **Channels:** In-App, Email, SMS.
- **Content:** Friendly reminder, exact meeting point, time remaining, and preparation checklist (comfortable footwear, prompt arrival).

---

## 8. Database Schema Gaps & Reason for PARTIAL Classification

Requirement **A16** remains classified as **PARTIAL** due to the following infrastructure boundaries:

1. **Physical Database Schema Boundaries:**
   - Existing table `dbo.notifications` physically supports only in-app notifications:
     `id`, `user_id`, `title`, `message`, `notification_type`, `link_url`, `is_read`, `read_at`, `created_at`.
   - It does not contain columns for multi-channel tracking (`delivery_channel`, `external_message_id`, `retry_count`, `delivered_at`, `failed_at`).
   - A future database migration (e.g. `notification_delivery_logs`) will be required to persist durable multi-channel delivery audit trails.
2. **No Real Outbound Network Credentials in Development:**
   - Development and test environments use `MockEmailProvider`, `MockSMSProvider`, `NoOpEmailProvider`, and `NoOpSMSProvider`. Zero real network calls are made.
   - Production deployment requires configuring live SMTP credentials (e.g., AWS SES) and SMS gateway API keys (e.g., Dialog/Mobitel/Twilio).
3. **Durable Distributed Worker Integration:**
   - Pre-session reminders (`24h_before`, `2h_before`) require a durable background task scheduler (e.g., Celery + Redis, APScheduler, or AWS SQS + Lambda) running continuously outside the web server process. The foundation `ReminderScheduleRequest` schema and calculation logic are in place.

---

## 9. Verification & Safety Summary

- **Unit/Integration Tests:** 20 passed (`tests/test_activity_notification_dispatch.py`).
- **Database Safety:** 0 DDL statements, 0 DML statements, 0 migrations executed.
- **Network Safety:** Strictly 0 outbound network calls (enforced by mock/no-op adapters).
- **Status in Canonical 48 Requirements:** Retained as **PARTIAL** with clear production roadmap.
