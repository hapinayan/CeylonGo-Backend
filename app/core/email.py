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


def get_smtp_timeout() -> int:
    try:
        return int(os.getenv("SMTP_TIMEOUT_SECONDS", "10"))
    except ValueError:
        return 10


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


def is_smtp_configured() -> bool:
    """Return True if SMTP host is specified."""
    return get_smtp_host() is not None


def get_frontend_reset_password_url() -> str:
    return os.getenv(
        "FRONTEND_RESET_PASSWORD_URL", "http://localhost:4200/reset-password"
    ).strip()


def build_password_reset_url(token: str) -> str:
    """Construct full frontend password reset URL with the given token."""
    frontend_url = get_frontend_reset_password_url()
    separator = "&" if "?" in frontend_url else "?"
    return f"{frontend_url}{separator}token={token}"


def build_password_reset_email_message(
    to_email: str,
    reset_url: str,
    user_name: str | None = None,
) -> EmailMessage:
    """
    Construct MIME EmailMessage containing multipart/alternative
    plain-text and CeylonGo-branded HTML content.
    """
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
        f"Please click the link below or copy and paste it into your browser to choose a new password:\n\n"
        f"{reset_url}\n\n"
        f"Important Notice:\n"
        f"- This link will expire in 30 minutes.\n"
        f"- This link can only be used once.\n\n"
        f"If you did not request this password reset, please ignore this email or contact support. "
        f"Your account password will remain unchanged and secure.\n\n"
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
            <p style="font-size: 16px;">Hello <strong>{recipient_name}</strong>,</p>
            <p>We received a request to reset your password. Click the button below to choose a new password:</p>
            <p style="text-align: center; margin: 30px 0;">
                <a href="{reset_url}" style="background-color: #FF9F1C; color: #ffffff; padding: 14px 28px; text-decoration: none; border-radius: 6px; font-weight: bold; display: inline-block;">
                    Reset Password
                </a>
            </p>
            <p style="font-size: 14px; color: #666666;">This link will expire in <strong>30 minutes</strong> and can only be used once.</p>
            <p style="font-size: 14px; color: #666666;">If you did not request a password reset, you can safely ignore this email. Your account remains secure.</p>
            <hr style="border: none; border-top: 1px solid #eeeeee; margin: 25px 0;" />
            <p style="font-size: 12px; color: #999999;">If the button above does not work, copy and paste this URL into your browser:<br/><a href="{reset_url}" style="color: #082B5C;">{reset_url}</a></p>
        </div>
    </div>
</body>
</html>"""

    msg.set_content(text_content)
    msg.add_alternative(html_content, subtype="html")
    return msg


def send_password_reset_email(
    to_email: str,
    token: str,
    user_name: str | None = None,
) -> bool:
    """
    Send a password reset email via configured SMTP.
    Returns True ONLY if SMTP server accepted the message.
    Returns False if SMTP is unconfigured or if delivery fails.
    Never exposes credentials or raw reset tokens in production logs.
    """
    if not is_smtp_configured():
        logger.warning(
            "SMTP is not configured (SMTP_HOST is missing). Password reset email delivery skipped for recipient."
        )
        return False

    smtp_host = get_smtp_host()
    smtp_port = get_smtp_port()
    timeout = get_smtp_timeout()
    reset_url = build_password_reset_url(token)
    msg = build_password_reset_email_message(to_email, reset_url, user_name)

    try:
        server_class = smtplib.SMTP_SSL if smtp_port == 465 else smtplib.SMTP
        with server_class(smtp_host, smtp_port, timeout=timeout) as server:
            server.ehlo()
            if is_smtp_use_tls() and smtp_port != 465:
                server.starttls()
                server.ehlo()

            username = get_smtp_username()
            password = get_smtp_password()
            if username and password:
                server.login(username, password)

            server.send_message(msg)
            logger.info("Password reset email accepted by SMTP server for %s", to_email)
            return True
    except (smtplib.SMTPException, OSError, TimeoutError) as exc:
        logger.error(
            "Failed to send password reset email to %s: %s",
            to_email,
            exc.__class__.__name__,
        )
        return False
