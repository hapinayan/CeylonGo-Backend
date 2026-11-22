"""
Module 03 — Activity Domain Notification Dispatch Layer (A16).

Provides high-level dispatch helpers for the four canonical activity lifecycle events:
1. Booking Confirmation (dispatch_activity_booking_confirmation)
2. Booking Cancellation (dispatch_activity_booking_cancellation)
3. Schedule Change (dispatch_activity_schedule_change)
4. Session Reminder (dispatch_activity_reminder)

All helpers integrate multi-channel delivery (in-app, email, SMS), safe PII masking,
bounded exponential backoff, and strict idempotency protection.
"""

from datetime import datetime
import logging
from typing import Any

from sqlalchemy.orm import Session

from app.models.activity import Activity
from app.models.activity_booking import ActivityBooking
from app.models.activity_slot import ActivitySlot
from app.models.user import User
from app.schemas.notification_dispatch import (
    EmailPayload,
    InAppPayload,
    NotificationDispatchBatchResult,
    ReminderScheduleRequest,
    SMSPayload,
)
from app.services.notification_dispatch import (
    NotificationDispatchService,
    create_activity_reminder_schedule,
    default_notification_service,
)

logger = logging.getLogger("ceylongo.activity_notifications")


def _try_build_email_payload(
    to_email: str | None,
    subject: str,
    body_text: str,
    body_html: str | None = None,
    recipient_name: str | None = None,
) -> EmailPayload | None:
    """Safely validate and build EmailPayload; returns None if email is missing or invalid."""
    if not to_email:
        return None
    try:
        return EmailPayload(
            to_email=to_email,
            subject=subject,
            body_text=body_text,
            body_html=body_html,
            recipient_name=recipient_name,
        )
    except Exception as exc:
        logger.warning("Could not construct EmailPayload for recipient: %s", exc)
        return None


def _try_build_sms_payload(
    to_phone: str | None,
    message: str,
    recipient_name: str | None = None,
) -> SMSPayload | None:
    """Safely validate and build SMSPayload; returns None if phone is missing or invalid."""
    if not to_phone:
        return None
    try:
        return SMSPayload(
            to_phone=to_phone,
            message=message,
            recipient_name=recipient_name,
        )
    except Exception as exc:
        logger.warning("Could not construct SMSPayload for recipient: %s", exc)
        return None


async def dispatch_activity_booking_confirmation(
    booking: ActivityBooking,
    slot: ActivitySlot,
    activity: Activity,
    user: User,
    db: Session | None = None,
    recipient_phone: str | None = None,
    dispatcher: NotificationDispatchService | None = None,
) -> NotificationDispatchBatchResult:
    """
    Dispatches booking confirmation multi-channel notifications (in-app, email, SMS).
    Includes ticket details, schedule, meeting point, and pricing summary.
    """
    srv = dispatcher or default_notification_service
    idempotency_key = f"act_book_conf_{booking.id}"

    # 1. In-App Payload
    in_app = InAppPayload(
        user_id=user.id,
        title=f"Booking Confirmed: {activity.title}",
        message=(
            f"Your booking #{booking.id} for {activity.title} ({booking.participants} participants) "
            f"is confirmed for {slot.starts_at.strftime('%Y-%m-%d %H:%M')}. "
            f"Meeting point: {activity.meeting_point}."
        ),
        notification_type="booking_confirmed",
        link_url=f"/activities/bookings/{booking.id}",
    )

    # 2. Email Payload
    body_text = (
        f"Dear {user.full_name},\n\n"
        f"Your booking for '{activity.title}' has been successfully confirmed!\n\n"
        f"Booking Reference: #{booking.id}\n"
        f"Activity: {activity.title}\n"
        f"Date & Time: {slot.starts_at.strftime('%A, %B %d, %Y at %I:%M %p')}\n"
        f"Duration: {activity.duration_minutes} minutes\n"
        f"Participants: {booking.participants}\n"
        f"Total Amount: {booking.currency} {booking.total_price:.2f}\n"
        f"Payment Status: {booking.payment_status} ({booking.payment_method})\n"
        f"Meeting Point: {activity.meeting_point}\n\n"
        f"Please arrive 15 minutes before the scheduled start time.\n\n"
        f"Thank you for choosing CeylonGo!"
    )
    body_html = (
        f"<html><body>"
        f"<h2>Booking Confirmed!</h2>"
        f"<p>Dear <strong>{user.full_name}</strong>,</p>"
        f"<p>Your activity booking has been confirmed.</p>"
        f"<table style='border-collapse: collapse; width: 100%; max-width: 600px;'>"
        f"<tr><td><strong>Booking Reference:</strong></td><td>#{booking.id}</td></tr>"
        f"<tr><td><strong>Activity:</strong></td><td>{activity.title}</td></tr>"
        f"<tr><td><strong>Date & Time:</strong></td><td>{slot.starts_at.strftime('%A, %B %d, %Y at %I:%M %p')}</td></tr>"
        f"<tr><td><strong>Participants:</strong></td><td>{booking.participants}</td></tr>"
        f"<tr><td><strong>Total Paid:</strong></td><td>{booking.currency} {booking.total_price:.2f}</td></tr>"
        f"<tr><td><strong>Meeting Point:</strong></td><td>{activity.meeting_point}</td></tr>"
        f"</table>"
        f"<p style='margin-top: 20px;'>Please arrive 15 minutes before departure.</p>"
        f"<p>Safe travels,<br>Team CeylonGo</p>"
        f"</body></html>"
    )
    email = _try_build_email_payload(
        to_email=user.email,
        subject=f"Booking Confirmed: {activity.title} (#{booking.id})",
        body_text=body_text,
        body_html=body_html,
        recipient_name=user.full_name,
    )

    # 3. SMS Payload
    sms_text = (
        f"CeylonGo: Booking #{booking.id} for {activity.title} confirmed! "
        f"Starts {slot.starts_at.strftime('%Y-%m-%d %H:%M')}. "
        f"Meeting: {activity.meeting_point}."
    )
    sms = _try_build_sms_payload(
        to_phone=recipient_phone,
        message=sms_text,
        recipient_name=user.full_name,
    )

    return await srv.dispatch(
        event_type="booking_confirmed",
        idempotency_key=idempotency_key,
        in_app=in_app,
        email=email,
        sms=sms,
        db=db,
    )


async def dispatch_activity_booking_cancellation(
    booking: ActivityBooking,
    slot: ActivitySlot,
    activity: Activity,
    user: User,
    reason: str,
    cancelled_by: str = "traveler",
    db: Session | None = None,
    recipient_phone: str | None = None,
    dispatcher: NotificationDispatchService | None = None,
) -> NotificationDispatchBatchResult:
    """
    Dispatches booking cancellation notification across channels.
    Details cancellation reason, who initiated it, and policy notes.
    """
    srv = dispatcher or default_notification_service
    idempotency_key = f"act_book_cancel_{booking.id}_{cancelled_by}"

    in_app = InAppPayload(
        user_id=user.id,
        title=f"Booking Cancelled: {activity.title}",
        message=(
            f"Your booking #{booking.id} for {activity.title} has been cancelled "
            f"by {cancelled_by}. Reason: {reason}."
        ),
        notification_type="booking_cancelled",
        link_url=f"/activities/bookings/{booking.id}",
    )

    body_text = (
        f"Dear {user.full_name},\n\n"
        f"Your booking #{booking.id} for '{activity.title}' scheduled for "
        f"{slot.starts_at.strftime('%Y-%m-%d %H:%M')} has been cancelled.\n\n"
        f"Cancelled By: {cancelled_by.capitalize()}\n"
        f"Reason: {reason}\n\n"
        f"Refund Notice: If eligible, your refund will be processed in accordance with the "
        f"cancellation policy window.\n\n"
        f"CeylonGo Support"
    )
    body_html = (
        f"<html><body>"
        f"<h2>Booking Cancelled</h2>"
        f"<p>Dear <strong>{user.full_name}</strong>,</p>"
        f"<p>Your booking #{booking.id} for <strong>{activity.title}</strong> has been cancelled.</p>"
        f"<p><strong>Reason:</strong> {reason}</p>"
        f"<p><strong>Cancelled by:</strong> {cancelled_by.capitalize()}</p>"
        f"<p><em>Refund Note: Eligible refunds are credited automatically subject to the activity cancellation policy.</em></p>"
        f"</body></html>"
    )
    email = _try_build_email_payload(
        to_email=user.email,
        subject=f"Booking Cancelled: {activity.title} (#{booking.id})",
        body_text=body_text,
        body_html=body_html,
        recipient_name=user.full_name,
    )

    sms_text = (
        f"CeylonGo: Booking #{booking.id} for {activity.title} cancelled by {cancelled_by}. "
        f"Reason: {reason}. Check app for refund details."
    )
    sms = _try_build_sms_payload(
        to_phone=recipient_phone,
        message=sms_text,
        recipient_name=user.full_name,
    )

    return await srv.dispatch(
        event_type="booking_cancelled",
        idempotency_key=idempotency_key,
        in_app=in_app,
        email=email,
        sms=sms,
        db=db,
    )


async def dispatch_activity_schedule_change(
    slot: ActivitySlot,
    activity: Activity,
    affected_bookings: list[ActivityBooking],
    reason: str,
    new_starts_at: datetime | None = None,
    new_ends_at: datetime | None = None,
    db: Session | None = None,
    dispatcher: NotificationDispatchService | None = None,
) -> list[NotificationDispatchBatchResult]:
    """
    Dispatches schedule-change notifications to all travelers with active bookings
    for a modified or rescheduled session slot.
    """
    srv = dispatcher or default_notification_service
    batch_results: list[NotificationDispatchBatchResult] = []

    for booking in affected_bookings:
        user = booking.traveler
        if not user:
            logger.warning("Booking #%s has no associated traveler user record; skipping", booking.id)
            continue

        idempotency_key = f"act_slot_sched_{slot.id}_{booking.id}"
        time_desc = (
            f"New schedule: {new_starts_at.strftime('%Y-%m-%d %H:%M')}"
            if new_starts_at
            else "The scheduled session time has been changed"
        )

        in_app = InAppPayload(
            user_id=user.id,
            title=f"Schedule Update: {activity.title}",
            message=f"Important schedule change for booking #{booking.id}: {reason}. {time_desc}.",
            notification_type="schedule_changed",
            link_url=f"/activities/bookings/{booking.id}",
        )

        body_text = (
            f"Dear {user.full_name},\n\n"
            f"Please be advised that the schedule for your activity '{activity.title}' "
            f"(Booking #{booking.id}) has been updated.\n\n"
            f"Reason: {reason}\n"
            f"{time_desc}\n\n"
            f"Meeting Point: {activity.meeting_point}\n\n"
            f"If this new schedule does not suit you, please contact support or request a cancellation.\n\n"
            f"Team CeylonGo"
        )
        email = _try_build_email_payload(
            to_email=user.email,
            subject=f"Important Schedule Update: {activity.title} (#{booking.id})",
            body_text=body_text,
            recipient_name=user.full_name,
        )

        sms_text = (
            f"CeylonGo Alert: Schedule update for {activity.title} booking #{booking.id}. "
            f"{time_desc}. Reason: {reason}."
        )
        sms = _try_build_sms_payload(
            to_phone=None,  # User profile lacks phone column; can be extended via traveler profile
            message=sms_text,
            recipient_name=user.full_name,
        )

        res = await srv.dispatch(
            event_type="schedule_changed",
            idempotency_key=idempotency_key,
            in_app=in_app,
            email=email,
            sms=sms,
            db=db,
        )
        batch_results.append(res)

    return batch_results


async def dispatch_activity_reminder(
    booking: ActivityBooking,
    slot: ActivitySlot,
    activity: Activity,
    user: User,
    reminder_window: str = "24h_before",
    db: Session | None = None,
    recipient_phone: str | None = None,
    dispatcher: NotificationDispatchService | None = None,
) -> NotificationDispatchBatchResult:
    """
    Dispatches pre-session reminder notification (e.g. 24h or 2h before departure).
    """
    srv = dispatcher or default_notification_service
    idempotency_key = f"act_reminder_{booking.id}_{reminder_window}"
    window_label = "24 hours" if reminder_window == "24h_before" else "2 hours"

    in_app = InAppPayload(
        user_id=user.id,
        title=f"Upcoming Activity Reminder: {activity.title}",
        message=(
            f"Your activity '{activity.title}' starts in {window_label} "
            f"({slot.starts_at.strftime('%Y-%m-%d %H:%M')}). Meeting: {activity.meeting_point}."
        ),
        notification_type="reminder_scheduled",
        link_url=f"/activities/bookings/{booking.id}",
    )

    body_text = (
        f"Dear {user.full_name},\n\n"
        f"This is a friendly reminder that your upcoming activity '{activity.title}' "
        f"starts in {window_label}!\n\n"
        f"Date & Time: {slot.starts_at.strftime('%A, %B %d, %Y at %I:%M %p')}\n"
        f"Meeting Point: {activity.meeting_point}\n"
        f"Participants: {booking.participants}\n\n"
        f"Please bring comfortable footwear and arrive 15 minutes before departure.\n\n"
        f"Safe travels,\nTeam CeylonGo"
    )
    email = _try_build_email_payload(
        to_email=user.email,
        subject=f"Reminder: {activity.title} starts in {window_label}!",
        body_text=body_text,
        recipient_name=user.full_name,
    )

    sms_text = (
        f"CeylonGo Reminder: '{activity.title}' starts in {window_label} at "
        f"{slot.starts_at.strftime('%H:%M')} on {slot.starts_at.strftime('%Y-%m-%d')}. "
        f"Meeting: {activity.meeting_point}."
    )
    sms = _try_build_sms_payload(
        to_phone=recipient_phone,
        message=sms_text,
        recipient_name=user.full_name,
    )

    return await srv.dispatch(
        event_type="reminder_scheduled",
        idempotency_key=idempotency_key,
        in_app=in_app,
        email=email,
        sms=sms,
        db=db,
    )
