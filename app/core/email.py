import logging
import os
import smtplib
from datetime import date, datetime
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
    token: str | None = None,
    user_name: str | None = None,
    reset_url: str | None = None,
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
    final_reset_url = reset_url or (build_password_reset_url(token) if token else None)
    if not final_reset_url:
        return False
    msg = build_password_reset_email_message(to_email, final_reset_url, user_name)

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


def get_provider_otp_expire_minutes() -> int:
    try:
        return int(os.getenv("PROVIDER_OTP_EXPIRE_MINUTES", "5"))
    except ValueError:
        return 5


def build_provider_otp_email_message(
    to_email: str,
    otp: str,
    purpose: str = "registration",
    user_name: str | None = None,
) -> EmailMessage:
    """
    Construct MIME EmailMessage containing multipart/alternative
    plain-text and CeylonGo-branded HTML content for Service Provider OTP.
    """
    recipient_name = (
        user_name.strip() if user_name and user_name.strip() else "Service Provider"
    )
    expire_minutes = get_provider_otp_expire_minutes()

    if purpose == "resubmission":
        action_desc = "updating your CeylonGo Service Provider application"
        subject_action = "Application Update"
    elif purpose == "password_setup":
        action_desc = "setting up your password for your approved CeylonGo Service Provider account"
        subject_action = "Account Password Setup"
    else:
        action_desc = "registering as a Service Provider on CeylonGo"
        subject_action = "Provider Registration"

    msg = EmailMessage()
    msg["Subject"] = f"CeylonGo — Your Verification Code ({subject_action})"
    msg["From"] = f"{get_smtp_from_name()} <{get_smtp_from_email()}>"
    msg["To"] = to_email

    text_content = (
        f"Hello {recipient_name},\n\n"
        f"Your one-time verification code for {action_desc} is:\n\n"
        f"    {otp}\n\n"
        f"Important Security Information:\n"
        f"- This code will expire in {expire_minutes} minutes.\n"
        f"- This code can only be used once.\n"
        f"- Never share this code with anyone. CeylonGo staff will never ask for your verification code.\n\n"
        f"If you did not request this code, please ignore this email. Your email address remains secure.\n\n"
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
            <p style="color: #FF9F1C; margin: 5px 0 0 0; font-size: 14px; font-weight: bold;">Discover Local. Feel at Home.</p>
        </div>
        <div style="padding: 30px;">
            <p style="font-size: 16px;">Hello <strong>{recipient_name}</strong>,</p>
            <p>Your one-time verification code for {action_desc} is:</p>
            <div style="text-align: center; margin: 28px 0;">
                <div style="font-size: 36px; font-weight: 800; letter-spacing: 8px; color: #082B5C; background: #F8FAFC; padding: 16px 28px; border-radius: 8px; display: inline-block; font-family: 'Courier New', Courier, monospace; border: 1.5px solid #E2E8F0;">
                    {otp}
                </div>
            </div>
            <p style="font-size: 14px; color: #64748B;">This code is valid for <strong>{expire_minutes} minutes</strong> and can only be used once.</p>
            <div style="background-color: #FEF3C7; border-left: 4px solid #FF9F1C; padding: 12px 16px; margin: 20px 0; border-radius: 4px;">
                <p style="margin: 0; font-size: 13px; color: #92400E;">
                    <strong>Security Warning:</strong> Never share this code with anyone. CeylonGo staff will never ask for your verification code.
                </p>
            </div>
            <p style="font-size: 13px; color: #64748B;">If you did not request this verification code, please ignore this email. No changes have been made.</p>
            <hr style="border: none; border-top: 1px solid #EEEEEE; margin: 25px 0;" />
            <p style="font-size: 12px; color: #94A3B8; text-align: center; margin: 0;">CeylonGo — Discover Local. Feel at Home.</p>
        </div>
    </div>
</body>
</html>"""

    msg.set_content(text_content)
    msg.add_alternative(html_content, subtype="html")
    return msg


def send_provider_otp_email(
    to_email: str,
    otp: str,
    purpose: str = "registration",
    user_name: str | None = None,
) -> bool:
    """
    Send a Service Provider verification OTP email via configured SMTP.
    Returns True ONLY if SMTP server accepted the message.
    Returns False if SMTP is unconfigured or if delivery fails.
    Never exposes credentials or raw OTPs in production logs.
    """
    msg = build_provider_otp_email_message(to_email, otp, purpose, user_name)
    return _send_verification_email(msg)


def send_traveler_otp_email(to_email: str, otp: str) -> bool:
    msg = EmailMessage()
    msg["Subject"] = "CeylonGo traveler email verification"
    msg["From"] = f"{get_smtp_from_name()} <{get_smtp_from_email()}>"
    msg["To"] = to_email
    msg.set_content(
        f"Your CeylonGo verification code is {otp}. It expires in 5 minutes.\n"
        "Enter this code only in the registration session you started. "
        "If you did not request it, ignore this email."
    )
    return _send_verification_email(msg)


def _send_verification_email(msg: EmailMessage) -> bool:
    smtp_host = get_smtp_host()
    if not smtp_host:
        logger.warning("SMTP is not configured. Verification email delivery skipped.")
        return False

    smtp_port = get_smtp_port()
    timeout = get_smtp_timeout()

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

            if server.send_message(msg):
                return False
            logger.info("Verification email accepted by SMTP server")
            return True
    except (smtplib.SMTPException, OSError, TimeoutError) as exc:
        logger.error(
            "Failed to send verification email: %s",
            exc.__class__.__name__,
        )
        return False


def build_provider_approval_email_message(
    to_email: str,
    provider_name: str | None = None,
    business_name: str | None = None,
) -> EmailMessage:
    name = (
        provider_name.strip()
        if provider_name and provider_name.strip()
        else "Partner"
    )
    biz = (
        business_name.strip()
        if business_name and business_name.strip()
        else "your business"
    )

    msg = EmailMessage()
    msg["Subject"] = "CeylonGo — Your Service Provider Application Has Been Approved!"
    msg["From"] = f"{get_smtp_from_name()} <{get_smtp_from_email()}>"
    msg["To"] = to_email

    text_content = (
        f"Hello {name},\n\n"
        f"Congratulations! We are delighted to inform you that your Service Provider application for {biz} "
        f"has been reviewed and approved by the CeylonGo Administration Team.\n\n"
        f"Next Steps to Activate Your Account:\n"
        f"1. Return to the CeylonGo Provider Onboarding portal (Step 1).\n"
        f"2. Enter your registered email address ({to_email}) to receive a fresh verification code (OTP).\n"
        f"3. Verify your email to unlock initial password creation.\n"
        f"4. Create a secure password for your provider account.\n"
        f"5. Sign in to your Provider Dashboard via the Provider Login page.\n\n"
        f"For security purposes, credentials or direct login links are never included in email communications.\n\n"
        f"Welcome aboard!\n"
        f"The CeylonGo Team\n"
        f"Discover Local. Feel at Home.\n"
    )

    html_content = f"""<!DOCTYPE html>
<html>
<body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333333; margin: 0; padding: 20px;">
    <div style="max-width: 600px; margin: 0 auto; background: #ffffff; border: 1px solid #e0e0e0; border-radius: 8px; overflow: hidden;">
        <div style="background-color: #082B5C; padding: 24px; text-align: center;">
            <h1 style="color: #ffffff; margin: 0; font-size: 24px;">CeylonGo</h1>
            <p style="color: #FF9F1C; margin: 5px 0 0 0; font-size: 14px; font-weight: bold;">Discover Local. Feel at Home.</p>
        </div>
        <div style="padding: 30px;">
            <div style="background-color: #ECFDF5; border-left: 4px solid #10B981; padding: 14px 18px; margin-bottom: 24px; border-radius: 4px;">
                <p style="margin: 0; font-size: 15px; color: #065F46; font-weight: bold;">
                    Application Approved!
                </p>
            </div>
            <p style="font-size: 16px;">Hello <strong>{name}</strong>,</p>
            <p>Congratulations! We are delighted to inform you that your Service Provider application for <strong>{biz}</strong> has been reviewed and <strong>approved</strong> by the CeylonGo Administration Team.</p>
            <h3 style="color: #082B5C; margin-top: 25px;">Next Steps to Activate Your Account:</h3>
            <ol style="padding-left: 20px; color: #475569;">
                <li style="margin-bottom: 10px;">Return to the <strong>CeylonGo Provider Onboarding portal</strong> (Step 1).</li>
                <li style="margin-bottom: 10px;">Enter your registered email address (<code>{to_email}</code>) to request a fresh verification code (OTP).</li>
                <li style="margin-bottom: 10px;">Verify your OTP to proceed to <strong>password setup</strong>.</li>
                <li style="margin-bottom: 10px;">Create a strong password to secure your account.</li>
                <li style="margin-bottom: 10px;">Sign in to your Provider Dashboard using the separate <strong>Provider Login</strong> page.</li>
            </ol>
            <div style="background-color: #FEF3C7; border-left: 4px solid #FF9F1C; padding: 12px 16px; margin: 25px 0; border-radius: 4px;">
                <p style="margin: 0; font-size: 13px; color: #92400E;">
                    <strong>Security Notice:</strong> CeylonGo will never send passwords or direct login tokens via email. Please complete your password setup through the official onboarding portal.
                </p>
            </div>
            <p style="font-size: 14px; color: #64748B;">Welcome to the CeylonGo community. We look forward to working with you!</p>
            <hr style="border: none; border-top: 1px solid #EEEEEE; margin: 25px 0;" />
            <p style="font-size: 12px; color: #94A3B8; text-align: center; margin: 0;">CeylonGo — Discover Local. Feel at Home.</p>
        </div>
    </div>
</body>
</html>"""

    msg.set_content(text_content)
    msg.add_alternative(html_content, subtype="html")
    return msg


def send_provider_approval_email(
    to_email: str,
    provider_name: str | None = None,
    business_name: str | None = None,
) -> bool:
    if not is_smtp_configured():
        logger.warning(
            "SMTP is not configured. Provider approval email delivery skipped for %s.",
            to_email,
        )
        return False

    smtp_host = get_smtp_host()
    smtp_port = get_smtp_port()
    timeout = get_smtp_timeout()
    msg = build_provider_approval_email_message(to_email, provider_name, business_name)

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
            logger.info("Provider approval email dispatched to %s", to_email)
            return True
    except (smtplib.SMTPException, OSError, TimeoutError) as exc:
        logger.error(
            "Failed to send provider approval email to %s: %s",
            to_email,
            exc.__class__.__name__,
        )
        return False


def build_provider_rejection_email_message(
    to_email: str,
    provider_name: str | None = None,
    business_name: str | None = None,
    rejection_reason: str = "Application details could not be verified.",
) -> EmailMessage:
    name = (
        provider_name.strip()
        if provider_name and provider_name.strip()
        else "Partner"
    )
    biz = (
        business_name.strip()
        if business_name and business_name.strip()
        else "your business"
    )

    msg = EmailMessage()
    msg["Subject"] = "CeylonGo — Update Regarding Your Service Provider Application"
    msg["From"] = f"{get_smtp_from_name()} <{get_smtp_from_email()}>"
    msg["To"] = to_email

    text_content = (
        f"Hello {name},\n\n"
        f"Thank you for your interest in partnering with CeylonGo for {biz}.\n\n"
        f"After careful review by our administrative team, we regret to inform you that your application "
        f"cannot be approved in its current form.\n\n"
        f"Reason for rejection:\n"
        f"{rejection_reason}\n\n"
        f"How to Resubmit Your Application:\n"
        f"1. Return to the CeylonGo Provider Onboarding portal.\n"
        f"2. Enter your registered email address ({to_email}) to receive a fresh verification code (OTP).\n"
        f"3. Verify your OTP to enter the application resubmission workflow.\n"
        f"4. Correct the required details or re-upload clear identity verification documents.\n"
        f"5. Resubmit your application for review.\n\n"
        f"If you have questions, please reach out to our support team.\n\n"
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
            <p style="color: #FF9F1C; margin: 5px 0 0 0; font-size: 14px; font-weight: bold;">Discover Local. Feel at Home.</p>
        </div>
        <div style="padding: 30px;">
            <div style="background-color: #FEF2F2; border-left: 4px solid #EF4444; padding: 14px 18px; margin-bottom: 24px; border-radius: 4px;">
                <p style="margin: 0; font-size: 15px; color: #991B1B; font-weight: bold;">
                    Application Requires Attention
                </p>
            </div>
            <p style="font-size: 16px;">Hello <strong>{name}</strong>,</p>
            <p>Thank you for your interest in partnering with CeylonGo for <strong>{biz}</strong>. After careful review, our administrative team was unable to approve your application at this time.</p>
            <div style="background-color: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 6px; padding: 16px; margin: 20px 0;">
                <p style="margin: 0 0 6px 0; font-size: 13px; font-weight: bold; color: #475569; text-transform: uppercase; letter-spacing: 0.5px;">Reason for Rejection:</p>
                <p style="margin: 0; font-size: 15px; color: #1E293B;">{rejection_reason}</p>
            </div>
            <h3 style="color: #082B5C; margin-top: 25px;">How to Resubmit Your Application:</h3>
            <ol style="padding-left: 20px; color: #475569;">
                <li style="margin-bottom: 10px;">Return to the <strong>CeylonGo Provider Onboarding portal</strong>.</li>
                <li style="margin-bottom: 10px;">Enter your registered email address (<code>{to_email}</code>) to receive a fresh verification code (OTP).</li>
                <li style="margin-bottom: 10px;">Verify your OTP to open your saved application draft in resubmission mode.</li>
                <li style="margin-bottom: 10px;">Update the necessary information or re-upload clear identity verification documents.</li>
                <li style="margin-bottom: 10px;">Resubmit your application for review.</li>
            </ol>
            <p style="font-size: 14px; color: #64748B;">If you have any questions or require further assistance, please contact our support team.</p>
            <hr style="border: none; border-top: 1px solid #EEEEEE; margin: 25px 0;" />
            <p style="font-size: 12px; color: #94A3B8; text-align: center; margin: 0;">CeylonGo — Discover Local. Feel at Home.</p>
        </div>
    </div>
</body>
</html>"""

    msg.set_content(text_content)
    msg.add_alternative(html_content, subtype="html")
    return msg


def send_provider_rejection_email(
    to_email: str,
    provider_name: str | None = None,
    business_name: str | None = None,
    rejection_reason: str = "Application details could not be verified.",
) -> bool:
    if not is_smtp_configured():
        logger.warning(
            "SMTP is not configured. Provider rejection email delivery skipped for %s.",
            to_email,
        )
        return False

    smtp_host = get_smtp_host()
    smtp_port = get_smtp_port()
    timeout = get_smtp_timeout()
    msg = build_provider_rejection_email_message(
        to_email, provider_name, business_name, rejection_reason
    )

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
            logger.info("Provider rejection email dispatched to %s", to_email)
            return True
    except (smtplib.SMTPException, OSError, TimeoutError) as exc:
        logger.error(
            "Failed to send provider rejection email to %s: %s",
            to_email,
            exc.__class__.__name__,
        )
        return False


# ==============================================================================
# M04 Eco-Tourism Badge Verification Decision Notifications
# ==============================================================================


def build_eco_badge_decision_email_message(
    to_email: str,
    experience_title: str,
    decision: str,
    provider_name: str | None = None,
    business_name: str | None = None,
    reason: str | None = None,
    valid_until: datetime | date | str | None = None,
) -> EmailMessage:
    """
    Construct MIME EmailMessage for Eco Badge verification decision notifications
    (granted, rejected, or revoked) with CeylonGo branding, clear next actions,
    and safe plain-text and HTML versions.
    SECURITY: Never includes private evidence documents, file paths, tokens, or credentials.
    """
    name = (
        provider_name.strip()
        if provider_name and provider_name.strip()
        else "Eco Partner"
    )
    biz = (
        business_name.strip()
        if business_name and business_name.strip()
        else "your eco-tourism business"
    )
    title = experience_title.strip() if experience_title else "Eco Experience"

    validity_str = "Ongoing (Subject to periodic audit)"
    if valid_until:
        if isinstance(valid_until, (datetime, date)):
            validity_str = valid_until.strftime("%B %d, %Y")
        else:
            validity_str = str(valid_until)[:10]

    reason_text = (
        reason.strip()
        if reason and reason.strip()
        else "Administrative verification review completed."
    )

    norm_decision = decision.strip().lower()

    msg = EmailMessage()
    msg["From"] = f"{get_smtp_from_name()} <{get_smtp_from_email()}>"
    msg["To"] = to_email

    if norm_decision == "granted":
        msg["Subject"] = f"CeylonGo — Eco-Friendly Badge Granted for '{title}'"
        banner_bg = "#ECFDF5"
        banner_border = "#10B981"
        banner_title_color = "#065F46"
        banner_title = "Eco-Friendly Badge Verified & Granted!"

        text_content = (
            f"Hello {name},\n\n"
            f"Congratulations! We are delighted to inform you that the sustainability claims and conservation "
            f"practices for your eco experience \"{title}\" ({biz}) have been reviewed and approved by the "
            f"CeylonGo Administration Team.\n\n"
            f"The official CeylonGo Eco-Friendly Badge has been granted to your listing.\n\n"
            f"Verification Details:\n"
            f"- Experience: {title}\n"
            f"- Badge Status: Verified & Granted\n"
            f"- Validity: {validity_str}\n"
            f"- Reviewer Note: {reason_text}\n\n"
            f"What this means for your listing:\n"
            f"1. Your experience now prominently displays the certified Eco-Friendly Badge in search and detail views.\n"
            f"2. Travelers filtering for eco-certified activities will discover your listing.\n"
            f"3. Your verified sustainability practices are showcased to conscious travelers.\n\n"
            f"Next Steps & Responsibilities:\n"
            f"- Please maintain ongoing adherence to your approved environmental and wildlife safety guidelines.\n"
            f"- Keep community records and permit documentation current for periodic administrative audits.\n\n"
            f"Thank you for championing sustainable tourism in Sri Lanka!\n\n"
            f"Warm regards,\n"
            f"The CeylonGo Team\n"
            f"Discover Local. Feel at Home.\n"
        )

        decision_html_block = f"""
            <div style="background-color: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 6px; padding: 18px; margin: 20px 0;">
                <p style="margin: 0 0 8px 0; font-size: 14px; color: #1E293B;"><strong>Experience:</strong> {title}</p>
                <p style="margin: 0 0 8px 0; font-size: 14px; color: #065F46;"><strong>Status:</strong> Verified & Granted</p>
                <p style="margin: 0 0 8px 0; font-size: 14px; color: #475569;"><strong>Valid Until:</strong> {validity_str}</p>
                <p style="margin: 0; font-size: 14px; color: #475569;"><strong>Reviewer Note:</strong> {reason_text}</p>
            </div>
            <h3 style="color: #082B5C; margin-top: 25px; font-size: 16px;">What This Means For Your Listing:</h3>
            <ul style="padding-left: 20px; color: #475569; font-size: 14px; line-height: 1.6;">
                <li style="margin-bottom: 8px;">Your listing prominently displays the certified <strong>Eco-Friendly Badge</strong> in search and detail views.</li>
                <li style="margin-bottom: 8px;">Travelers filtering for eco-certified activities will easily discover your listing.</li>
                <li style="margin-bottom: 8px;">Your verified sustainability practices are highlighted for conscious travelers.</li>
            </ul>
            <h3 style="color: #082B5C; margin-top: 20px; font-size: 16px;">Next Steps:</h3>
            <ul style="padding-left: 20px; color: #475569; font-size: 14px; line-height: 1.6;">
                <li style="margin-bottom: 8px;">Maintain ongoing compliance with your published environmental and wildlife guidelines.</li>
                <li style="margin-bottom: 8px;">Keep community engagement and conservation records ready for periodic reviews.</li>
            </ul>
        """

    elif norm_decision == "rejected":
        msg["Subject"] = f"CeylonGo — Update Regarding Eco-Friendly Badge for '{title}'"
        banner_bg = "#FEF2F2"
        banner_border = "#EF4444"
        banner_title_color = "#991B1B"
        banner_title = "Eco-Friendly Badge Verification Requires Attention"

        text_content = (
            f"Hello {name},\n\n"
            f"Thank you for submitting your sustainability claims and evidence for your eco experience \"{title}\" ({biz}).\n\n"
            f"After careful review by the CeylonGo Administration Team, we regret to inform you that your listing "
            f"cannot be granted the Eco-Friendly Badge at this time.\n\n"
            f"Decision Details:\n"
            f"- Experience: {title}\n"
            f"- Badge Status: Rejected / Needs Revision\n"
            f"- Reason / Feedback: {reason_text}\n\n"
            f"How to Resubmit for Verification:\n"
            f"1. Log in to your CeylonGo Provider Dashboard.\n"
            f"2. Navigate to your listing's Sustainability Practices section.\n"
            f"3. Review the feedback provided above and update your practice descriptions or upload valid supporting evidence documents.\n"
            f"4. Click 'Submit for Verification' when your updated documentation is ready.\n\n"
            f"Please Note:\n"
            f"Your eco experience listing itself remains in its current status. Only the specialized Eco-Friendly Badge was declined.\n\n"
            f"Warm regards,\n"
            f"The CeylonGo Team\n"
            f"Discover Local. Feel at Home.\n"
        )

        decision_html_block = f"""
            <div style="background-color: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 6px; padding: 18px; margin: 20px 0;">
                <p style="margin: 0 0 8px 0; font-size: 14px; color: #1E293B;"><strong>Experience:</strong> {title}</p>
                <p style="margin: 0 0 8px 0; font-size: 14px; color: #991B1B;"><strong>Status:</strong> Verification Declined</p>
                <p style="margin: 0; font-size: 14px; color: #475569;"><strong>Reason / Feedback:</strong> {reason_text}</p>
            </div>
            <h3 style="color: #082B5C; margin-top: 25px; font-size: 16px;">How to Resubmit for Verification:</h3>
            <ol style="padding-left: 20px; color: #475569; font-size: 14px; line-height: 1.6;">
                <li style="margin-bottom: 8px;">Log in to your <strong>CeylonGo Provider Dashboard</strong>.</li>
                <li style="margin-bottom: 8px;">Navigate to your listing's <strong>Sustainability Practices</strong> tab.</li>
                <li style="margin-bottom: 8px;">Review the reviewer feedback and update your practice details or re-upload clear evidence documents.</li>
                <li style="margin-bottom: 8px;">Click <strong>Submit for Verification</strong> once updated.</li>
            </ol>
            <p style="font-size: 13px; color: #64748B; margin-top: 15px;">
                <em>Note: Your experience listing itself remains in its current listing status; only the Eco-Friendly Badge was not granted.</em>
            </p>
        """

    elif norm_decision == "revoked":
        msg["Subject"] = f"CeylonGo — Notice: Eco-Friendly Badge Revoked for '{title}'"
        banner_bg = "#FFFBEB"
        banner_border = "#F59E0B"
        banner_title_color = "#92400E"
        banner_title = "Notice: Eco-Friendly Badge Status Update"

        text_content = (
            f"Hello {name},\n\n"
            f"This is an official notification regarding your eco experience \"{title}\" ({biz}) on CeylonGo.\n\n"
            f"Following an administrative audit or compliance review, the Eco-Friendly Badge associated with this "
            f"experience has been revoked.\n\n"
            f"Revocation Details:\n"
            f"- Experience: {title}\n"
            f"- Badge Status: Revoked\n"
            f"- Stated Reason: {reason_text}\n\n"
            f"Impact on Your Listing:\n"
            f"- The Eco-Friendly Badge has been immediately removed from your listing in search results and public views.\n"
            f"- Previously verified sustainability practices have been marked unverified.\n\n"
            f"Next Steps:\n"
            f"If you believe this action was taken in error or if you have remedied the non-compliance reasons cited above, "
            f"please contact the CeylonGo Administration Team or submit updated verification documentation through your Provider Dashboard.\n\n"
            f"Warm regards,\n"
            f"The CeylonGo Team\n"
            f"Discover Local. Feel at Home.\n"
        )

        decision_html_block = f"""
            <div style="background-color: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 6px; padding: 18px; margin: 20px 0;">
                <p style="margin: 0 0 8px 0; font-size: 14px; color: #1E293B;"><strong>Experience:</strong> {title}</p>
                <p style="margin: 0 0 8px 0; font-size: 14px; color: #92400E;"><strong>Badge Status:</strong> Revoked</p>
                <p style="margin: 0; font-size: 14px; color: #475569;"><strong>Stated Reason:</strong> {reason_text}</p>
            </div>
            <h3 style="color: #082B5C; margin-top: 25px; font-size: 16px;">Impact on Your Listing:</h3>
            <ul style="padding-left: 20px; color: #475569; font-size: 14px; line-height: 1.6;">
                <li style="margin-bottom: 8px;">The Eco-Friendly Badge has been removed from your listing in search and detail views.</li>
                <li style="margin-bottom: 8px;">Associated sustainability practices are no longer marked as verified.</li>
            </ul>
            <h3 style="color: #082B5C; margin-top: 20px; font-size: 16px;">Next Steps:</h3>
            <p style="color: #475569; font-size: 14px; line-height: 1.6;">
                If you have remedied the compliance issues noted above or have additional evidence to submit, you may update your sustainability records and re-request verification via the Provider Dashboard, or contact the CeylonGo Support Team.
            </p>
        """

    else:
        raise ValueError(f"Unsupported badge decision '{decision}' for email template.")

    html_content = f"""<!DOCTYPE html>
<html>
<body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333333; margin: 0; padding: 20px;">
    <div style="max-width: 600px; margin: 0 auto; background: #ffffff; border: 1px solid #e0e0e0; border-radius: 8px; overflow: hidden;">
        <div style="background-color: #082B5C; padding: 24px; text-align: center;">
            <h1 style="color: #ffffff; margin: 0; font-size: 24px;">CeylonGo</h1>
            <p style="color: #FF9F1C; margin: 5px 0 0 0; font-size: 14px; font-weight: bold;">Discover Local. Feel at Home.</p>
        </div>
        <div style="padding: 30px;">
            <div style="background-color: {banner_bg}; border-left: 4px solid {banner_border}; padding: 14px 18px; margin-bottom: 24px; border-radius: 4px;">
                <p style="margin: 0; font-size: 15px; color: {banner_title_color}; font-weight: bold;">
                    {banner_title}
                </p>
            </div>
            <p style="font-size: 16px;">Hello <strong>{name}</strong>,</p>
            {decision_html_block}
            <div style="background-color: #FEF3C7; border-left: 4px solid #FF9F1C; padding: 12px 16px; margin: 25px 0; border-radius: 4px;">
                <p style="margin: 0; font-size: 13px; color: #92400E;">
                    <strong>Security Notice:</strong> CeylonGo will never request your account password, payment credentials, or personal identity documents via email.
                </p>
            </div>
            <p style="font-size: 14px; color: #64748B;">Warm regards,<br/>The CeylonGo Team</p>
            <hr style="border: none; border-top: 1px solid #EEEEEE; margin: 25px 0;" />
            <p style="font-size: 12px; color: #94A3B8; text-align: center; margin: 0;">CeylonGo — Discover Local. Feel at Home.</p>
        </div>
    </div>
</body>
</html>"""

    msg.set_content(text_content)
    msg.add_alternative(html_content, subtype="html")
    return msg


def send_eco_badge_decision_email(
    to_email: str,
    experience_title: str,
    decision: str,
    provider_name: str | None = None,
    business_name: str | None = None,
    reason: str | None = None,
    valid_until: datetime | date | str | None = None,
) -> bool:
    """
    Send an Eco Badge verification decision email via configured SMTP.
    Returns True ONLY if SMTP server accepted the message.
    Returns False if SMTP is unconfigured or if delivery fails.
    Never exposes credentials, private file paths, or tokens in logs.
    """
    if not is_smtp_configured():
        logger.warning(
            "SMTP is not configured (SMTP_HOST missing). Eco badge notification skipped for %s.",
            to_email,
        )
        return False

    smtp_host = get_smtp_host()
    smtp_port = get_smtp_port()
    timeout = get_smtp_timeout()
    msg = build_eco_badge_decision_email_message(
        to_email=to_email,
        experience_title=experience_title,
        decision=decision,
        provider_name=provider_name,
        business_name=business_name,
        reason=reason,
        valid_until=valid_until,
    )

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
            logger.info(
                "Eco badge decision email dispatched to %s for decision=%s",
                to_email,
                decision,
            )
            return True
    except (smtplib.SMTPException, OSError, TimeoutError) as exc:
        logger.error(
            "Failed to send eco badge decision email to %s: %s",
            to_email,
            exc.__class__.__name__,
        )
        return False


def send_admin_invitation_email(to_email: str, activation_url: str) -> bool:
    msg = EmailMessage()
    msg["Subject"] = "Your CeylonGo administrator invitation"
    msg["From"] = f"{get_smtp_from_name()} <{get_smtp_from_email()}>"
    msg["To"] = to_email
    msg.set_content(
        f"Set your CeylonGo administrator password using this link:\n{activation_url}\n\n"
        "The link expires in 48 hours and can be used once. "
        "If you did not expect this invitation, contact your CeylonGo administrator."
    )
    html_content = f"""<!DOCTYPE html>
<html>
<body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333333; margin: 0; padding: 20px;">
    <div style="max-width: 600px; margin: 0 auto; background: #ffffff; border: 1px solid #e0e0e0; border-radius: 8px; overflow: hidden;">
        <div style="background-color: #082B5C; padding: 24px; text-align: center;">
            <h1 style="color: #ffffff; margin: 0; font-size: 24px;">CeylonGo</h1>
            <p style="color: #FF9F1C; margin: 5px 0 0 0; font-size: 14px;">Administrator Portal Invitation</p>
        </div>
        <div style="padding: 30px;">
            <p>You have been invited to join the <strong>CeylonGo</strong> administration portal.</p>
            <p>Click the button below to set your password and activate your administrator account:</p>
            <p style="text-align: center; margin: 30px 0;">
                <a href="{activation_url}" style="background-color: #FF9F1C; color: #ffffff; padding: 14px 28px; text-decoration: none; border-radius: 6px; font-weight: bold; display: inline-block;">
                    Activate Admin Account
                </a>
            </p>
            <p style="font-size: 14px; color: #666666;">This link expires in <strong>48 hours</strong> and can only be used once.</p>
            <p style="font-size: 14px; color: #666666;">If you did not expect this invitation, please contact your CeylonGo Super Administrator.</p>
        </div>
    </div>
</body>
</html>"""
    msg.add_alternative(html_content, subtype="html")
    return _send_verification_email(msg)
