"""Schemas for communication preview, send, test-send, and history."""

import re
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalize_email(value: str) -> str:
    value = value.strip().lower()
    if not _EMAIL.fullmatch(value):
        raise ValueError("Enter a valid email address")
    return value


class MessagePreviewRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    lead_id: int = Field(gt=0)
    template_id: int = Field(gt=0)
    sender_identity_id: int = Field(gt=0)
    to_email: str | None = Field(default=None, max_length=320)
    subject_override: str | None = Field(default=None, min_length=1, max_length=255)
    body_override: str | None = Field(default=None, min_length=1)

    @field_validator("to_email")
    @classmethod
    def validate_to_email(cls, value):
        return normalize_email(value) if value is not None else None


class MessageSendRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    lead_id: int | None = Field(default=None, gt=0)
    template_id: int | None = Field(default=None, gt=0)
    sender_identity_id: int = Field(gt=0)
    to_email: str = Field(min_length=3, max_length=320)
    recipient_name: str | None = Field(default=None, max_length=160)
    subject: str = Field(min_length=1, max_length=255)
    body_text: str = Field(min_length=1)
    body_html: str | None = None
    idempotency_key: str = Field(
        min_length=8,
        max_length=180,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]+$",
    )
    follow_up_date: date | None = None
    attachment_name: str | None = Field(default=None, max_length=255)
    attachment_content_type: str | None = Field(default=None, max_length=120)
    attachment_b64: str | None = None

    @field_validator("to_email")
    @classmethod
    def validate_to_email(cls, value):
        return normalize_email(value)

    @model_validator(mode="after")
    def validate_attachment(self):
        if bool(self.attachment_name) != bool(self.attachment_b64):
            raise ValueError(
                "attachment_name and attachment_b64 must be supplied together"
            )
        return self


class MessageSendTestRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    sender_identity_id: int = Field(gt=0)
    to_email: str = Field(min_length=3, max_length=320)
    subject: str = Field(min_length=1, max_length=255)
    body_text: str = Field(min_length=1)
    idempotency_key: str = Field(
        min_length=8,
        max_length=180,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]+$",
    )

    @field_validator("to_email")
    @classmethod
    def validate_to_email(cls, value):
        return normalize_email(value)


class SenderIdentitySummary(BaseModel):
    id: int
    display_name: str
    email_address: str
    reply_to_address: str | None
    provider: str


class MessagePreviewResponse(BaseModel):
    lead_id: int
    template_id: int
    to_email: str
    recipient_name: str
    subject: str
    body_text: str
    body_html: str | None
    sender_identity: SenderIdentitySummary
    smtp_configured: bool


class MessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    lead_id: int | None
    template_id: int | None = None
    sender_identity_id: int
    recipient_email: str
    recipient_name: str | None = None
    subject: str
    body_text: str
    body_html: str | None = None
    message_type: str = "manual"
    status: str
    idempotency_key: str
    provider_message_id: str | None = None
    attachment_name: str | None = None
    attachment_content_type: str | None = None
    attachment_size: int | None = None
    follow_up_date: date | None = None
    error_message: str | None = None
    sent_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
