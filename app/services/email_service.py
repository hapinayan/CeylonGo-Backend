import logging
import os
import smtplib
from email.message import EmailMessage

logger = logging.getLogger("ceylongo.email")


class EmailService:
    """Service handling transactional email delivery with pluggable backends."""

    def __init__(self):
        self.backend = os.getenv("EMAIL_DELIVERY_BACKEND", "console").strip().lower()
        self.frontend_reset_url = os.getenv(
            "FRONTEND_PASSWORD_RESET_URL", "http://localhost:4200/create-new-password"
        ).rstrip("/")

        # SMTP Configuration (optional until configured for production)
        self.smtp_host = os.getenv("SMTP_HOST", "")
        self.smtp_port = int(os.getenv("SMTP_PORT", "587"))
        self.smtp_username = os.getenv("SMTP_USERNAME", "")
        self.smtp_password = os.getenv("SMTP_PASSWORD", "")
        self.smtp_use_tls = os.getenv("SMTP_USE_TLS", "yes").strip().lower() in (
            "yes",
            "true",
            "1",
        )
        self.from_email = os.getenv("SMTP_FROM_EMAIL", "noreply@ceylongo.com")
        self.from_name = os.getenv("SMTP_FROM_NAME", "CeylonGo Support")

    def send_password_reset_email(
        self, recipient_email: str, raw_token: str, expires_in_minutes: int = 15
    ) -> bool:
        """Dispatch password reset instructions using the active delivery backend."""
        reset_link = f"{self.frontend_reset_url}?token={raw_token}"

        if self.backend == "console":
            # Clearly marked development-only output. Never active in production.
            print("\n" + "=" * 80)
            print(" [DEVELOPMENT ONLY] CeylonGo Password Reset Secret")
            print(" (This message is emitted because EMAIL_DELIVERY_BACKEND=console)")
            print("=" * 80)
            print(f" Recipient:   {recipient_email}")
            print(f" Reset Token: {raw_token}")
            print(f" Reset Link:  {reset_link}")
            print(f" Validity:    {expires_in_minutes} minutes")
            print("=" * 80 + "\n", flush=True)
            return True

        elif self.backend == "smtp":
            if not self.smtp_host or not self.smtp_username:
                logger.warning(
                    "SMTP email delivery requested but SMTP_HOST or SMTP_USERNAME is not configured."
                )
                return False

            try:
                msg = EmailMessage()
                msg["Subject"] = "Reset Your CeylonGo Password"
                msg["From"] = f"{self.from_name} <{self.from_email}>"
                msg["To"] = recipient_email

                plain_content = (
                    f"Hello,\n\n"
                    f"We received a request to reset your password for your CeylonGo account.\n"
                    f"Click the link below or enter this token in the app to set a new password:\n\n"
                    f"{reset_link}\n\n"
                    f"Reset Token: {raw_token}\n\n"
                    f"This link is valid for {expires_in_minutes} minutes and can only be used once.\n"
                    f"If you did not request this, please ignore this email.\n\n"
                    f"Discover Local. Feel at Home.\n"
                    f"The CeylonGo Team"
                )
                msg.set_content(plain_content)

                if self.smtp_port == 465:
                    with smtplib.SMTP_SSL(self.smtp_host, self.smtp_port) as server:
                        server.login(self.smtp_username, self.smtp_password)
                        server.send_message(msg)
                else:
                    with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                        if self.smtp_use_tls:
                            server.starttls()
                        server.login(self.smtp_username, self.smtp_password)
                        server.send_message(msg)

                return True
            except Exception as e:
                # Never log raw tokens or credentials
                logger.error(f"Failed to deliver SMTP reset email: {e.__class__.__name__}")
                return False

        else:
            logger.debug(f"Email delivery skipped; unknown backend: {self.backend}")
            return False


email_service = EmailService()
