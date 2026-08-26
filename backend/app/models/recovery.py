from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import (
    ActionAttemptOutcome,
    ActionCategory,
    ActionStatus,
    ApprovalStatus,
    PlanStatus,
    RetryClass,
)
from app.models.money import Money
from app.models.policy import PolicyDecision


class RecoveryOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1)
    provider_option_id: str = Field(min_length=1)
    option_fingerprint: str = Field(min_length=16)
    incremental_cost: Money
    quote_expires_at: datetime
    provider_snapshot_hash: str = Field(min_length=16)
    arrival_at: datetime
    penalty_minor_units: int = Field(default=0, ge=0)
    reversible: bool = False
    reversible_until: datetime | None = None


class PlannedAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_id: str = Field(min_length=1)
    incident_id: str = Field(min_length=1)
    plan_version: int = Field(ge=1)
    category: ActionCategory
    provider: str = Field(min_length=1)
    target_external_id: str = Field(min_length=1)
    desired_state: dict[str, object]
    prerequisites: list[str] = Field(default_factory=list)
    cost: Money = Field(default_factory=lambda: Money(currency="EUR", minor_units=0))
    reversible: bool = False
    penalty_minor_units: int = Field(default=0, ge=0)
    ambiguous: bool = False
    major_change_reasons: list[str] = Field(default_factory=list)
    verification_spec: dict[str, object] = Field(default_factory=dict)
    policy_decision: PolicyDecision | None = None
    effect_key: str = Field(min_length=16)
    execution_status: ActionStatus = ActionStatus.PENDING
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    provider_reference: str | None = None
    attempt_count: int = Field(default=0, ge=0)
    lease_started_at: datetime | None = None
    retry_after: datetime | None = None


class ActionAttempt(BaseModel):
    """Immutable, sanitized record of one claimed provider attempt."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    attempt_id: str = Field(min_length=16)
    incident_id: str = Field(min_length=1)
    action_id: str = Field(min_length=1)
    attempt_number: int = Field(ge=1)
    worker_id: str = Field(min_length=1)
    outcome: ActionAttemptOutcome
    retry_class: RetryClass
    provider_reference: str | None = None
    error_code: str | None = Field(default=None, min_length=1, max_length=80)
    started_at: datetime
    completed_at: datetime


class RecoveryPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: str = Field(min_length=1)
    incident_id: str = Field(min_length=1)
    version: int = Field(ge=1)
    source_incident_version: int = Field(ge=1)
    policy_version: int = Field(ge=1)
    impact_hash: str = Field(min_length=16)
    selected_option: RecoveryOption
    actions: list[PlannedAction]
    total_incremental_cost: Money
    valid_until: datetime
    plan_hash: str = Field(min_length=16)
    status: PlanStatus = PlanStatus.CURRENT


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approval_id: str = Field(min_length=1)
    incident_id: str = Field(min_length=1)
    plan_version: int = Field(ge=1)
    plan_hash: str = Field(min_length=16)
    policy_version: int = Field(ge=1)
    approved_action_ids: list[str] = Field(default_factory=list)
    maximum_authorized: Money
    option_fingerprint: str = Field(min_length=16)
    expires_at: datetime
    telegram_user_id: str = Field(min_length=1)
    telegram_chat_id: str = Field(min_length=1)
    callback_token_hash: str = Field(min_length=32)
    status: ApprovalStatus = ApprovalStatus.PENDING
    decided_at: datetime | None = None
    consumed_update_id: str | None = None
