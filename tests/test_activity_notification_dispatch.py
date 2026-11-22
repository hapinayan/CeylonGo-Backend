"""
Tests for Module 03 — Multi-Channel Asynchronous Notification Delivery Foundation (A16).

Verifies:
1. PII masking and structured logging hygiene.
2. Bounded exponential backoff calculation.
3. Thread-safe idempotency deduplication.
4. Strongly-typed schema validations (email/phone syntax).
5. Mock and No-Op provider adapters (zero outbound network calls).
6. Retry loops on transient failures and fail-closed on permanent failures.
7. Activity booking confirmation multi-channel dispatch.
8. Activity booking cancellation multi-channel dispatch.
9. Activity session schedule-change batch dispatch.
10. Activity reminder scheduling and dispatch.
"""

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock
import pytest

from app.schemas.notification_dispatch import (
    EmailPayload,
    SMSPayload,
)
from app.services.activity_notifications import (
    create_activity_reminder_schedule,
    dispatch_activity_booking_cancellation,
    dispatch_activity_booking_confirmation,
    dispatch_activity_reminder,
    dispatch_activity_schedule_change,
)
from app.services.notification_dispatch import (
    InMemoryIdempotencyStore,
    MockEmailProvider,
    MockSMSProvider,
    NoOpEmailProvider,
    NoOpSMSProvider,
    NotificationDispatchService,
    RetryPolicy,
    mask_email,
    mask_phone,
)


# ==============================================================================
# 1. PII Masking Tests
# ==============================================================================


def test_mask_email():
    assert mask_email("kasun.perera@example.com") == "k***a@example.com"
    assert mask_email("ab@domain.com") == "a***@domain.com"
    assert mask_email("invalid-email") == "***"
    assert mask_email("") == "***"


def test_mask_phone():
    assert mask_phone("+94771234567") == "+947****567"
    assert mask_phone("+94 77 123 4567") == "+947****567"
    assert mask_phone("0771234567") == "0771****567"
    assert mask_phone("1234") == "***"
    assert mask_phone("") == "***"


# ==============================================================================
# 2. Bounded Exponential Backoff Retry Policy Tests
# ==============================================================================


def test_retry_policy_delays():
    policy = RetryPolicy(
        max_attempts=4,
        initial_delay_seconds=0.1,
        backoff_factor=2.0,
        max_delay_seconds=1.0,
    )
    assert policy.compute_delay(1) == 0.0
    assert policy.compute_delay(2) == 0.1
    assert policy.compute_delay(3) == 0.2
    assert policy.compute_delay(4) == 0.4
    assert policy.compute_delay(10) == 1.0  # Clamped to max_delay_seconds


def test_retry_policy_boundary_clamps():
    policy = RetryPolicy(max_attempts=99, initial_delay_seconds=-5, backoff_factor=0.5)
    assert policy.max_attempts == 10  # Clamped to 10
    assert policy.initial_delay_seconds == 0.01  # Clamped to minimum
    assert policy.backoff_factor == 1.0  # Clamped to minimum


# ==============================================================================
# 3. Idempotency Store Tests
# ==============================================================================


def test_in_memory_idempotency_store():
    store = InMemoryIdempotencyStore()
    key = "evt_unique_123"

    # First attempt: accepted
    assert store.check_and_set(key, ttl_seconds=60) is True

    # Second attempt: duplicate suppressed
    assert store.check_and_set(key, ttl_seconds=60) is False

    # After clearing store: accepted again
    store.clear()
    assert store.check_and_set(key, ttl_seconds=60) is True


# ==============================================================================
# 4. Schema Validation Tests
# ==============================================================================


def test_email_payload_validation():
    valid = EmailPayload(
        to_email="traveler@example.com",
        subject="Booking Receipt",
        body_text="Your receipt details...",
    )
    assert valid.to_email == "traveler@example.com"

    with pytest.raises(ValueError):
        EmailPayload(
            to_email="not-an-email",
            subject="Test",
            body_text="Body",
        )


def test_sms_payload_validation():
    valid = SMSPayload(
        to_phone="+94771234567",
        message="Your activity is confirmed!",
    )
    assert valid.to_phone == "+94771234567"

    with pytest.raises(ValueError):
        SMSPayload(
            to_phone="short",
            message="Test",
        )


def test_reminder_schedule_request():
    starts_at = datetime(2026, 12, 1, 10, 0, tzinfo=timezone.utc)
    req = create_activity_reminder_schedule(
        booking_id=10,
        slot_id=20,
        user_id=5,
        activity_title="Kite Surfing Kalpitiya",
        starts_at=starts_at,
        reminder_window="24h_before",
    )
    assert req.scheduled_dispatch_time == datetime(2026, 11, 30, 10, 0, tzinfo=timezone.utc)
    assert req.reminder_window == "24h_before"

    req_2h = create_activity_reminder_schedule(
        booking_id=10,
        slot_id=20,
        user_id=5,
        activity_title="Kite Surfing Kalpitiya",
        starts_at=starts_at,
        reminder_window="2h_before",
    )
    assert req_2h.scheduled_dispatch_time == datetime(2026, 12, 1, 8, 0, tzinfo=timezone.utc)


# ==============================================================================
# 5. Mock & No-Op Delivery Adapter Tests
# ==============================================================================


def test_mock_email_provider_success():
    async def _run():
        provider = MockEmailProvider(is_enabled=True)
        payload = EmailPayload(
            to_email="test@example.com",
            subject="Hello",
            body_text="World",
        )
        res = await provider.send_email(payload)
        assert res.status == "sent"
        assert res.channel == "email"
        assert len(provider.sent_messages) == 1

    asyncio.run(_run())


def test_mock_email_provider_disabled():
    async def _run():
        provider = MockEmailProvider(is_enabled=False)
        payload = EmailPayload(
            to_email="test@example.com",
            subject="Hello",
            body_text="World",
        )
        res = await provider.send_email(payload)
        assert res.status == "skipped"
        assert res.failure_classification == "provider_disabled"
        assert len(provider.sent_messages) == 0

    asyncio.run(_run())


def test_mock_email_provider_permanent_failure():
    async def _run():
        provider = MockEmailProvider(is_enabled=True)
        provider.simulate_permanent_failure = True
        payload = EmailPayload(
            to_email="unknown@example.com",
            subject="Hello",
            body_text="World",
        )
        res = await provider.send_email(payload)
        assert res.status == "failed_permanent"
        assert res.failure_classification == "permanent_invalid_recipient"

    asyncio.run(_run())


def test_mock_sms_provider_success():
    async def _run():
        provider = MockSMSProvider(is_enabled=True)
        payload = SMSPayload(
            to_phone="+94770000000",
            message="Hello SMS",
        )
        res = await provider.send_sms(payload)
        assert res.status == "sent"
        assert res.channel == "sms"
        assert len(provider.sent_messages) == 1

    asyncio.run(_run())


def test_noop_providers():
    async def _run():
        noop_email = NoOpEmailProvider()
        res_e = await noop_email.send_email(
            EmailPayload(to_email="test@example.com", subject="S", body_text="B")
        )
        assert res_e.status == "skipped"
        assert res_e.failure_classification == "provider_unconfigured"

        noop_sms = NoOpSMSProvider()
        res_s = await noop_sms.send_sms(
            SMSPayload(to_phone="+94770000000", message="M")
        )
        assert res_s.status == "skipped"
        assert res_s.failure_classification == "provider_unconfigured"

    asyncio.run(_run())


# ==============================================================================
# 6. Dispatcher Transient Retry & Idempotency Tests
# ==============================================================================


def test_dispatcher_retry_on_transient_failure():
    async def _run():
        email_prov = MockEmailProvider(is_enabled=True)
        email_prov.simulate_transient_failure_count = 1  # Fails 1st, succeeds 2nd

        policy = RetryPolicy(max_attempts=3, initial_delay_seconds=0.01)
        store = InMemoryIdempotencyStore()
        dispatcher = NotificationDispatchService(
            email_provider=email_prov,
            idempotency_store=store,
            retry_policy=policy,
        )

        result = await dispatcher.dispatch(
            event_type="booking_confirmed",
            idempotency_key="key_retry_test_1",
            email=EmailPayload(to_email="guest@example.com", subject="Sub", body_text="Txt"),
        )

        assert result.is_success is True
        assert result.sent_channels == 1
        email_res = result.results[0]
        assert email_res.status == "sent"
        assert email_res.attempt_count == 2

    asyncio.run(_run())


def test_dispatcher_fail_closed_on_permanent_failure():
    async def _run():
        email_prov = MockEmailProvider(is_enabled=True)
        email_prov.simulate_permanent_failure = True

        policy = RetryPolicy(max_attempts=3, initial_delay_seconds=0.01)
        store = InMemoryIdempotencyStore()
        dispatcher = NotificationDispatchService(
            email_provider=email_prov,
            idempotency_store=store,
            retry_policy=policy,
        )

        result = await dispatcher.dispatch(
            event_type="booking_confirmed",
            idempotency_key="key_perm_test_1",
            email=EmailPayload(to_email="guest@example.com", subject="Sub", body_text="Txt"),
        )

        assert result.is_success is False
        assert result.failed_channels == 1
        assert result.results[0].status == "failed_permanent"
        assert result.results[0].attempt_count == 1  # Did NOT waste retries

    asyncio.run(_run())


def test_dispatcher_duplicate_event_suppression():
    async def _run():
        email_prov = MockEmailProvider(is_enabled=True)
        store = InMemoryIdempotencyStore()
        dispatcher = NotificationDispatchService(
            email_provider=email_prov,
            idempotency_store=store,
        )

        payload = EmailPayload(to_email="guest@example.com", subject="Sub", body_text="Txt")
        key = "duplicate_idemp_key_1"

        # 1st dispatch: succeeds
        res1 = await dispatcher.dispatch(
            event_type="booking_confirmed",
            idempotency_key=key,
            email=payload,
        )
        assert res1.sent_channels == 1
        assert len(email_prov.sent_messages) == 1

        # 2nd dispatch: suppressed by idempotency
        res2 = await dispatcher.dispatch(
            event_type="booking_confirmed",
            idempotency_key=key,
            email=payload,
        )
        assert res2.skipped_channels == 1
        assert res2.results[0].failure_classification == "duplicate_suppressed"
        assert len(email_prov.sent_messages) == 1  # No new message sent

    asyncio.run(_run())


# ==============================================================================
# 7. Activity Domain Notification Functions Tests
# ==============================================================================


@pytest.fixture
def mock_domain_entities():
    """Build lightweight mock entities for domain tests."""
    user = MagicMock()
    user.id = 42
    user.full_name = "Nimal Silva"
    user.email = "nimal@example.com"

    activity = MagicMock()
    activity.id = 101
    activity.title = "Sigiriya Rock Fortress Guided Tour"
    activity.duration_minutes = 180
    activity.meeting_point = "Sigiriya Main Gate Ticket Counter"

    slot = MagicMock()
    slot.id = 505
    slot.starts_at = datetime(2026, 11, 25, 8, 30)

    booking = MagicMock()
    booking.id = 999
    booking.participants = 2
    booking.currency = "LKR"
    booking.total_price = Decimal("15000.00")
    booking.payment_method = "online"
    booking.payment_status = "paid"
    booking.traveler = user

    return user, activity, slot, booking


def test_dispatch_activity_booking_confirmation(mock_domain_entities):
    async def _run():
        user, activity, slot, booking = mock_domain_entities
        email_prov = MockEmailProvider(is_enabled=True)
        sms_prov = MockSMSProvider(is_enabled=True)
        dispatcher = NotificationDispatchService(
            email_provider=email_prov,
            sms_provider=sms_prov,
            idempotency_store=InMemoryIdempotencyStore(),
        )

        batch_res = await dispatch_activity_booking_confirmation(
            booking=booking,
            slot=slot,
            activity=activity,
            user=user,
            recipient_phone="+94771234567",
            dispatcher=dispatcher,
        )

        assert batch_res.is_success is True
        assert batch_res.event_type == "booking_confirmed"
        assert batch_res.sent_channels == 2  # email + sms
        assert len(email_prov.sent_messages) == 1
        assert "Sigiriya Rock Fortress" in email_prov.sent_messages[0].subject
        assert len(sms_prov.sent_messages) == 1
        assert "Sigiriya" in sms_prov.sent_messages[0].message

    asyncio.run(_run())


def test_dispatch_activity_booking_cancellation(mock_domain_entities):
    async def _run():
        user, activity, slot, booking = mock_domain_entities
        email_prov = MockEmailProvider(is_enabled=True)
        sms_prov = MockSMSProvider(is_enabled=True)
        dispatcher = NotificationDispatchService(
            email_provider=email_prov,
            sms_provider=sms_prov,
            idempotency_store=InMemoryIdempotencyStore(),
        )

        batch_res = await dispatch_activity_booking_cancellation(
            booking=booking,
            slot=slot,
            activity=activity,
            user=user,
            reason="Heavy monsoon downpour",
            cancelled_by="provider",
            recipient_phone="+94771234567",
            dispatcher=dispatcher,
        )

        assert batch_res.is_success is True
        assert batch_res.event_type == "booking_cancelled"
        assert "Heavy monsoon downpour" in email_prov.sent_messages[0].body_text
        assert "cancelled by provider" in sms_prov.sent_messages[0].message

    asyncio.run(_run())


def test_dispatch_activity_schedule_change(mock_domain_entities):
    async def _run():
        user, activity, slot, booking = mock_domain_entities
        email_prov = MockEmailProvider(is_enabled=True)
        dispatcher = NotificationDispatchService(
            email_provider=email_prov,
            idempotency_store=InMemoryIdempotencyStore(),
        )

        new_time = datetime(2026, 11, 25, 14, 0)
        batch_results = await dispatch_activity_schedule_change(
            slot=slot,
            activity=activity,
            affected_bookings=[booking],
            reason="Guide schedule conflict",
            new_starts_at=new_time,
            dispatcher=dispatcher,
        )

        assert len(batch_results) == 1
        res = batch_results[0]
        assert res.is_success is True
        assert res.event_type == "schedule_changed"
        assert len(email_prov.sent_messages) == 1
        assert "Important Schedule Update" in email_prov.sent_messages[0].subject

    asyncio.run(_run())


def test_dispatch_activity_reminder(mock_domain_entities):
    async def _run():
        user, activity, slot, booking = mock_domain_entities
        email_prov = MockEmailProvider(is_enabled=True)
        sms_prov = MockSMSProvider(is_enabled=True)
        dispatcher = NotificationDispatchService(
            email_provider=email_prov,
            sms_provider=sms_prov,
            idempotency_store=InMemoryIdempotencyStore(),
        )

        batch_res = await dispatch_activity_reminder(
            booking=booking,
            slot=slot,
            activity=activity,
            user=user,
            reminder_window="24h_before",
            recipient_phone="+94771234567",
            dispatcher=dispatcher,
        )

        assert batch_res.is_success is True
        assert batch_res.event_type == "reminder_scheduled"
        assert "24 hours" in email_prov.sent_messages[0].body_text
        assert "24 hours" in sms_prov.sent_messages[0].message

    asyncio.run(_run())
