"""Pydantic schemas for the Communications API."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.communications.templates import validate_template


class TemplateCreate(BaseModel):
    """Payload for creating a reusable communication template."""

    model_config = ConfigDict(str_strip_whitespace=True)

    slug: str = Field(
        min_length=1,
        max_length=120,
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
    )
    name: str = Field(min_length=1, max_length=160)
    template_type: Literal["cold", "followup", "newsletter"]
    subject: str = Field(min_length=1, max_length=255)
    body_text: str = Field(min_length=1)
    body_html: str | None = None

    @model_validator(mode="after")
    def validate_template_content(self) -> "TemplateCreate":
        validate_template(
            template_type=self.template_type,
            subject=self.subject,
            body=self.body_text,
        )
        return self
