"""Schemas for news sources, normalized articles, and AI-assisted drafts."""

from datetime import datetime
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


NewsSourceType = Literal["rss", "api", "manual", "n8n"]
TrustTier = Literal["verified", "review_required", "experimental"]
ArticleStatus = Literal["new", "selected", "used", "rejected"]


class NewsSourceCreate(BaseModel):
    model_config = ConfigDict(
        str_strip_whitespace=True,
        extra="forbid",
    )

    name: str = Field(min_length=1, max_length=180)
    source_type: NewsSourceType
    url: str = Field(min_length=8, max_length=2000)
    publisher: str = Field(min_length=1, max_length=180)
    trust_tier: TrustTier = "review_required"
    is_active: bool = False


class NewsSourceUpdate(BaseModel):
    model_config = ConfigDict(
        str_strip_whitespace=True,
        extra="forbid",
    )

    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=180,
    )
    source_type: NewsSourceType | None = None
    url: str | None = Field(
        default=None,
        min_length=8,
        max_length=2000,
    )
    publisher: str | None = Field(
        default=None,
        min_length=1,
        max_length=180,
    )
    trust_tier: TrustTier | None = None
    is_active: bool | None = None

    @model_validator(mode="after")
    def require_change(self) -> "NewsSourceUpdate":
        if not self.model_fields_set:
            raise ValueError(
                "At least one source field must be supplied"
            )
        return self


class NewsArticleInput(BaseModel):
    model_config = ConfigDict(
        str_strip_whitespace=True,
        extra="forbid",
    )

    external_id: str | None = Field(default=None, max_length=500)
    title: str = Field(min_length=1, max_length=500)
    canonical_url: str = Field(min_length=8, max_length=4000)
    publisher: str = Field(min_length=1, max_length=180)
    published_at: datetime
    summary: str | None = Field(default=None, max_length=10000)
    content_text: str | None = Field(default=None, max_length=100000)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("published_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("published_at must be timezone-aware")
        return value


class NewsIngestionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: int = Field(gt=0)
    articles: list[NewsArticleInput] = Field(
        min_length=1,
        max_length=500,
    )


class NewsDraftGenerationRequest(BaseModel):
    model_config = ConfigDict(
        str_strip_whitespace=True,
        extra="forbid",
    )

    article_ids: list[int] = Field(
        min_length=1,
        max_length=20,
    )
    sender_identity_id: int = Field(gt=0)
    newsletter_name: str = Field(min_length=1, max_length=180)
    subject_hint: str | None = Field(default=None, max_length=255)

    @field_validator("article_ids")
    @classmethod
    def normalize_ids(cls, value: list[int]) -> list[int]:
        if any(item <= 0 for item in value):
            raise ValueError("Article IDs must be positive")
        return list(dict.fromkeys(value))


class NewsArticleStatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ArticleStatus
