"""Normalized provider-event and overview schemas."""

from datetime import datetime
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)


ProviderEventType = Literal[
    "delivered",
    "hard_bounce",
    "soft_bounce",
    "complaint",
    "unsubscribe",
    "opened",
    "clicked",
]


class ProviderEventPayload(BaseModel):
    model_config = ConfigDict(
        str_strip_whitespace=True,
        extra="forbid",
    )

    provider_event_id: str = Field(min_length=1, max_length=255)
    provider_message_id: str = Field(min_length=1, max_length=500)
    event_type: ProviderEventType
    recipient_email: str | None = Field(default=None, max_length=320)
    occurred_at: datetime
    bounce_type: Literal["hard", "soft"] | None = None
    reason: str | None = Field(default=None, max_length=2000)
    url: str | None = Field(default=None, max_length=4000)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("occurred_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")
        return value

    @field_validator("recipient_email")
    @classmethod
    def normalize_email(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip().lower()


class ProviderEventResult(BaseModel):
    event_id: int
    message_id: int | None
    status: str
    duplicate: bool


class CommunicationsOverviewResponse(BaseModel):
    total_messages: int
    queued_messages: int
    processing_messages: int
    sent_messages: int
    delivered_messages: int
    failed_messages: int
    dead_letter_messages: int
    bounced_messages: int
    complained_messages: int
    unsubscribed_messages: int
    opens: int
    clicks: int
    active_campaigns: int
    active_newsletters: int
    suppressed_contacts: int
