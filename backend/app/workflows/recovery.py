from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from app.models.commands import OutboxRecord, WorkflowCommand
from app.models.domain import Incident, Trip
from app.models.enums import ActionStatus, ApprovalStatus, IncidentStatus, WorkflowCommandType
from app.models.policy import AutonomyPolicy
from app.models.recovery import ApprovalRequest, PlannedAction, RecoveryOption, RecoveryPlan
from app.providers.demo import PersistentDemoProvider
from app.services.action_executor import ActionExecutor, ActionProvider
from app.services.approval_tokens import (
    ApprovalTokenManager,
    callback_token_hash,
    new_callback_token,
)
from app.services.canonical_hash import canonical_hash
from app.services.onboarding import OnboardingError, TelegramOnboardingService
from app.services.ports import IncidentRepository
from app.services.recovery_planner import CanonicalRecoveryPlanner
from app.services.verifier import can_mark_recovered


class RecoveryWorkflowError(RuntimeError):
    pass


class RecoveryQuoteProvider(Protocol):
    async def search_recovery_option(
        self, *, trip: Trip, incident: Incident, now: datetime
    ) -> RecoveryOption: ...


@dataclass(frozen=True)
class RecoveryStartResult:
    plan: RecoveryPlan
    approval: ApprovalRequest | None
    incident_status: IncidentStatus
    approval_callback_token: str | None = None


@dataclass(frozen=True)
class RecoveryPreparedResult:
    plan: RecoveryPlan
    incident_status: IncidentStatus


class RecoveryWorkflow:
    """Bounded, persistent recovery orchestration for the canonical local demo."""

    def __init__(
        self,
        repository: IncidentRepository,
        planner: CanonicalRecoveryPlanner | None = None,
        provider: ActionProvider | None = None,
        provider_factory: Callable[[str], ActionProvider] | None = None,
        quote_provider: RecoveryQuoteProvider | None = None,
        approval_tokens: ApprovalTokenManager | None = None,
    ) -> None:
        self._repository = repository
        self._planner = planner or CanonicalRecoveryPlanner()
        self._provider = provider or PersistentDemoProvider(repository)
        self._provider_factory = provider_factory
        self._quote_provider = quote_provider
        self._executor = ActionExecutor(repository, self._provider)
        self._approval_tokens = approval_tokens

    def _executor_for(self, telegram_user_id: str | None = None) -> ActionExecutor:
        if self._provider_factory is not None and telegram_user_id is not None:
            return ActionExecutor(self._repository, self._provider_factory(telegram_user_id))
        return self._executor

    async def start(
        self,
        *,
        incident_id: str,
        policy: AutonomyPolicy,
        telegram_user_id: str,
        telegram_chat_id: str,
        now: datetime,
    ) -> RecoveryStartResult:
        prepared = await self.prepare(incident_id=incident_id, policy=policy, now=now)
        return await self.continue_plan(
            plan=prepared.plan,
            policy=policy,
            telegram_user_id=telegram_user_id,
            telegram_chat_id=telegram_chat_id,
            now=now,
            notification_delivered=True,
        )

    async def prepare(
        self,
        *,
        incident_id: str,
        policy: AutonomyPolicy,
        now: datetime,
    ) -> RecoveryPreparedResult:
        """Persist a plan and stop at NOTIFYING before any provider effect."""

        incident = await self._require_incident(incident_id)
        trip = await self._repository.get_trip(incident.trip_id)
        if trip is None:
            raise RecoveryWorkflowError("recovery trip does not exist")
        previous_plan = await self._repository.get_current_plan(incident_id)
        live_option = None
        if self._quote_provider is not None and not incident.external_event_id.startswith(
            "telegram-demo:"
        ):
            try:
                live_option = await self._quote_provider.search_recovery_option(
                    trip=trip, incident=incident, now=now
                )
            except Exception as exc:
                # Quote providers are optional. Never silently turn an unavailable
                # live search into a fake confirmed flight.
                raise RecoveryWorkflowError("live recovery quote is unavailable") from exc
        plan = self._planner.create_plan(
            incident=incident,
            policy=policy,
            now=now,
            version=1 if previous_plan is None else previous_plan.version + 1,
            trip=trip,
            live_option=live_option,
        )
        if not await self._repository.commit_plan(
            plan=plan, expected_incident_version=incident.version
        ):
            raise RecoveryWorkflowError("could not commit current recovery plan")
        for action in plan.actions:
            if not await self._repository.put_action(action):
                raise RecoveryWorkflowError(f"could not persist action {action.action_id}")
        current = await self._transition(
            incident_id,
            {IncidentStatus.PLANNING},
            IncidentStatus.NOTIFYING,
            now,
        )
        return RecoveryPreparedResult(plan=plan, incident_status=current.status)

    async def continue_plan(
        self,
        *,
        plan: RecoveryPlan,
        policy: AutonomyPolicy,
        telegram_user_id: str,
        telegram_chat_id: str,
        now: datetime,
        notification_delivered: bool = False,
    ) -> RecoveryStartResult:
        """Resume a committed plan from its durable incident/action state."""

        incident = await self._require_incident(plan.incident_id)
        if incident.status == IncidentStatus.NOTIFYING:
            if not notification_delivered:
                return RecoveryStartResult(
                    plan=plan,
                    approval=None,
                    incident_status=IncidentStatus.NOTIFYING,
                )
            incident = await self._transition(
                plan.incident_id,
                {IncidentStatus.NOTIFYING},
                IncidentStatus.EXECUTING_AUTO,
                now,
            )
        if incident.status == IncidentStatus.EXECUTING_AUTO:
            await self._execute_actions(
                plan.actions,
                worker_id="recovery-auto",
                now=now,
                approved_action_ids=frozenset(),
                executor=self._executor_for(telegram_user_id),
            )
            barrier = await self._execution_barrier(
                incident_id=plan.incident_id,
                phase=IncidentStatus.EXECUTING_AUTO,
                approval_id=None,
                now=now,
            )
            if barrier != IncidentStatus.EXECUTING_AUTO:
                return RecoveryStartResult(
                    plan=plan,
                    approval=None,
                    incident_status=barrier,
                )

        approval_actions = [
            action.action_id
            for action in plan.actions
            if action.policy_decision is not None
            and action.policy_decision.verdict.value == "APPROVAL_REQUIRED"
        ]
        approval: ApprovalRequest | None = None
        if approval_actions:
            approval, generated_callback_token = self._approval_for(
                plan=plan,
                policy=policy,
                telegram_user_id=telegram_user_id,
                telegram_chat_id=telegram_chat_id,
                now=now,
                action_ids=approval_actions,
            )
            callback_token: str | None = generated_callback_token
            stored_new = await self._repository.store_approval(approval)
            if not stored_new:
                existing = await self._repository.get_approval(approval.approval_id)
                if existing is None:
                    raise RecoveryWorkflowError("could not persist approval request")
                approval = existing
                callback_token = self._callback_token_for(existing)
            await self._enqueue_approval_expiry(approval, now)
            current = await self._require_incident(plan.incident_id)
            if current.status == IncidentStatus.EXECUTING_AUTO:
                current = await self._transition(
                    plan.incident_id,
                    {IncidentStatus.EXECUTING_AUTO},
                    IncidentStatus.WAITING_APPROVAL,
                    now,
                )
            if current.status == IncidentStatus.WAITING_APPROVAL:
                stored = await self._repository.get_approval(approval.approval_id)
                return RecoveryStartResult(
                    plan=plan,
                    approval=stored or approval,
                    incident_status=current.status,
                    approval_callback_token=callback_token,
                )
            if current.status in {IncidentStatus.EXECUTING_APPROVED, IncidentStatus.VERIFYING}:
                status = await self.resume_after_approval(approval_id=approval.approval_id, now=now)
                return RecoveryStartResult(
                    plan=plan,
                    approval=approval,
                    incident_status=status,
                    approval_callback_token=callback_token,
                )
            return RecoveryStartResult(
                plan=plan,
                approval=approval,
                incident_status=current.status,
                approval_callback_token=callback_token,
            )

        current = await self._require_incident(plan.incident_id)
        if current.status == IncidentStatus.EXECUTING_AUTO:
            current = await self._transition(
                plan.incident_id,
                {IncidentStatus.EXECUTING_AUTO},
                IncidentStatus.VERIFYING,
                now,
            )
        if current.status == IncidentStatus.VERIFYING:
            await self._finish_if_verified(plan.incident_id, current, None, now)
        completed = await self._require_incident(plan.incident_id)
        return RecoveryStartResult(plan=plan, approval=None, incident_status=completed.status)

    async def approve(
        self,
        *,
        approval_id: str,
        callback_token_hash: str,
        telegram_user_id: str,
        telegram_chat_id: str,
        update_id: str,
        now: datetime,
        process_inline: bool = True,
    ) -> IncidentStatus:
        approval = await self._repository.get_approval(approval_id)
        if approval is None:
            raise RecoveryWorkflowError("approval request does not exist")
        outbox = OutboxRecord(
            outbox_id=canonical_hash({"approval_id": approval_id, "type": "resume"}),
            command=WorkflowCommand(
                command_id=canonical_hash(
                    {"approval_id": approval_id, "type": "resume_after_approval"}
                ),
                type=WorkflowCommandType.RESUME_AFTER_APPROVAL,
                incident_id=approval.incident_id,
                plan_version=approval.plan_version,
                created_at=now,
                correlation_id=f"approval:{approval_id}",
                payload={"approval_id": approval_id},
            ),
            created_at=now,
        )
        consumed = await self._repository.consume_approval(
            approval_id=approval_id,
            callback_token_hash=callback_token_hash,
            telegram_user_id=telegram_user_id,
            telegram_chat_id=telegram_chat_id,
            update_id=update_id,
            now=now,
            outbox=outbox,
        )
        if not consumed:
            current_approval = await self._repository.get_approval(approval_id)
            bindings_match = (
                current_approval is not None
                and current_approval.callback_token_hash == callback_token_hash
                and current_approval.telegram_user_id == telegram_user_id
                and current_approval.telegram_chat_id == telegram_chat_id
            )
            if (
                not bindings_match
                or current_approval is None
                or current_approval.status != ApprovalStatus.APPROVED
            ):
                current = await self._require_incident(approval.incident_id)
                return current.status
        stored_outbox = await self._repository.get_outbox(outbox.outbox_id)
        if stored_outbox is None:
            raise RecoveryWorkflowError("approval continuation outbox is missing")
        if not process_inline:
            return (await self._require_incident(approval.incident_id)).status
        return await self.process_command(
            command=stored_outbox.command,
            worker_id="approval-inline",
            now=now,
        )

    async def process_command(
        self,
        *,
        command: WorkflowCommand,
        worker_id: str,
        now: datetime,
    ) -> IncidentStatus:
        """Claim and execute a persisted workflow command exactly once."""

        claimed = await self._repository.claim_workflow_command(
            command=command,
            worker_id=worker_id,
            lease_expires_at=now + timedelta(minutes=1),
            now=now,
        )
        if not claimed:
            return (await self._require_incident(command.incident_id)).status
        incident = await self._require_incident(command.incident_id)
        plan = await self._repository.get_current_plan(command.incident_id)
        if command.plan_version is not None and (
            plan is None or plan.version != command.plan_version
        ):
            if not await self._repository.complete_workflow_command(
                command_id=command.command_id,
                worker_id=worker_id,
                completed_at=now,
            ):
                raise RecoveryWorkflowError("could not complete stale workflow command")
            return incident.status
        status: IncidentStatus
        if command.type == WorkflowCommandType.START_RECOVERY:
            if incident.status != IncidentStatus.PLANNING:
                status = incident.status
            else:
                policy, _, _ = await self._traveler_policy_context(incident.incident_id)
                prepared = await self.prepare(
                    incident_id=incident.incident_id,
                    policy=policy,
                    now=now,
                )
                status = prepared.incident_status
        elif command.type == WorkflowCommandType.CONTINUE_WORKFLOW:
            plan, policy, telegram_user_id, telegram_chat_id = await self._command_context(
                incident.incident_id
            )
            result = await self.continue_plan(
                plan=plan,
                policy=policy,
                telegram_user_id=telegram_user_id,
                telegram_chat_id=telegram_chat_id,
                now=now,
                notification_delivered=(command.payload.get("notification_delivered") == "true"),
            )
            status = result.incident_status
        elif command.type == WorkflowCommandType.RESUME_AFTER_APPROVAL:
            approval_id = command.payload.get("approval_id")
            if approval_id is None:
                raise RecoveryWorkflowError("resume command has no approval binding")
            status = await self.resume_after_approval(approval_id=approval_id, now=now)
        elif command.type == WorkflowCommandType.RETRY_ACTION:
            phase = command.payload.get("phase")
            if incident.status != IncidentStatus.RETRY_SCHEDULED:
                status = incident.status
            elif phase == IncidentStatus.EXECUTING_APPROVED.value:
                approval_id = command.payload.get("approval_id")
                if approval_id is None:
                    raise RecoveryWorkflowError("approved retry has no approval binding")
                await self._transition(
                    incident.incident_id,
                    {IncidentStatus.RETRY_SCHEDULED},
                    IncidentStatus.EXECUTING_APPROVED,
                    now,
                )
                status = await self.resume_after_approval(approval_id=approval_id, now=now)
            elif phase == IncidentStatus.EXECUTING_AUTO.value:
                plan, policy, telegram_user_id, telegram_chat_id = await self._command_context(
                    incident.incident_id
                )
                await self._transition(
                    incident.incident_id,
                    {IncidentStatus.RETRY_SCHEDULED},
                    IncidentStatus.EXECUTING_AUTO,
                    now,
                )
                result = await self.continue_plan(
                    plan=plan,
                    policy=policy,
                    telegram_user_id=telegram_user_id,
                    telegram_chat_id=telegram_chat_id,
                    now=now,
                    notification_delivered=True,
                )
                status = result.incident_status
            else:
                raise RecoveryWorkflowError("retry command has no valid phase")
        elif command.type == WorkflowCommandType.EXPIRE_APPROVAL:
            approval_id = command.payload.get("approval_id")
            if approval_id is None:
                raise RecoveryWorkflowError("expiry command has no approval binding")
            replan_command = WorkflowCommand(
                command_id=canonical_hash(
                    {"approval_id": approval_id, "type": "replan_after_expiry"}
                ),
                type=WorkflowCommandType.REPLAN,
                incident_id=command.incident_id,
                created_at=now,
                correlation_id=command.correlation_id,
                payload={"expired_approval_id": approval_id},
            )
            replan_outbox = OutboxRecord(
                outbox_id=canonical_hash({"command_id": replan_command.command_id}),
                command=replan_command,
                created_at=now,
            )
            expired = await self._repository.expire_approval_and_enqueue_replan(
                approval_id=approval_id,
                now=now,
                outbox=replan_outbox,
            )
            status = (
                IncidentStatus.PLANNING
                if expired
                else (await self._require_incident(command.incident_id)).status
            )
        elif command.type == WorkflowCommandType.REPLAN:
            if incident.status != IncidentStatus.PLANNING:
                status = incident.status
            else:
                _, policy, _, _ = await self._command_context(incident.incident_id)
                prepared = await self.prepare(
                    incident_id=incident.incident_id,
                    policy=policy,
                    now=now,
                )
                status = prepared.incident_status
        elif incident.status in {
            IncidentStatus.RECOVERED,
            IncidentStatus.NEEDS_ATTENTION,
            IncidentStatus.CANCELLED,
        }:
            status = incident.status
        else:
            raise RecoveryWorkflowError(f"workflow command {command.type.value} is not implemented")
        if not await self._repository.complete_workflow_command(
            command_id=command.command_id,
            worker_id=worker_id,
            completed_at=now,
        ):
            raise RecoveryWorkflowError("could not complete workflow command")
        return status

    async def resume_after_approval(self, *, approval_id: str, now: datetime) -> IncidentStatus:
        """Continue an already consumed approval after any process boundary."""

        approval = await self._repository.get_approval(approval_id)
        if approval is None or approval.status != ApprovalStatus.APPROVED:
            raise RecoveryWorkflowError("approval is not consumable for resume")
        incident = await self._require_incident(approval.incident_id)
        if incident.status in {
            IncidentStatus.RECOVERED,
            IncidentStatus.NEEDS_ATTENTION,
            IncidentStatus.CANCELLED,
        }:
            return incident.status
        if incident.status == IncidentStatus.WAITING_APPROVAL:
            incident = await self._transition(
                approval.incident_id,
                {IncidentStatus.WAITING_APPROVAL},
                IncidentStatus.EXECUTING_APPROVED,
                now,
            )

        if incident.status == IncidentStatus.EXECUTING_APPROVED:
            actions = await self._repository.list_actions(approval.incident_id)
            await self._execute_actions(
                actions,
                worker_id="recovery-approved",
                now=now,
                approved_action_ids=frozenset(approval.approved_action_ids),
                executor=self._executor_for(approval.telegram_user_id),
            )
            barrier = await self._execution_barrier(
                incident_id=approval.incident_id,
                phase=IncidentStatus.EXECUTING_APPROVED,
                approval_id=approval.approval_id,
                now=now,
            )
            if barrier != IncidentStatus.EXECUTING_APPROVED:
                return barrier
            incident = await self._transition(
                approval.incident_id,
                {IncidentStatus.EXECUTING_APPROVED},
                IncidentStatus.VERIFYING,
                now,
            )
        if incident.status == IncidentStatus.VERIFYING:
            await self._finish_if_verified(approval.incident_id, incident, approval, now)
        return (await self._require_incident(approval.incident_id)).status

    async def decline(
        self,
        *,
        approval_id: str,
        callback_token_hash: str,
        telegram_user_id: str,
        telegram_chat_id: str,
        update_id: str,
        now: datetime,
    ) -> IncidentStatus:
        approval = await self._repository.get_approval(approval_id)
        if approval is None:
            raise RecoveryWorkflowError("approval request does not exist")
        declined = await self._repository.decline_approval(
            approval_id=approval_id,
            callback_token_hash=callback_token_hash,
            telegram_user_id=telegram_user_id,
            telegram_chat_id=telegram_chat_id,
            update_id=update_id,
            now=now,
        )
        if not declined:
            return (await self._require_incident(approval.incident_id)).status
        return IncidentStatus.CANCELLED

    async def request_replan(
        self,
        *,
        approval_id: str,
        callback_token_hash: str,
        telegram_user_id: str,
        telegram_chat_id: str,
        update_id: str,
        now: datetime,
        resume_cancelled: bool = False,
    ) -> IncidentStatus:
        approval = await self._repository.get_approval(approval_id)
        if approval is None:
            raise RecoveryWorkflowError("approval request does not exist")
        request_kind = "resume" if resume_cancelled else "find_another"
        outbox = OutboxRecord(
            outbox_id=canonical_hash(
                {"approval_id": approval_id, "type": "replan", "kind": request_kind}
            ),
            command=WorkflowCommand(
                command_id=canonical_hash(
                    {
                        "approval_id": approval_id,
                        "type": "replan",
                        "kind": request_kind,
                    }
                ),
                type=WorkflowCommandType.REPLAN,
                incident_id=approval.incident_id,
                plan_version=approval.plan_version,
                created_at=now,
                correlation_id=f"approval:{approval_id}:{request_kind}",
                payload={"approval_id": approval_id, "reason": request_kind},
            ),
            created_at=now,
        )
        requested = await self._repository.request_approval_replan(
            approval_id=approval_id,
            callback_token_hash=callback_token_hash,
            telegram_user_id=telegram_user_id,
            telegram_chat_id=telegram_chat_id,
            update_id=update_id,
            now=now,
            outbox=outbox,
            resume_cancelled=resume_cancelled,
        )
        if not requested:
            return (await self._require_incident(approval.incident_id)).status
        return IncidentStatus.PLANNING

    async def _execute_actions(
        self,
        actions: list[PlannedAction],
        *,
        worker_id: str,
        now: datetime,
        approved_action_ids: frozenset[str],
        executor: ActionExecutor | None = None,
    ) -> None:
        # Multiple passes make dependency ordering independent from Firestore query order.
        remaining = {action.action_id: action for action in actions}
        while remaining:
            progressed = False
            for action_id, action in list(remaining.items()):
                result = await (executor or self._executor).execute(
                    action=action,
                    worker_id=worker_id,
                    now=now,
                    approval_granted=action_id in approved_action_ids,
                )
                if result is not None and result.execution_status == ActionStatus.VERIFIED:
                    remaining.pop(action_id)
                    progressed = True
                elif result is None and action_id not in approved_action_ids:
                    remaining.pop(action_id)
            if not progressed:
                return

    async def _execution_barrier(
        self,
        *,
        incident_id: str,
        phase: IncidentStatus,
        approval_id: str | None,
        now: datetime,
    ) -> IncidentStatus:
        actions = await self._repository.list_actions(incident_id)
        if any(
            action.execution_status
            in {ActionStatus.FAILED_TERMINAL, ActionStatus.VERIFICATION_FAILED}
            for action in actions
        ):
            return (
                await self._transition(incident_id, {phase}, IncidentStatus.NEEDS_ATTENTION, now)
            ).status
        retryable = [
            action for action in actions if action.execution_status == ActionStatus.FAILED_RETRYABLE
        ]
        if not retryable:
            return phase
        retry_at = min(action.retry_after or now for action in retryable)
        incident = await self._transition(incident_id, {phase}, IncidentStatus.RETRY_SCHEDULED, now)
        attempt = max(action.attempt_count for action in retryable)
        payload = {"phase": phase.value}
        if approval_id is not None:
            payload["approval_id"] = approval_id
        plan = await self._repository.get_current_plan(incident_id)
        if plan is None:
            raise RecoveryWorkflowError("retry command has no current plan")
        command = WorkflowCommand(
            command_id=canonical_hash(
                {
                    "incident_id": incident_id,
                    "type": "retry_action",
                    "phase": phase.value,
                    "attempt": attempt,
                }
            ),
            type=WorkflowCommandType.RETRY_ACTION,
            incident_id=incident_id,
            plan_version=plan.version,
            created_at=now,
            correlation_id=incident.correlation_id,
            payload=payload,
            not_before=retry_at,
        )
        outbox = OutboxRecord(
            outbox_id=canonical_hash({"command_id": command.command_id}),
            command=command,
            created_at=now,
        )
        if not await self._repository.enqueue_outbox_once(outbox):
            raise RecoveryWorkflowError("could not enqueue retry command")
        return IncidentStatus.RETRY_SCHEDULED

    async def _enqueue_approval_expiry(self, approval: ApprovalRequest, now: datetime) -> None:
        command = WorkflowCommand(
            command_id=canonical_hash(
                {"approval_id": approval.approval_id, "type": "expire_approval"}
            ),
            type=WorkflowCommandType.EXPIRE_APPROVAL,
            incident_id=approval.incident_id,
            plan_version=approval.plan_version,
            created_at=now,
            correlation_id=f"approval:{approval.approval_id}",
            payload={"approval_id": approval.approval_id},
            not_before=approval.expires_at,
        )
        outbox = OutboxRecord(
            outbox_id=canonical_hash({"command_id": command.command_id}),
            command=command,
            created_at=now,
        )
        if not await self._repository.enqueue_outbox_once(outbox):
            raise RecoveryWorkflowError("could not enqueue approval expiry")

    async def _command_context(
        self, incident_id: str
    ) -> tuple[RecoveryPlan, AutonomyPolicy, str, str]:
        plan = await self._repository.get_current_plan(incident_id)
        if plan is None:
            raise RecoveryWorkflowError("workflow command has no current plan")
        policy, telegram_user_id, telegram_chat_id = await self._traveler_policy_context(
            incident_id
        )
        return plan, policy, telegram_user_id, telegram_chat_id

    async def _traveler_policy_context(self, incident_id: str) -> tuple[AutonomyPolicy, str, str]:
        incident = await self._require_incident(incident_id)
        trip = await self._repository.get_trip(incident.trip_id)
        if trip is None or trip.owner_user_id is None:
            raise RecoveryWorkflowError("workflow command has no traveler context")
        prefix = "telegram:"
        if not trip.owner_user_id.startswith(prefix):
            raise RecoveryWorkflowError("workflow command owner is not a Telegram traveler")
        telegram_user_id = trip.owner_user_id.removeprefix(prefix)
        traveler = await self._repository.get_traveler(telegram_user_id)
        if traveler is None:
            raise RecoveryWorkflowError("workflow command traveler does not exist")
        if traveler.active_policy_version is None:
            raise RecoveryWorkflowError("workflow command policy is unavailable")
        policy = await self._repository.get_traveler_policy(
            user_id=traveler.user_id,
            version=traveler.active_policy_version,
        )
        if policy is None:
            # Compatibility for schema-v1 pilot profiles created before immutable
            # policy documents existed. New activations always use the atomic path.
            try:
                policy = TelegramOnboardingService.policy(traveler)
            except OnboardingError as exc:
                raise RecoveryWorkflowError("workflow command policy is unavailable") from exc
        return policy, traveler.telegram_user_id, traveler.telegram_chat_id

    async def _finish_if_verified(
        self,
        incident_id: str,
        incident: Incident,
        approval: ApprovalRequest | None,
        now: datetime,
    ) -> None:
        actions = await self._repository.list_actions(incident_id)
        current_approval = None
        if approval is not None:
            current_approval = await self._repository.get_approval(approval.approval_id)
        current_plan_version = max((action.plan_version for action in actions), default=None)
        if can_mark_recovered(
            actions,
            current_approval,
            current_plan_version=current_plan_version,
        ):
            await self._transition(
                incident_id, {IncidentStatus.VERIFYING}, IncidentStatus.RECOVERED, now
            )
            return
        await self._transition(
            incident_id,
            {IncidentStatus.VERIFYING},
            IncidentStatus.NEEDS_ATTENTION,
            now,
        )

    async def _transition(
        self,
        incident_id: str,
        from_states: set[IncidentStatus],
        to_state: IncidentStatus,
        now: datetime,
    ) -> Incident:
        current = await self._require_incident(incident_id)
        transitioned = await self._repository.transition_incident(
            incident_id=incident_id,
            expected_version=current.version,
            from_states=from_states,
            to_state=to_state,
            updated_at=now,
        )
        if transitioned is None:
            raise RecoveryWorkflowError(f"could not transition incident to {to_state}")
        return transitioned

    async def _require_incident(self, incident_id: str) -> Incident:
        incident = await self._repository.get_incident(incident_id)
        if incident is None:
            raise RecoveryWorkflowError(f"incident {incident_id!r} does not exist")
        return incident

    def _approval_for(
        self,
        *,
        plan: RecoveryPlan,
        policy: AutonomyPolicy,
        telegram_user_id: str,
        telegram_chat_id: str,
        now: datetime,
        action_ids: list[str],
    ) -> tuple[ApprovalRequest, str]:
        approval_id = canonical_hash({"incident": plan.incident_id, "plan": plan.plan_hash})
        callback_token = (
            self._approval_tokens.token_for(approval_id=approval_id, plan_hash=plan.plan_hash)
            if self._approval_tokens is not None
            else new_callback_token()
        )
        return ApprovalRequest(
            approval_id=approval_id,
            incident_id=plan.incident_id,
            plan_version=plan.version,
            plan_hash=plan.plan_hash,
            policy_version=policy.version,
            approved_action_ids=action_ids,
            maximum_authorized=plan.total_incremental_cost,
            option_fingerprint=plan.selected_option.option_fingerprint,
            expires_at=now + timedelta(minutes=15),
            telegram_user_id=telegram_user_id,
            telegram_chat_id=telegram_chat_id,
            callback_token_hash=callback_token_hash(callback_token),
        ), callback_token

    def _callback_token_for(self, approval: ApprovalRequest) -> str | None:
        if self._approval_tokens is None:
            return None
        token = self._approval_tokens.token_for(
            approval_id=approval.approval_id, plan_hash=approval.plan_hash
        )
        if callback_token_hash(token) != approval.callback_token_hash:
            raise RecoveryWorkflowError("approval callback signing key does not match")
        return token
