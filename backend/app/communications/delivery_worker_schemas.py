"""Schemas for bounded delivery-worker runs."""

from pydantic import BaseModel, ConfigDict, Field


class WorkerRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    batch_size: int = Field(default=25, ge=1, le=500)


class WorkerRunResult(BaseModel):
    worker_id: str
    recovered: int
    claimed: int
    sent: int
    retried: int
    dead_lettered: int
    suppressed: int
    cancelled: int
    deferred: int
    errors: int
