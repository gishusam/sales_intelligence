"""Provider-neutral outbound email contracts."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class OutboundAttachment:
    filename: str
    content_type: str
    data: bytes


@dataclass(frozen=True)
class OutboundMessage:
    from_name: str
    from_email: str
    reply_to: str | None
    to_email: str
    subject: str
    body_text: str
    body_html: str | None = None
    attachment: OutboundAttachment | None = None


@dataclass(frozen=True)
class ProviderResult:
    accepted: bool
    provider_message_id: str | None = None
    error_message: str | None = None


class EmailProvider(Protocol):
    def send(self, message: OutboundMessage) -> ProviderResult:
        ...
