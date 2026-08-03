"""SMTP provider using environment-managed credentials."""

import logging
import os
import smtplib
from email.message import EmailMessage
from email.utils import formataddr

from app.communications.delivery import OutboundMessage, ProviderResult


logger = logging.getLogger(__name__)

_TRUE_VALUES = {"1", "true", "yes", "on"}
_SAFE_MOCK_ENVIRONMENTS = {
    "development",
    "dev",
    "test",
    "testing",
    "local",
}


class SMTPEmailProvider:
    def __init__(self):
        self.host = os.getenv("SMTP_HOST", "").strip()
        self.port = int(os.getenv("SMTP_PORT", "587"))
        self.username = os.getenv("SMTP_USER", "").strip()
        self.password = os.getenv("SMTP_PASSWORD", "")
        self.use_tls = os.getenv(
            "SMTP_USE_TLS",
            "true",
        ).lower() not in {
            "0",
            "false",
            "no",
        }
        self.environment = os.getenv(
            "ENVIRONMENT",
            os.getenv("APP_ENV", "development"),
        ).strip().lower()
        mock_default = (
            "true"
            if self.environment in _SAFE_MOCK_ENVIRONMENTS
            else "false"
        )
        self.mock_enabled = os.getenv(
            "COMMUNICATIONS_SMTP_MOCK",
            mock_default,
        ).strip().lower() in _TRUE_VALUES

    @property
    def configured(self) -> bool:
        return bool(
            self.host
            and self.username
            and self.password
        )

    @property
    def mock_allowed(self) -> bool:
        return (
            self.environment in _SAFE_MOCK_ENVIRONMENTS
            and self.mock_enabled
        )

    def send(self, message: OutboundMessage) -> ProviderResult:
        if not self.configured:
            if self.mock_allowed:
                logger.info(
                    "[MOCK EMAIL] %s -> %s | %s",
                    message.from_email,
                    message.to_email,
                    message.subject,
                )
                return ProviderResult(
                    accepted=True,
                    provider_message_id="mock",
                )

            return ProviderResult(
                accepted=False,
                error_message=(
                    "SMTP is not configured for this environment"
                ),
            )

        email = EmailMessage()
        email["From"] = formataddr(
            (message.from_name, message.from_email)
        )
        email["To"] = message.to_email
        email["Subject"] = message.subject

        if message.reply_to:
            email["Reply-To"] = message.reply_to

        email.set_content(message.body_text)

        if message.body_html:
            email.add_alternative(
                message.body_html,
                subtype="html",
            )

        if message.attachment:
            maintype, _, subtype = (
                message.attachment.content_type.partition("/")
            )
            email.add_attachment(
                message.attachment.data,
                maintype=maintype or "application",
                subtype=subtype or "octet-stream",
                filename=message.attachment.filename,
            )

        try:
            with smtplib.SMTP(
                self.host,
                self.port,
                timeout=30,
            ) as server:
                if self.use_tls:
                    server.starttls()
                server.login(self.username, self.password)
                server.send_message(email)

            return ProviderResult(accepted=True)

        except Exception as exc:
            logger.exception("SMTP delivery failed")
            return ProviderResult(
                accepted=False,
                error_message=str(exc),
            )
