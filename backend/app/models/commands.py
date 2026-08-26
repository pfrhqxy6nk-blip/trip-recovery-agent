from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import OutboxStatus, WorkflowCommandStatus, WorkflowCommandType


class WorkflowCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    command_id: str = Field(min_length=16)
    type: WorkflowCommandType
    incident_id: str = Field(min_length=1)
    plan_version: int | None = Field(default=None, ge=1)
    created_at: datetime
    correlation_id: str = Field(min_length=1)
    payload: dict[str, str] = Field(default_factory=dict)
    not_before: datetime | None = None


class WorkflowCommandState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: WorkflowCommand
    payload_hash: str = Field(min_length=64, max_length=64)
    status: WorkflowCommandStatus
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    completed_at: datetime | None = None


class OutboxRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outbox_id: str = Field(min_length=16)
    command: WorkflowCommand
    status: OutboxStatus = OutboxStatus.PENDING
    created_at: datetime
    published_at: datetime | None = None
