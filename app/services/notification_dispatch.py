"""
Asynchronous Notification Delivery Service & Provider Adapters (Module 03 — A16).

Architectural components:
1. PII Masking & Structured Logging
2. Bounded Exponential Backoff Retry Policy
3. Thread-safe Idempotency Store
4. Provider Adapter Interfaces (Email, SMS)
5. Testable Mock & No-Op Delivery Providers (Zero Network Outbound)
6. Asynchronous Multi-Channel Dispatcher
7. Activity Reminder Scheduling Foundation
"""

from abc import ABC, abstractmethod
import asyncio
from datetime import datetime, timedelta, timezone
import json
import logging
import math
import re
import threading
from typing import Any

from sqlalchemy.orm import Session

from app.core.notification_service import create_notification
from app.schemas.notification_dispatch import (
    DeliveryResult,
    DeliveryStatus,
    EmailPayload,
    FailureClassification,
    InAppPayload,
    NotificationChannel,
    NotificationDispatchBatchResult,
    NotificationEventType,
    ReminderScheduleRequest,
    SMSPayload,
)

logger = logging.getLogger("ceylongo.notification_dispatch")


# ==============================================================================
# 1. PII Masking & Safe Logging Utilities
# ==============================================================================


def mask_email(email: str) -> str:
    """Mask email address to protect personal data in audit logs (e.g. j***e@domain.com)."""
    if not email or "@" not in email:
        return "***"
    parts = email.strip().split("@", 1)
    user, domain = parts[0], parts[1]
    if len(user) <= 2:
        masked_user = user[0] + "***"
    else:
        masked_user = user[0] + "***" + user[-1]
    return f"{masked_user}@{domain}"


def mask_phone(phone: str) -> str:
    """Mask phone number to protect personal data in audit logs (e.g. +947****001)."""
    clean = re.sub(r"[\s\-]", "", phone.strip())
    if len(clean) <= 5:
        return "***"
    prefix = clean[:4]
    suffix = clean[-3:]
    return f"{prefix}****{suffix}"


# ==============================================================================
# 2. Bounded Exponential Backoff Retry Policy
# ==============================================================================


class RetryPolicy:
    """Configurable exponential backoff retry policy for transient delivery failures."""

    def __init__(
        self,
        max_attempts: int = 3,
        initial_delay_seconds: float = 0.5,
        backoff_factor: float = 2.0,
        max_delay_seconds: float = 30.0,
    ) -> None:
        self.max_attempts = max(1, min(max_attempts, 10))
        self.initial_delay_seconds = max(0.01, initial_delay_seconds)
        self.backoff_factor = max(1.0, backoff_factor)
        self.max_delay_seconds = max(self.initial_delay_seconds, max_delay_seconds)

    def compute_delay(self, attempt: int) -> float:
        """Calculate delay in seconds for the given attempt number (1-based)."""
        if attempt <= 1:
            return 0.0
        exponent = attempt - 2
        delay = self.initial_delay_seconds * (self.backoff_factor ** exponent)
        return min(delay, self.max_delay_seconds)


# ==============================================================================
# 3. Idempotency Store Interface & In-Memory Implementation
# ==============================================================================


class IdempotencyStore(ABC):
    """Abstract interface for notification deduplication and idempotency."""

    @abstractmethod
    def check_and_set(self, key: str, ttl_seconds: int = 86400) -> bool:
        """
        Returns True if the key was set (first time seen; proceed with dispatch).
        Returns False if the key already exists (duplicate; suppress dispatch).
        """
        pass

    @abstractmethod
    def clear(self) -> None:
        """Clear stored idempotency keys (primarily for testing)."""
        pass


class InMemoryIdempotencyStore(IdempotencyStore):
    """Thread-safe in-memory sliding window idempotency store."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._store: dict[str, float] = {}

    def check_and_set(self, key: str, ttl_seconds: int = 86400) -> bool:
        now_ts = datetime.now(timezone.utc).timestamp()
        with self._lock:
            # Clean expired keys
            expired_keys = [k for k, exp in self._store.items() if exp <= now_ts]
            for k in expired_keys:
                del self._store[k]

            if key in self._store:
                return False  # Already seen and unexpired

            self._store[key] = now_ts + ttl_seconds
            return True

    def clear(self) -> None:
        with self._lock:
            self._store.clear()


# Global default idempotency store
_DEFAULT_IDEMPOTENCY_STORE = InMemoryIdempotencyStore()


# ==============================================================================
# 4. Provider Adapter Interfaces (Email, SMS)
# ==============================================================================


class EmailProviderAdapter(ABC):
    """Abstract adapter for email delivery gateways (SMTP, SendGrid, SES)."""

    @abstractmethod
    async def send_email(
        self,
        payload: EmailPayload,
        idempotency_key: str | None = None,
    ) -> DeliveryResult:
        """Dispatch email and return structured DeliveryResult."""
        pass


class SMSProviderAdapter(ABC):
    """Abstract adapter for SMS delivery gateways (Twilio, Dialog, Mobitel)."""

    @abstractmethod
    async def send_sms(
        self,
        payload: SMSPayload,
        idempotency_key: str | None = None,
    ) -> DeliveryResult:
        """Dispatch SMS and return structured DeliveryResult."""
        pass


# ==============================================================================
# 5. Mock & No-Op Delivery Adapters (Safe Testing & Offline Development)
# ==============================================================================


class MockEmailProvider(EmailProviderAdapter):
    """Mock email provider for automated tests and offline simulation. Zero network calls."""

    def __init__(self, is_enabled: bool = True) -> None:
        self.is_enabled = is_enabled
        self.sent_messages: list[EmailPayload] = []
        self.simulate_transient_failure_count: int = 0
        self.simulate_permanent_failure: bool = False
        self._attempts_recorded: int = 0

    async def send_email(
        self,
        payload: EmailPayload,
        idempotency_key: str | None = None,
    ) -> DeliveryResult:
        self._attempts_recorded += 1
        masked = mask_email(payload.to_email)

        if not self.is_enabled:
            return DeliveryResult(
                channel="email",
                status="skipped",
                recipient_masked=masked,
                failure_classification="provider_disabled",
                error_message="Email provider is disabled",
            )

        if self.simulate_permanent_failure:
            return DeliveryResult(
                channel="email",
                status="failed_permanent",
                recipient_masked=masked,
                failure_classification="permanent_invalid_recipient",
                error_message="Simulated permanent SMTP rejection (550 User Unknown)",
            )

        if self.simulate_transient_failure_count > 0:
            self.simulate_transient_failure_count -= 1
            return DeliveryResult(
                channel="email",
                status="retrying",
                recipient_masked=masked,
                failure_classification="transient_network_error",
                error_message="Simulated transient SMTP connection timeout",
            )

        self.sent_messages.append(payload)
        return DeliveryResult(
            channel="email",
            status="sent",
            recipient_masked=masked,
            provider_message_id=f"mock-email-{len(self.sent_messages)}",
        )


class MockSMSProvider(SMSProviderAdapter):
    """Mock SMS provider for automated tests and offline simulation. Zero network calls."""

    def __init__(self, is_enabled: bool = True) -> None:
        self.is_enabled = is_enabled
        self.sent_messages: list[SMSPayload] = []
        self.simulate_transient_failure_count: int = 0
        self.simulate_permanent_failure: bool = False
        self._attempts_recorded: int = 0

    async def send_sms(
        self,
        payload: SMSPayload,
        idempotency_key: str | None = None,
    ) -> DeliveryResult:
        self._attempts_recorded += 1
        masked = mask_phone(payload.to_phone)

        if not self.is_enabled:
            return DeliveryResult(
                channel="sms",
                status="skipped",
                recipient_masked=masked,
                failure_classification="provider_disabled",
                error_message="SMS provider is disabled",
            )

        if self.simulate_permanent_failure:
            return DeliveryResult(
                channel="sms",
                status="failed_permanent",
                recipient_masked=masked,
                failure_classification="permanent_invalid_recipient",
                error_message="Simulated permanent SMS gateway failure (Invalid MSISDN)",
            )

        if self.simulate_transient_failure_count > 0:
            self.simulate_transient_failure_count -= 1
            return DeliveryResult(
                channel="sms",
                status="retrying",
                recipient_masked=masked,
                failure_classification="transient_rate_limit",
                error_message="Simulated transient SMS gateway rate limit (429)",
            )

        self.sent_messages.append(payload)
        return DeliveryResult(
            channel="sms",
            status="sent",
            recipient_masked=masked,
            provider_message_id=f"mock-sms-{len(self.sent_messages)}",
        )


class NoOpEmailProvider(EmailProviderAdapter):
    """No-op email adapter used when external email delivery is turned off."""

    async def send_email(
        self,
        payload: EmailPayload,
        idempotency_key: str | None = None,
    ) -> DeliveryResult:
        return DeliveryResult(
            channel="email",
            status="skipped",
            recipient_masked=mask_email(payload.to_email),
            failure_classification="provider_unconfigured",
            error_message="External email delivery is unconfigured or disabled in this environment",
        )


class NoOpSMSProvider(SMSProviderAdapter):
    """No-op SMS adapter used when external SMS delivery is turned off."""

    async def send_sms(
        self,
        payload: SMSPayload,
        idempotency_key: str | None = None,
    ) -> DeliveryResult:
        return DeliveryResult(
            channel="sms",
            status="skipped",
            recipient_masked=mask_phone(payload.to_phone),
            failure_classification="provider_unconfigured",
            error_message="External SMS delivery is unconfigured or disabled in this environment",
        )


# ==============================================================================
# 6. Asynchronous Multi-Channel Notification Dispatcher
# ==============================================================================


class NotificationDispatchService:
    """
    Orchestrates multi-channel asynchronous notification delivery (in-app, email, SMS).
    Guarantees:
    - Zero network delivery during uncommitted DB transactions
    - Exponential backoff retries for transient delivery errors
    - Immediate fail-closed behavior for permanent errors
    - Strict idempotency protection against duplicate event triggers
    - Structured PII-masked observability
    """

    def __init__(
        self,
        email_provider: EmailProviderAdapter | None = None,
        sms_provider: SMSProviderAdapter | None = None,
        idempotency_store: IdempotencyStore | None = None,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        self.email_provider = email_provider or NoOpEmailProvider()
        self.sms_provider = sms_provider or NoOpSMSProvider()
        self.idempotency_store = idempotency_store or _DEFAULT_IDEMPOTENCY_STORE
        self.retry_policy = retry_policy or RetryPolicy()

    async def dispatch(
        self,
        event_type: NotificationEventType,
        idempotency_key: str,
        in_app: InAppPayload | None = None,
        email: EmailPayload | None = None,
        sms: SMSPayload | None = None,
        db: Session | None = None,
    ) -> NotificationDispatchBatchResult:
        """
        Execute dispatch across all requested channels.
        Enforces idempotency and bounded retry loops.
        """
        # Idempotency check: if key already consumed, suppress duplicate delivery
        is_new = self.idempotency_store.check_and_set(idempotency_key)
        if not is_new:
            logger.warning(
                "Duplicate notification dispatch suppressed for key: %s (event: %s)",
                idempotency_key,
                event_type,
            )
            return NotificationDispatchBatchResult(
                event_type=event_type,
                idempotency_key=idempotency_key,
                is_success=True,
                results=[
                    DeliveryResult(
                        channel="in_app" if in_app else ("email" if email else "sms"),
                        status="skipped",
                        recipient_masked="idempotent_duplicate",
                        failure_classification="duplicate_suppressed",
                        error_message="Duplicate event key suppressed by idempotency protection",
                    )
                ],
                total_channels=1,
                sent_channels=0,
                failed_channels=0,
                skipped_channels=1,
            )

        results: list[DeliveryResult] = []

        # 1. In-App Notification (dbo.notifications)
        if in_app is not None and db is not None:
            try:
                notif = create_notification(
                    db=db,
                    user_id=in_app.user_id,
                    title=in_app.title,
                    message=in_app.message,
                    notification_type=in_app.notification_type,
                    link_url=in_app.link_url,
                )
                results.append(
                    DeliveryResult(
                        channel="in_app",
                        status="sent" if notif is not None else "skipped",
                        recipient_masked=f"user_{in_app.user_id}",
                        provider_message_id=str(notif.id) if notif else None,
                    )
                )
            except Exception as exc:
                logger.error("Failed to create in-app notification: %s", exc.__class__.__name__)
                results.append(
                    DeliveryResult(
                        channel="in_app",
                        status="failed_permanent",
                        recipient_masked=f"user_{in_app.user_id}",
                        error_message=str(exc),
                    )
                )

        # 2. Email Delivery with Retry Loop
        if email is not None:
            email_res = await self._dispatch_with_retry(
                channel="email",
                send_fn=lambda: self.email_provider.send_email(email, idempotency_key),
                recipient_masked=mask_email(email.to_email),
            )
            results.append(email_res)

        # 3. SMS Delivery with Retry Loop
        if sms is not None:
            sms_res = await self._dispatch_with_retry(
                channel="sms",
                send_fn=lambda: self.sms_provider.send_sms(sms, idempotency_key),
                recipient_masked=mask_phone(sms.to_phone),
            )
            results.append(sms_res)

        # Summarize batch outcome
        total = len(results)
        sent = sum(1 for r in results if r.status == "sent")
        failed = sum(1 for r in results if r.status in ("failed_permanent", "retrying"))
        skipped = sum(1 for r in results if r.status == "skipped")
        is_success = failed == 0

        logger.info(
            "Notification batch dispatch finished: event=%s, key=%s, total=%d, sent=%d, failed=%d, skipped=%d",
            event_type,
            idempotency_key,
            total,
            sent,
            failed,
            skipped,
        )

        return NotificationDispatchBatchResult(
            event_type=event_type,
            idempotency_key=idempotency_key,
            is_success=is_success,
            results=results,
            total_channels=total,
            sent_channels=sent,
            failed_channels=failed,
            skipped_channels=skipped,
        )

    async def _dispatch_with_retry(
        self,
        channel: NotificationChannel,
        send_fn: Any,
        recipient_masked: str,
    ) -> DeliveryResult:
        """Executes delivery with exponential backoff for transient errors."""
        last_result: DeliveryResult | None = None
        for attempt in range(1, self.retry_policy.max_attempts + 1):
            if attempt > 1:
                delay = self.retry_policy.compute_delay(attempt)
                logger.debug(
                    "Retrying %s dispatch to %s (attempt %d/%d) after %.2fs delay",
                    channel,
                    recipient_masked,
                    attempt,
                    self.retry_policy.max_attempts,
                    delay,
                )
                await asyncio.sleep(delay)

            try:
                res = await send_fn()
                res.attempt_count = attempt
                if res.status == "sent" or res.status == "skipped":
                    return res

                if res.status == "failed_permanent":
                    # Permanent failure: do NOT retry
                    return res

                last_result = res
            except Exception as exc:
                logger.warning(
                    "%s dispatch unexpected exception on attempt %d: %s",
                    channel,
                    attempt,
                    exc.__class__.__name__,
                )
                last_result = DeliveryResult(
                    channel=channel,
                    status="retrying" if attempt < self.retry_policy.max_attempts else "failed_permanent",
                    recipient_masked=recipient_masked,
                    attempt_count=attempt,
                    error_message=str(exc),
                    failure_classification="transient_network_error",
                )

        if last_result is not None:
            last_result.status = "failed_permanent"
            return last_result

        return DeliveryResult(
            channel=channel,
            status="failed_permanent",
            recipient_masked=recipient_masked,
            attempt_count=self.retry_policy.max_attempts,
            error_message="Delivery exceeded max retry attempts",
            failure_classification="transient_network_error",
        )


# Global default dispatch service instance (configured with No-Op providers by default)
default_notification_service = NotificationDispatchService()


# ==============================================================================
# 7. Reminder Notification Scheduling Foundation
# ==============================================================================


def create_activity_reminder_schedule(
    booking_id: int,
    slot_id: int,
    user_id: int,
    activity_title: str,
    starts_at: datetime,
    reminder_window: str = "24h_before",
) -> ReminderScheduleRequest:
    """
    Constructs a validated ReminderScheduleRequest (foundation for future Celery/cron worker).
    Calculates exact scheduled_dispatch_time based on session starts_at.
    """
    if reminder_window == "24h_before":
        dispatch_time = starts_at - timedelta(hours=24)
    elif reminder_window == "2h_before":
        dispatch_time = starts_at - timedelta(hours=2)
    else:
        raise ValueError(f"Unsupported reminder window: {reminder_window}")

    return ReminderScheduleRequest(
        booking_id=booking_id,
        slot_id=slot_id,
        user_id=user_id,
        activity_title=activity_title,
        session_starts_at=starts_at,
        reminder_window=reminder_window,  # type: ignore[arg-type]
        scheduled_dispatch_time=dispatch_time,
    )
