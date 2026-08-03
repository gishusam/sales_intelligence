"""Pydantic schemas for the Communications API."""

from datetime import datetime
import re
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.communications.templates import validate_template


TemplateType = Literal["cold", "followup", "newsletter"]


class TemplateCreate(BaseModel):
    """Payload for creating a reusable communication template."""

    model_config = ConfigDict(str_strip_whitespace=True)

    slug: str = Field(
        min_length=1,
        max_length=120,
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
    )
    name: str = Field(min_length=1, max_length=160)
    template_type: TemplateType
    subject: str = Field(min_length=1, max_length=255)
    body_text: str = Field(min_length=1)
    body_html: str | None = None
    is_default: bool = False
    is_active: bool = True

    @model_validator(mode="after")
    def validate_content(self) -> "TemplateCreate":
        validate_template(
            template_type=self.template_type,
            subject=self.subject,
            body=self.body_text,
        )
        return self


class TemplateUpdate(BaseModel):
    """Partial update for an existing communication template."""

    model_config = ConfigDict(str_strip_whitespace=True)

    slug: str | None = Field(
        default=None,
        min_length=1,
        max_length=120,
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
    )
    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=160,
    )
    template_type: TemplateType | None = None
    subject: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
    )
    body_text: str | None = Field(
        default=None,
        min_length=1,
    )
    body_html: str | None = None
    is_default: bool | None = None
    is_active: bool | None = None

    @model_validator(mode="after")
    def require_a_change(self) -> "TemplateUpdate":
        if not self.model_fields_set:
            raise ValueError(
                "At least one template field must be supplied"
            )
        return self


class TemplateResponse(BaseModel):
    """Public representation of a reusable template."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    slug: str
    name: str
    template_type: TemplateType
    channel: str
    subject: str
    body_text: str
    body_html: str | None
    required_placeholders: list[str]
    version: int
    is_default: bool
    is_active: bool
    created_by: int | None
    updated_by: int | None
    created_at: datetime | None
    updated_at: datetime | None


_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _normalize_email(value: str) -> str:
    normalized = value.strip().lower()
    if not _EMAIL_PATTERN.fullmatch(normalized):
        raise ValueError("Enter a valid email address")
    return normalized


class SenderIdentityCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    display_name: str = Field(min_length=1, max_length=160)
    email_address: str = Field(min_length=3, max_length=320)
    reply_to_address: str | None = Field(default=None, max_length=320)
    provider: str = Field(
        default="smtp",
        min_length=1,
        max_length=60,
        pattern=r"^[a-z0-9_-]+$",
    )
    provider_reference: str | None = Field(default=None, max_length=255)
    is_default: bool = False
    is_active: bool = True

    @field_validator("email_address")
    @classmethod
    def validate_email_address(cls, value: str) -> str:
        return _normalize_email(value)

    @field_validator("reply_to_address")
    @classmethod
    def validate_reply_to_address(cls, value: str | None) -> str | None:
        return None if value is None else _normalize_email(value)

    @model_validator(mode="after")
    def validate_default_state(self) -> "SenderIdentityCreate":
        if self.is_default and not self.is_active:
            raise ValueError("Default sender must be active")
        return self


class SenderIdentityUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    display_name: str | None = Field(default=None, min_length=1, max_length=160)
    email_address: str | None = Field(default=None, min_length=3, max_length=320)
    reply_to_address: str | None = Field(default=None, max_length=320)
    provider: str | None = Field(
        default=None,
        min_length=1,
        max_length=60,
        pattern=r"^[a-z0-9_-]+$",
    )
    provider_reference: str | None = Field(default=None, max_length=255)
    is_default: bool | None = None
    is_active: bool | None = None

    @field_validator("email_address")
    @classmethod
    def validate_email_address(cls, value: str | None) -> str | None:
        return None if value is None else _normalize_email(value)

    @field_validator("reply_to_address")
    @classmethod
    def validate_reply_to_address(cls, value: str | None) -> str | None:
        return None if value is None else _normalize_email(value)

    @model_validator(mode="after")
    def validate_update(self) -> "SenderIdentityUpdate":
        if not self.model_fields_set:
            raise ValueError("At least one sender field must be supplied")
        if self.is_default is True and self.is_active is False:
            raise ValueError("Default sender must be active")
        return self


class SenderIdentityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    display_name: str
    email_address: str
    reply_to_address: str | None
    provider: str
    provider_reference: str | None
    is_default: bool
    is_active: bool
    created_by: int | None
    updated_by: int | None
    created_at: datetime | None
    updated_at: datetime | None
