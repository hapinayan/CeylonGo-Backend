
import logging
import os
import smtplib
from email.message import EmailMessage

logger = logging.getLogger(__name__)


def get_smtp_host() -> str | None:
    host = os.getenv("SMTP_HOST")
    return host.strip() if host and host.strip() else None


def get_smtp_port() -> int:
    try:
        return int(os.getenv("SMTP_PORT", "587"))
    except ValueError:
        return 587


def get_smtp_username() -> str | None:
    user = os.getenv("SMTP_USERNAME")
    return user.strip() if user and user.strip() else None


def get_smtp_password() -> str | None:
    return os.getenv("SMTP_PASSWORD")


def get_smtp_from_email() -> str:
    return os.getenv("SMTP_FROM_EMAIL", "noreply@ceylongo.com").strip()


def get_smtp_from_name() -> str:
    return os.getenv("SMTP_FROM_NAME", "CeylonGo").strip()


def is_smtp_use_tls() -> bool:
    return os.getenv("SMTP_USE_TLS", "true").lower() in ("true", "1", "yes")


def get_frontend_reset_password_url() -> str:
    return os.getenv(
        "FRONTEND_RESET_PASSWORD_URL", "http://localhost:4200/reset-password"
    ).strip()


def send_password_reset_email(
    to_email: str,
    token: str,
    user_name: str | None = None,
) -> bool:
    """
    Send a password reset email containing a single-use token link.
    Handles unconfigured SMTP or connection failures safely without crashing or leaking credentials.
    """
    frontend_url = get_frontend_reset_password_url()
    # Support query parameter appending
    separator = "&" if "?" in frontend_url else "?"
    reset_url = f"{frontend_url}{separator}token={token}"

    smtp_host = get_smtp_host()
    if not smtp_host:
        logger.info(
            "SMTP_HOST not set. Mock password reset link dispatched to %s: %s",
            to_email,
            reset_url,
        )
        return True

    recipient_name = (
        user_name.strip() if user_name and user_name.strip() else "Traveler"
    )

    msg = EmailMessage()
    msg["Subject"] = "CeylonGo — Reset Your Password"
    msg["From"] = f"{get_smtp_from_name()} <{get_smtp_from_email()}>"
    msg["To"] = to_email

    text_content = (
        f"Hello {recipient_name},\n\n"
        f"We received a request to reset your password for your CeylonGo account.\n"
        f"Please click the link below to choose a new password:\n\n"
        f"{reset_url}\n\n"
        f"This link will expire in 30 minutes.\n\n"
        f"If you did not request this password reset, please ignore this email. "
        f"Your account remains secure.\n\n"
        f"Warm regards,\n"
        f"The CeylonGo Team\n"
        f"Discover Local. Feel at Home.\n"
    )

    html_content = f"""<!DOCTYPE html>
<html>
<body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333333; margin: 0; padding: 20px;">
    <div style="max-width: 600px; margin: 0 auto; background: #ffffff; border: 1px solid #e0e0e0; border-radius: 8px; overflow: hidden;">
        <div style="background-color: #082B5C; padding: 24px; text-align: center;">
            <h1 style="color: #ffffff; margin: 0; font-size: 24px;">CeylonGo</h1>
            <p style="color: #FF9F1C; margin: 5px 0 0 0; font-size: 14px;">Discover Local. Feel at Home.</p>
        </div>
        <div style="padding: 30px;">
            <p style="font-size: 16px;">Hello {recipient_name},</p>
            <p>We received a request to reset your password. Click the button below to choose a new password:</p>
            <p style="text-align: center; margin: 30px 0;">
                <a href="{reset_url}" style="background-color: #FF9F1C; color: #ffffff; padding: 14px 28px; text-decoration: none; border-radius: 6px; font-weight: bold; display: inline-block;">
                    Reset Password
                </a>
            </p>
            <p style="font-size: 14px; color: #666666;">This link will expire in <strong>30 minutes</strong>.</p>
            <p style="font-size: 14px; color: #666666;">If you did not request a password reset, you can safely ignore this email.</p>
            <hr style="border: none; border-top: 1px solid #eeeeee; margin: 25px 0;" />
            <p style="font-size: 12px; color: #999999;">If the button above does not work, copy and paste this URL into your browser:<br/><a href="{reset_url}" style="color: #082B5C;">{reset_url}</a></p>
        </div>
    </div>
</body>
</html>"""

    msg.set_content(text_content)
    msg.add_alternative(html_content, subtype="html")

    try:
        with smtplib.SMTP(smtp_host, get_smtp_port(), timeout=10) as server:
            if is_smtp_use_tls():
                server.starttls()
            username = get_smtp_username()
            password = get_smtp_password()
            if username and password:
                server.login(username, password)
            server.send_message(msg)
            return True
    except (smtplib.SMTPException, OSError, TimeoutError) as exc:
        logger.error(
            "Failed to send password reset email to %s: %s",
            to_email,
            exc.__class__.__name__,
        )
        return False
