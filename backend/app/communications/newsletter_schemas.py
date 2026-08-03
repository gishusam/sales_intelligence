"""Schemas for newsletters and audience snapshots."""

from datetime import datetime
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


NewsletterStatus = Literal[
    "draft",
    "in_review",
    "approved",
    "scheduled",
    "sent",
    "cancelled",
]
BlockKind = Literal["heading", "paragraph", "link", "divider"]


class NewsletterBlock(BaseModel):
    model_config = ConfigDict(
        str_strip_whitespace=True,
        extra="forbid",
    )

    kind: BlockKind
    text: str | None = Field(default=None, max_length=5000)
    url: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def validate_block(self) -> "NewsletterBlock":
        if self.kind in {"heading", "paragraph"} and not self.text:
            raise ValueError(f"{self.kind} block requires text")

        if self.kind == "link" and (not self.text or not self.url):
            raise ValueError("link block requires text and url")

        if self.kind == "divider":
            self.text = None
            self.url = None

        return self


def contains_unsubscribe(
    blocks: list[NewsletterBlock],
) -> bool:
    return any(
        "{unsubscribe_link}" in (block.text or "")
        or "{unsubscribe_link}" in (block.url or "")
        for block in blocks
    )


class NewsletterCreate(BaseModel):
    model_config = ConfigDict(
        str_strip_whitespace=True,
        extra="forbid",
    )

    name: str = Field(min_length=1, max_length=180)
    subject: str = Field(min_length=1, max_length=255)
    preview_text: str | None = Field(default=None, max_length=255)
    sender_identity_id: int = Field(gt=0)
    blocks: list[NewsletterBlock] = Field(
        min_length=1,
        max_length=100,
    )

    @model_validator(mode="after")
    def require_unsubscribe(self) -> "NewsletterCreate":
        if not contains_unsubscribe(self.blocks):
            raise ValueError(
                "Newsletter must include {unsubscribe_link}"
            )
        return self


class NewsletterUpdate(BaseModel):
    model_config = ConfigDict(
        str_strip_whitespace=True,
        extra="forbid",
    )

    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=180,
    )
    subject: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
    )
    preview_text: str | None = Field(default=None, max_length=255)
    sender_identity_id: int | None = Field(default=None, gt=0)
    blocks: list[NewsletterBlock] | None = Field(
        default=None,
        min_length=1,
        max_length=100,
    )

    @model_validator(mode="after")
    def validate_update(self) -> "NewsletterUpdate":
        if not self.model_fields_set:
            raise ValueError(
                "At least one newsletter field must be supplied"
            )

        if self.blocks is not None and not contains_unsubscribe(self.blocks):
            raise ValueError(
                "Newsletter must include {unsubscribe_link}"
            )

        return self


class NewsletterResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    subject: str
    preview_text: str | None
    status: NewsletterStatus
    sender_identity_id: int
    blocks: list[dict]
    body_text: str
    body_html: str
    created_by: int | None
    updated_by: int | None
    reviewed_by: int | None
    approved_by: int | None
    reviewed_at: datetime | None
    approved_at: datetime | None
    created_at: datetime | None
    updated_at: datetime | None


class NewsletterAudienceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lead_ids: list[int] = Field(min_length=1, max_length=1000)

    @field_validator("lead_ids")
    @classmethod
    def positive_ids(cls, value: list[int]) -> list[int]:
        if any(item <= 0 for item in value):
            raise ValueError("Lead IDs must be positive")
        return value


class NewsletterTestSendRequest(BaseModel):
    model_config = ConfigDict(
        str_strip_whitespace=True,
        extra="forbid",
    )

    to_email: str = Field(min_length=3, max_length=320)

    @field_validator("to_email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        normalized = value.strip().lower()

        if (
            "@" not in normalized
            or "." not in normalized.split("@")[-1]
        ):
            raise ValueError("Enter a valid email address")

        return normalized
