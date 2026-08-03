"""Schemas for campaign preparation, scheduling, and controls."""

from datetime import datetime

from pydantic import (
    BaseModel,
    ConfigDict,
    field_validator,
)


class CampaignScheduleRequest(BaseModel):
    """Schedule a prepared campaign without sending it inline."""

    model_config = ConfigDict(extra="forbid")

    scheduled_for: datetime

    @field_validator("scheduled_for")
    @classmethod
    def require_timezone(
        cls,
        value: datetime,
    ) -> datetime:
        if (
            value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise ValueError(
                "scheduled_for must be timezone-aware"
            )
        return value


class CampaignPreparationResult(BaseModel):
    campaign_id: int
    status: str
    step_count: int
    eligible_recipients: int
    suppressed_recipients: int


class CampaignScheduleResult(BaseModel):
    campaign_id: int
    status: str
    scheduled_for: datetime
    queued_messages: int
    existing_messages: int
    newly_suppressed: int


class CampaignStateResult(BaseModel):
    campaign_id: int
    status: str
