from __future__ import annotations

from datetime import datetime, timedelta
from typing import Protocol

from app.models.enums import (
    ActionAttemptOutcome,
    ActionStatus,
    PolicyVerdict,
    RetryClass,
)
from app.models.recovery import ActionAttempt, PlannedAction
from app.services.canonical_hash import canonical_hash
from app.services.ports import IncidentRepository


class ActionExecutionError(RuntimeError):
    pass


class ProviderActionError(RuntimeError):
    """Sanitized provider failure with an explicit retry contract."""

    def __init__(self, *, error_code: str, retryable: bool, retry_after_seconds: int = 30) -> None:
        if not error_code or len(error_code) > 80:
            raise ValueError("provider error_code must be 1-80 characters")
        if retry_after_seconds < 0:
            raise ValueError("retry_after_seconds must not be negative")
        self.error_code = error_code
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"provider action failed ({error_code})")


class ActionProvider(Protocol):
    async def apply(self, action: PlannedAction) -> str: ...

    async def verify(self, action: PlannedAction) -> bool: ...


class ActionExecutor:
    """Claims, applies, and verifies one action without holding a DB transaction open."""

    def __init__(self, repository: IncidentRepository, provider: ActionProvider) -> None:
        self._repository = repository
        self._provider = provider

    async def execute(
        self,
        *,
        action: PlannedAction,
        worker_id: str,
        now: datetime,
        approval_granted: bool = False,
    ) -> PlannedAction | None:
        decision = action.policy_decision
        if decision is None:
            raise ActionExecutionError("action has no deterministic policy decision")
        if decision.verdict == PolicyVerdict.APPROVAL_REQUIRED and not approval_granted:
            return None
        if not await self._prerequisites_verified(action):
            return None

        persisted = await self._repository.get_action(action.action_id)
        if persisted is None:
            raise ActionExecutionError(f"action {action.action_id!r} is not persisted")
        if persisted.execution_status == ActionStatus.VERIFIED:
            return persisted
        if persisted.execution_status == ActionStatus.SUCCEEDED:
            return await self._verify_completed(persisted)

        # A worker may die after the provider committed the effect but before the local
        # receipt was written. Reread the provider before attempting another mutation.
        # This also makes a redelivery safe while the original action lease is present.
        if persisted.execution_status == ActionStatus.LEASED:
            if await self._provider.verify(persisted):
                return await self._recover_provider_success(persisted, now)
            if persisted.lease_expires_at is not None and persisted.lease_expires_at > now:
                return persisted
        elif persisted.execution_status not in {
            ActionStatus.PENDING,
            ActionStatus.FAILED_RETRYABLE,
        }:
            return persisted

        claimed = await self._repository.claim_action(
            action_id=action.action_id,
            worker_id=worker_id,
            lease_expires_at=now + timedelta(minutes=1),
            now=now,
        )
        if claimed is None:
            return await self._repository.get_action(action.action_id)

        # Close the small race between the initial provider reread and the action claim.
        if await self._provider.verify(claimed):
            return await self._recover_provider_success(claimed, now)

        try:
            provider_reference = await self._provider.apply(claimed)
        except ProviderActionError as exc:
            retry_at = now + timedelta(seconds=exc.retry_after_seconds) if exc.retryable else None
            attempt = self._attempt(
                claimed,
                completed_at=now,
                outcome=(
                    ActionAttemptOutcome.FAILED_RETRYABLE
                    if exc.retryable
                    else ActionAttemptOutcome.FAILED_TERMINAL
                ),
                retry_class=RetryClass.RETRYABLE if exc.retryable else RetryClass.TERMINAL,
                error_code=exc.error_code,
            )
            if not await self._repository.fail_action(
                action_id=claimed.action_id,
                worker_id=worker_id,
                retry_after=retry_at,
                attempt=attempt,
            ):
                raise ActionExecutionError("could not record classified provider failure") from exc
            return await self._repository.get_action(claimed.action_id)
        attempt = self._attempt(
            claimed,
            completed_at=now,
            outcome=ActionAttemptOutcome.SUCCEEDED,
            retry_class=RetryClass.NONE,
            provider_reference=provider_reference,
        )
        completed = await self._repository.complete_action_and_create_effect_receipt(
            action_id=claimed.action_id,
            effect_key=claimed.effect_key,
            provider_reference=provider_reference,
            completed_at=now,
            attempt=attempt,
        )
        if not completed:
            raise ActionExecutionError("could not record action completion")

        completed_action = await self._repository.get_action(claimed.action_id)
        if completed_action is None:
            raise ActionExecutionError("completed action disappeared")
        return await self._verify_completed(completed_action)

    async def _recover_provider_success(
        self, action: PlannedAction, completed_at: datetime
    ) -> PlannedAction:
        provider_reference = action.provider_reference or self._provider_reference(action)
        attempt = self._attempt(
            action,
            completed_at=completed_at,
            outcome=ActionAttemptOutcome.RECONCILED,
            retry_class=RetryClass.UNKNOWN,
            provider_reference=provider_reference,
        )
        completed = await self._repository.complete_action_and_create_effect_receipt(
            action_id=action.action_id,
            effect_key=action.effect_key,
            provider_reference=provider_reference,
            completed_at=completed_at,
            attempt=attempt,
        )
        if not completed:
            current = await self._repository.get_action(action.action_id)
            if current is not None and current.execution_status in {
                ActionStatus.SUCCEEDED,
                ActionStatus.VERIFIED,
            }:
                return (
                    current
                    if current.execution_status == ActionStatus.VERIFIED
                    else await self._verify_completed(current)
                )
            raise ActionExecutionError("could not reconcile provider success")
        completed_action = await self._repository.get_action(action.action_id)
        if completed_action is None:
            raise ActionExecutionError("reconciled action disappeared")
        return await self._verify_completed(completed_action)

    async def _verify_completed(self, action: PlannedAction) -> PlannedAction:
        verified = await self._provider.verify(action)
        if not await self._repository.mark_action_verified(
            action_id=action.action_id, verified=verified
        ):
            current = await self._repository.get_action(action.action_id)
            if current is not None and current.execution_status == ActionStatus.VERIFIED:
                return current
            raise ActionExecutionError("could not record action verification")
        result = await self._repository.get_action(action.action_id)
        if result is None:
            raise ActionExecutionError("verified action disappeared")
        return result

    @staticmethod
    def _provider_reference(action: PlannedAction) -> str:
        configured = action.verification_spec.get("resource_id")
        if isinstance(configured, str) and configured:
            return configured
        return f"{action.provider}:{action.target_external_id}"

    @staticmethod
    def _attempt(
        action: PlannedAction,
        *,
        completed_at: datetime,
        outcome: ActionAttemptOutcome,
        retry_class: RetryClass,
        provider_reference: str | None = None,
        error_code: str | None = None,
    ) -> ActionAttempt:
        worker_id = action.lease_owner or "provider-reconciliation"
        attempt_number = max(action.attempt_count, 1)
        attempt_id = (
            f"attempt-{canonical_hash({'action': action.action_id, 'number': attempt_number})[:24]}"
        )
        return ActionAttempt(
            attempt_id=attempt_id,
            incident_id=action.incident_id,
            action_id=action.action_id,
            attempt_number=attempt_number,
            worker_id=worker_id,
            outcome=outcome,
            retry_class=retry_class,
            provider_reference=provider_reference,
            error_code=error_code,
            started_at=action.lease_started_at or completed_at,
            completed_at=completed_at,
        )

    async def _prerequisites_verified(self, action: PlannedAction) -> bool:
        for prerequisite_id in action.prerequisites:
            prerequisite = await self._repository.get_action(prerequisite_id)
            if prerequisite is None or prerequisite.execution_status != ActionStatus.VERIFIED:
                return False
        return True
