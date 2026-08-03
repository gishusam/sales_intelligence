"""SMTP provider using environment-managed credentials."""

import logging
import os
import smtplib
from email.message import EmailMessage
from email.utils import formataddr

from app.communications.delivery import OutboundMessage, ProviderResult


logger = logging.getLogger(__name__)


class SMTPEmailProvider:
    def __init__(self):
        self.host = os.getenv("SMTP_HOST", "")
        self.port = int(os.getenv("SMTP_PORT", "587"))
        self.username = os.getenv("SMTP_USER", "")
        self.password = os.getenv("SMTP_PASSWORD", "")
        self.use_tls = os.getenv("SMTP_USE_TLS", "true").lower() not in {
            "0",
            "false",
            "no",
        }

    @property
    def configured(self):
        return bool(self.host and self.username and self.password)

    def send(self, message):
        if not self.configured:
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

        email = EmailMessage()
        email["From"] = formataddr((message.from_name, message.from_email))
        email["To"] = message.to_email
        email["Subject"] = message.subject
        if message.reply_to:
            email["Reply-To"] = message.reply_to
        email.set_content(message.body_text)

        if message.body_html:
            email.add_alternative(message.body_html, subtype="html")

        if message.attachment:
            maintype, _, subtype = message.attachment.content_type.partition("/")
            email.add_attachment(
                message.attachment.data,
                maintype=maintype or "application",
                subtype=subtype or "octet-stream",
                filename=message.attachment.filename,
            )

        try:
            with smtplib.SMTP(self.host, self.port, timeout=30) as server:
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
