"""Schemas for conservative follow-up automation."""

from datetime import datetime
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)


TriggerType = Literal["inactivity_followup"]


class AutomationRuleCreate(BaseModel):
    """Create an inactive-by-default follow-up rule."""

    model_config = ConfigDict(
        str_strip_whitespace=True,
        extra="forbid",
    )

    name: str = Field(min_length=1, max_length=180)
    trigger_type: TriggerType = "inactivity_followup"
    template_id: int = Field(gt=0)
    sender_identity_id: int = Field(gt=0)
    inactivity_days: int = Field(ge=1, le=365)
    max_drafts_per_lead: int = Field(default=1, ge=1, le=20)
    is_active: bool = False


class AutomationRuleUpdate(BaseModel):
    model_config = ConfigDict(
        str_strip_whitespace=True,
        extra="forbid",
    )

    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=180,
    )
    template_id: int | None = Field(default=None, gt=0)
    sender_identity_id: int | None = Field(default=None, gt=0)
    inactivity_days: int | None = Field(
        default=None,
        ge=1,
        le=365,
    )
    max_drafts_per_lead: int | None = Field(
        default=None,
        ge=1,
        le=20,
    )
    is_active: bool | None = None

    @model_validator(mode="after")
    def require_change(self) -> "AutomationRuleUpdate":
        if not self.model_fields_set:
            raise ValueError(
                "At least one automation rule field must be supplied"
            )
        return self


class AutomationRuleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    trigger_type: TriggerType
    template_id: int
    sender_identity_id: int
    inactivity_days: int
    max_drafts_per_lead: int
    is_active: bool
    created_by: int | None
    updated_by: int | None
    created_at: datetime | None
    updated_at: datetime | None


class LeadAutomationStateUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reply_received: bool | None = None
    automation_paused: bool | None = None
    pause_reason: str | None = Field(
        default=None,
        max_length=500,
    )

    @model_validator(mode="after")
    def require_change(self) -> "LeadAutomationStateUpdate":
        if not self.model_fields_set:
            raise ValueError(
                "At least one lead automation field must be supplied"
            )
        return self


class LeadAutomationStateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    lead_id: int
    reply_received_at: datetime | None
    automation_paused: bool
    pause_reason: str | None
    updated_by: int | None
    updated_at: datetime | None


class AutomationRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_id: int | None = Field(default=None, gt=0)
    batch_size: int = Field(default=100, ge=1, le=500)


class AutomationRuleRunResult(BaseModel):
    rule_id: int
    evaluated: int
    drafted: int
    skipped: int
    existing: int


class AutomationRunResult(BaseModel):
    rules_evaluated: int
    drafted: int
    skipped: int
    existing: int
    results: list[AutomationRuleRunResult]


class AutomationExecutionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    rule_id: int
    lead_id: int
    email_message_id: int | None
    idempotency_key: str
    status: str
    reason: str | None
    executed_at: datetime | None
    created_at: datetime | None
