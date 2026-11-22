"""
Pydantic Schemas for Module 03 — Asynchronous Multi-Channel Notification Dispatch (A16).

Defines strongly-typed payloads, delivery statuses, failure classifications,
and dispatch results for in-app, email, and SMS channels.
"""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


NotificationChannel = Literal["in_app", "email", "sms"]

NotificationEventType = Literal[
    "booking_confirmed",
    "booking_cancelled",
    "schedule_changed",
    "reminder_scheduled",
    "reservation_requested",
    "payment_received",
]

DeliveryStatus = Literal[
    "pending",
    "sent",
    "retrying",
    "failed_permanent",
    "skipped",
]

FailureClassification = Literal[
    "transient_network_error",
    "transient_rate_limit",
    "permanent_invalid_recipient",
    "permanent_blacklisted",
    "provider_disabled",
    "provider_unconfigured",
    "duplicate_suppressed",
]


class EmailPayload(BaseModel):
    """Payload for dispatching an email notification."""

    model_config = ConfigDict(extra="forbid")

    to_email: str = Field(min_length=5, max_length=255, description="Recipient email address")
    subject: str = Field(min_length=1, max_length=200, description="Email subject line")
    body_text: str = Field(min_length=1, max_length=10000, description="Plain text content")
    body_html: str | None = Field(default=None, max_length=50000, description="Optional HTML content")
    recipient_name: str | None = Field(default=None, max_length=150)

    @field_validator("to_email")
    @classmethod
    def validate_email_syntax(cls, v: str) -> str:
        clean = v.strip()
        if "@" not in clean or "." not in clean.split("@")[-1] or len(clean.split("@")[-1]) < 2:
            raise ValueError("Invalid recipient email syntax")
        return clean


class SMSPayload(BaseModel):
    """Payload for dispatching an SMS notification."""

    model_config = ConfigDict(extra="forbid")

    to_phone: str = Field(min_length=7, max_length=30, description="Recipient E.164 phone number")
    message: str = Field(min_length=1, max_length=1000, description="SMS text content")
    recipient_name: str | None = Field(default=None, max_length=150)

    @field_validator("to_phone")
    @classmethod
    def validate_phone_syntax(cls, v: str) -> str:
        clean = v.strip().replace(" ", "").replace("-", "")
        # Require leading + or standard digits
        if not (clean.startswith("+") and clean[1:].isdigit()) and not clean.isdigit():
            raise ValueError("Invalid recipient phone number format. Must contain digits or start with +")
        if len(clean) < 7:
            raise ValueError("Phone number is too short")
        return clean


class InAppPayload(BaseModel):
    """Payload for persisting an in-app notification to dbo.notifications."""

    model_config = ConfigDict(extra="forbid")

    user_id: int = Field(ge=1, description="Recipient user ID")
    title: str = Field(min_length=1, max_length=150)
    message: str = Field(min_length=1, max_length=1000)
    notification_type: str = Field(min_length=1, max_length=50)
    link_url: str | None = Field(default=None, max_length=500)


class DeliveryResult(BaseModel):
    """Result of an individual channel delivery attempt."""

    model_config = ConfigDict(extra="ignore")

    channel: NotificationChannel
    status: DeliveryStatus
    recipient_masked: str
    attempt_count: int = 1
    error_message: str | None = None
    failure_classification: FailureClassification | None = None
    provider_message_id: str | None = None
    dispatched_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class NotificationDispatchBatchResult(BaseModel):
    """Aggregated outcome of multi-channel notification dispatch."""

    model_config = ConfigDict(extra="ignore")

    event_type: NotificationEventType
    idempotency_key: str
    is_success: bool
    results: list[DeliveryResult]
    total_channels: int
    sent_channels: int
    failed_channels: int
    skipped_channels: int


class ReminderScheduleRequest(BaseModel):
    """Foundation schema for scheduling pre-activity session reminders."""

    model_config = ConfigDict(extra="forbid")

    booking_id: int = Field(ge=1)
    slot_id: int = Field(ge=1)
    user_id: int = Field(ge=1)
    activity_title: str
    session_starts_at: datetime
    reminder_window: Literal["24h_before", "2h_before"]
    scheduled_dispatch_time: datetime
