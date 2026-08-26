from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from app.demo_data import build_owned_demo_trip
from app.models.commands import WorkflowCommand
from app.models.enums import (
    IncidentStatus,
    OnboardingStep,
    OutboxStatus,
    PlanStatus,
    WorkflowCommandStatus,
)
from app.models.money import Money
from app.models.policy import AutonomyPolicy
from app.models.recovery import PlannedAction
from app.models.telegram import TelegramMessageReceipt, TelegramView, TravelerProfile
from app.providers.demo import PersistentDemoProvider
from app.providers.telegram import TelegramGatewayError, TelegramRetryClass
from app.services.action_executor import ProviderActionError
from app.services.memory import InMemoryIncidentRepository
from app.services.outbox import DurableOutboxDispatcher
from app.services.ports import EventPayloadConflict
from app.services.telegram_delivery import DurableTelegramDelivery
from app.workflows.impact_analysis import ImpactAnalysisWorkflow
from app.workflows.recovery import RecoveryWorkflow

from tests.helpers import ValidInterpreter, disruption_event


class RecordingCommandPublisher:
    def __init__(self) -> None:
        self.commands: list[WorkflowCommand] = []

    async def publish_command(self, command: WorkflowCommand) -> str:
        self.commands.append(command)
        return f"message-{len(self.commands)}"


async def prepared() -> tuple[
    InMemoryIncidentRepository, RecoveryWorkflow, AutonomyPolicy, datetime, str
]:
    now = datetime(2026, 8, 17, tzinfo=UTC)
    repository = InMemoryIncidentRepository()
    await repository.seed_trip(
        build_owned_demo_trip(owner_user_id="telegram:101", trip_id="demo-trip-001")
    )
    await repository.save_traveler(
        TravelerProfile(
            user_id="telegram:101",
            telegram_user_id="101",
            telegram_chat_id="202",
            onboarding_step=OnboardingStep.COMPLETE,
            automatic_spending_enabled=True,
            incident_spending_limit=Money(currency="EUR", minor_units=2_000),
            active_policy_version=1,
            created_at=now,
            updated_at=now,
        )
    )
    outcome = await ImpactAnalysisWorkflow(repository, ValidInterpreter()).process(
        disruption_event(event_id="durable-command-delay")
    )
    policy = AutonomyPolicy(
        policy_id="durable-command-policy",
        user_id="telegram:101",
        version=1,
        automatic_spending_enabled=True,
        incident_spending_limit=Money(currency="EUR", minor_units=2_000),
        created_at=now,
        updated_at=now,
    )
    return repository, RecoveryWorkflow(repository), policy, now, outcome.incident_id


async def test_approval_outbox_drives_resume_and_duplicate_command_is_noop() -> None:
    repository, workflow, policy, now, incident_id = await prepared()
    started = await workflow.start(
        incident_id=incident_id,
        policy=policy,
        telegram_user_id="telegram-user-1",
        telegram_chat_id="telegram-chat-1",
        now=now,
    )
    assert started.approval is not None

    waiting = await workflow.approve(
        approval_id=started.approval.approval_id,
        callback_token_hash=started.approval.callback_token_hash,
        telegram_user_id="telegram-user-1",
        telegram_chat_id="telegram-chat-1",
        update_id="approval-update-1",
        now=now,
        process_inline=False,
    )
    outbox = next(
        record
        for record in repository.outbox.values()
        if record.command.type.value == "RESUME_AFTER_APPROVAL"
    )
    publisher = RecordingCommandPublisher()
    dispatcher = DurableOutboxDispatcher(repository, publisher)

    first_dispatch = await dispatcher.dispatch_pending(now=now)
    second_dispatch = await dispatcher.dispatch_pending(now=now + timedelta(seconds=1))
    recovered = await workflow.process_command(
        command=publisher.commands[0], worker_id="command-worker-1", now=now
    )
    duplicate = await workflow.process_command(
        command=publisher.commands[0],
        worker_id="command-worker-2",
        now=now + timedelta(seconds=1),
    )
    command_state = await repository.get_workflow_command_state(outbox.command.command_id)

    assert waiting == IncidentStatus.WAITING_APPROVAL
    assert len(repository.effects) == 4
    assert first_dispatch.published == 1 and first_dispatch.pending == 1
    assert second_dispatch.published == 0
    assert len(publisher.commands) == 1
    assert repository.outbox[outbox.outbox_id].status == OutboxStatus.PUBLISHED
    assert recovered == IncidentStatus.RECOVERED
    assert duplicate == IncidentStatus.RECOVERED
    assert command_state is not None
    assert command_state.status == WorkflowCommandStatus.COMPLETED


async def test_start_and_continue_commands_stop_at_notification_then_wait_for_approval() -> None:
    repository, workflow, _, now, incident_id = await prepared()
    start = WorkflowCommand(
        command_id="start-recovery-command-001",
        type="START_RECOVERY",
        incident_id=incident_id,
        created_at=now,
        correlation_id="start-command-test",
    )
    notifying = await workflow.process_command(command=start, worker_id="start-worker", now=now)
    plan = await repository.get_current_plan(incident_id)
    assert plan is not None
    continue_command = WorkflowCommand(
        command_id="continue-recovery-command-001",
        type="CONTINUE_WORKFLOW",
        incident_id=incident_id,
        plan_version=plan.version,
        created_at=now,
        correlation_id="continue-command-test",
        payload={"notification_delivered": "true"},
    )
    waiting = await workflow.process_command(
        command=continue_command,
        worker_id="continue-worker",
        now=now,
    )

    assert notifying == IncidentStatus.NOTIFYING
    assert waiting == IncidentStatus.WAITING_APPROVAL
    assert len(repository.effects) == 3
    assert len(repository.approvals) == 1


async def test_concurrent_command_claim_has_one_winner_and_expired_lease_reclaims() -> None:
    repository, workflow, policy, now, incident_id = await prepared()
    started = await workflow.start(
        incident_id=incident_id,
        policy=policy,
        telegram_user_id="telegram-user-1",
        telegram_chat_id="telegram-chat-1",
        now=now,
    )
    assert started.approval is not None
    await workflow.approve(
        approval_id=started.approval.approval_id,
        callback_token_hash=started.approval.callback_token_hash,
        telegram_user_id="telegram-user-1",
        telegram_chat_id="telegram-chat-1",
        update_id="approval-update-2",
        now=now,
        process_inline=False,
    )
    command = next(
        record.command
        for record in repository.outbox.values()
        if record.command.type.value == "RESUME_AFTER_APPROVAL"
    )

    first, second = await asyncio.gather(
        repository.claim_workflow_command(
            command=command,
            worker_id="worker-a",
            lease_expires_at=now + timedelta(seconds=30),
            now=now,
        ),
        repository.claim_workflow_command(
            command=command,
            worker_id="worker-b",
            lease_expires_at=now + timedelta(seconds=30),
            now=now,
        ),
    )
    reclaimed = await repository.claim_workflow_command(
        command=command,
        worker_id="worker-c",
        lease_expires_at=now + timedelta(seconds=61),
        now=now + timedelta(seconds=31),
    )

    assert sum((first, second)) == 1
    assert reclaimed is True


async def test_command_id_payload_collision_is_rejected() -> None:
    repository, workflow, policy, now, incident_id = await prepared()
    started = await workflow.start(
        incident_id=incident_id,
        policy=policy,
        telegram_user_id="telegram-user-1",
        telegram_chat_id="telegram-chat-1",
        now=now,
    )
    assert started.approval is not None
    await workflow.approve(
        approval_id=started.approval.approval_id,
        callback_token_hash=started.approval.callback_token_hash,
        telegram_user_id="telegram-user-1",
        telegram_chat_id="telegram-chat-1",
        update_id="approval-update-3",
        now=now,
        process_inline=False,
    )
    command = next(
        record.command
        for record in repository.outbox.values()
        if record.command.type.value == "RESUME_AFTER_APPROVAL"
    )
    assert await repository.claim_workflow_command(
        command=command,
        worker_id="worker-a",
        lease_expires_at=now + timedelta(seconds=30),
        now=now,
    )

    with pytest.raises(EventPayloadConflict):
        await repository.claim_workflow_command(
            command=command.model_copy(update={"payload": {"approval_id": "tampered"}}),
            worker_id="worker-b",
            lease_expires_at=now + timedelta(seconds=30),
            now=now,
        )


class RetryFlightOnceProvider(PersistentDemoProvider):
    def __init__(self, repository: InMemoryIncidentRepository) -> None:
        super().__init__(repository)
        self.flight_calls = 0

    async def apply(self, action: PlannedAction) -> str:
        if action.provider == "demo-flight":
            self.flight_calls += 1
            if self.flight_calls == 1:
                raise ProviderActionError(
                    error_code="flight_provider_busy",
                    retryable=True,
                    retry_after_seconds=30,
                )
        return await super().apply(action)


async def test_retry_command_resumes_approved_phase_only_when_due() -> None:
    repository, _, policy, now, incident_id = await prepared()
    provider = RetryFlightOnceProvider(repository)
    workflow = RecoveryWorkflow(repository, provider=provider)
    started = await workflow.start(
        incident_id=incident_id,
        policy=policy,
        telegram_user_id="telegram-user-1",
        telegram_chat_id="telegram-chat-1",
        now=now,
    )
    assert started.approval is not None

    scheduled = await workflow.approve(
        approval_id=started.approval.approval_id,
        callback_token_hash=started.approval.callback_token_hash,
        telegram_user_id="telegram-user-1",
        telegram_chat_id="telegram-chat-1",
        update_id="approval-retry-update",
        now=now,
    )
    retry = next(
        record.command
        for record in repository.outbox.values()
        if record.command.type.value == "RETRY_ACTION"
    )
    too_early = await workflow.process_command(
        command=retry,
        worker_id="retry-worker-early",
        now=now + timedelta(seconds=29),
    )
    recovered = await workflow.process_command(
        command=retry,
        worker_id="retry-worker-due",
        now=now + timedelta(seconds=30),
    )

    assert scheduled == IncidentStatus.RETRY_SCHEDULED
    assert too_early == IncidentStatus.RETRY_SCHEDULED
    assert recovered == IncidentStatus.RECOVERED
    assert provider.flight_calls == 2
    assert len(repository.effects) == 4


class TerminalTransferProvider(PersistentDemoProvider):
    async def apply(self, action: PlannedAction) -> str:
        if action.provider == "demo-transfer":
            raise ProviderActionError(error_code="transfer_change_rejected", retryable=False)
        return await super().apply(action)


async def test_terminal_action_moves_incident_to_needs_attention_without_retry() -> None:
    repository, _, policy, now, incident_id = await prepared()
    result = await RecoveryWorkflow(
        repository, provider=TerminalTransferProvider(repository)
    ).start(
        incident_id=incident_id,
        policy=policy,
        telegram_user_id="telegram-user-1",
        telegram_chat_id="telegram-chat-1",
        now=now,
    )

    assert result.incident_status == IncidentStatus.NEEDS_ATTENTION
    assert all(record.command.type.value != "RETRY_ACTION" for record in repository.outbox.values())


async def test_expired_approval_enqueues_replan_and_creates_new_plan_version() -> None:
    repository, workflow, policy, now, incident_id = await prepared()
    started = await workflow.start(
        incident_id=incident_id,
        policy=policy,
        telegram_user_id="101",
        telegram_chat_id="202",
        now=now,
    )
    assert started.approval is not None
    expiry = next(
        record.command
        for record in repository.outbox.values()
        if record.command.type.value == "EXPIRE_APPROVAL"
    )

    too_early = await workflow.process_command(
        command=expiry,
        worker_id="expiry-worker-early",
        now=started.approval.expires_at - timedelta(seconds=1),
    )
    planning = await workflow.process_command(
        command=expiry,
        worker_id="expiry-worker-due",
        now=started.approval.expires_at,
    )
    replan = next(
        record.command
        for record in repository.outbox.values()
        if record.command.type.value == "REPLAN"
    )
    notifying = await workflow.process_command(
        command=replan,
        worker_id="replan-worker",
        now=started.approval.expires_at,
    )
    current_plan = await repository.get_current_plan(incident_id)
    superseded_plan = repository.plans[(incident_id, 1)]
    expired = await repository.get_approval(started.approval.approval_id)

    assert too_early == IncidentStatus.WAITING_APPROVAL
    assert planning == IncidentStatus.PLANNING
    assert notifying == IncidentStatus.NOTIFYING
    assert current_plan is not None and current_plan.version == 2
    assert superseded_plan.status == PlanStatus.SUPERSEDED
    assert expired is not None and expired.status.value == "EXPIRED"


class RetryFinalGateway:
    def __init__(self) -> None:
        self.calls = 0

    async def send_message(self, *, chat_id: str, view: TelegramView) -> TelegramMessageReceipt:
        self.calls += 1
        if self.calls == 1:
            raise TelegramGatewayError(
                operation="send_message",
                retry_class=TelegramRetryClass.SAFE_RETRY,
            )
        return TelegramMessageReceipt(chat_id=chat_id, message_id=501)

    async def edit_message(
        self, *, chat_id: str, message_id: int, view: TelegramView
    ) -> TelegramMessageReceipt:
        raise AssertionError("not used")

    async def answer_callback_query(
        self,
        *,
        callback_query_id: str,
        text: str | None = None,
        show_alert: bool = False,
    ) -> None:
        raise AssertionError("not used")


async def test_final_notification_retry_does_not_repeat_provider_actions() -> None:
    repository, workflow, policy, now, incident_id = await prepared()
    started = await workflow.start(
        incident_id=incident_id,
        policy=policy,
        telegram_user_id="101",
        telegram_chat_id="202",
        now=now,
    )
    assert started.approval is not None
    assert (
        await workflow.approve(
            approval_id=started.approval.approval_id,
            callback_token_hash=started.approval.callback_token_hash,
            telegram_user_id="101",
            telegram_chat_id="202",
            update_id="final-notification-approval",
            now=now,
        )
        == IncidentStatus.RECOVERED
    )
    effects_before = dict(repository.effects)
    gateway = RetryFinalGateway()
    delivery = DurableTelegramDelivery(repository, gateway)
    arguments = {
        "incident_id": incident_id,
        "kind": "FINAL",
        "dedupe_key": "recovered-v1",
        "chat_id": "202",
        "view": TelegramView(text="Trip recovered"),
        "now": now,
    }

    with pytest.raises(TelegramGatewayError):
        await delivery.send_once(**arguments)  # type: ignore[arg-type]
    assert await delivery.send_once(**arguments)  # type: ignore[arg-type]

    assert gateway.calls == 2
    assert repository.effects == effects_before
    assert len(repository.effects) == 4
