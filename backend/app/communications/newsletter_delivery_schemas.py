"""Newsletter scheduling and lifecycle schemas."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator


class NewsletterScheduleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scheduled_for: datetime

    @field_validator("scheduled_for")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(
                "scheduled_for must be timezone-aware"
            )
        return value
