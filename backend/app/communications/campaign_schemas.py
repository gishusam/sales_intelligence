"""Request and response schemas for draft communication campaigns."""

from datetime import datetime
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


CampaignType = Literal["cold", "followup", "newsletter"]
CampaignStatus = Literal[
    "draft",
    "ready",
    "scheduled",
    "running",
    "active",
    "paused",
    "completed",
    "cancelled",
    "failed",
]
RecipientStatus = Literal[
    "pending",
    "enrolled",
    "queued",
    "sent",
    "delivered",
    "failed",
    "suppressed",
    "cancelled",
    "bounced",
    "complained",
    "unsubscribed",
    "replied",
    "skipped",
]


class CampaignCreate(BaseModel):
    """Create a campaign in draft state."""

    model_config = ConfigDict(
        str_strip_whitespace=True,
        extra="forbid",
    )

    name: str = Field(min_length=1, max_length=180)
    description: str | None = Field(
        default=None,
        max_length=2000,
    )
    campaign_type: CampaignType
    sender_identity_id: int = Field(gt=0)


class CampaignUpdate(BaseModel):
    """Modify draft campaign metadata."""

    model_config = ConfigDict(
        str_strip_whitespace=True,
        extra="forbid",
    )

    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=180,
    )
    description: str | None = Field(
        default=None,
        max_length=2000,
    )
    sender_identity_id: int | None = Field(
        default=None,
        gt=0,
    )

    @model_validator(mode="after")
    def require_change(self) -> "CampaignUpdate":
        if not self.model_fields_set:
            raise ValueError(
                "At least one campaign field must be supplied"
            )
        return self


class CampaignResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None
    campaign_type: CampaignType
    sender_identity_id: int
    status: CampaignStatus
    created_by: int | None
    updated_by: int | None
    created_at: datetime | None
    updated_at: datetime | None


class CampaignStepCreate(BaseModel):
    """Add one ordered step to a draft campaign."""

    model_config = ConfigDict(
        str_strip_whitespace=True,
        extra="forbid",
    )

    step_order: int = Field(gt=0, le=100)
    delay_days: int = Field(ge=0, le=365)
    template_id: int = Field(gt=0)
    subject_override: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
    )
    body_text_override: str | None = Field(
        default=None,
        min_length=1,
    )


class CampaignStepResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    campaign_id: int
    step_order: int
    delay_days: int
    template_id: int
    subject_override: str | None
    body_text_override: str | None
    created_at: datetime | None
    updated_at: datetime | None


class CampaignEnrollmentRequest(BaseModel):
    """Enroll a bounded set of leads into a draft campaign."""

    model_config = ConfigDict(extra="forbid")

    lead_ids: list[int] = Field(
        min_length=1,
        max_length=500,
    )

    @field_validator("lead_ids")
    @classmethod
    def require_positive_ids(
        cls,
        value: list[int],
    ) -> list[int]:
        if any(lead_id <= 0 for lead_id in value):
            raise ValueError("Lead IDs must be positive")
        return value


class CampaignEnrollmentResult(BaseModel):
    requested: int
    unique_requested: int
    enrolled: int
    suppressed: int
    duplicates: int
    missing_email: int
    missing_leads: int
    results: list[dict]


class CampaignRecipientResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    campaign_id: int
    lead_id: int | None
    recipient_email: str
    recipient_name: str | None
    status: RecipientStatus
    suppression_reason: str | None
    enrolled_by: int | None
    enrolled_at: datetime | None
    created_at: datetime | None
    updated_at: datetime | None
