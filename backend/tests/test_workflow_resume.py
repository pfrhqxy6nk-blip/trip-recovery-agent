from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.demo_data import build_demo_trip
from app.models.enums import IncidentStatus
from app.models.money import Money
from app.models.policy import AutonomyPolicy
from app.models.recovery import PlannedAction
from app.providers.demo import PersistentDemoProvider
from app.services.approval_tokens import ApprovalTokenManager, callback_token_hash
from app.services.memory import InMemoryIncidentRepository
from app.workflows.impact_analysis import ImpactAnalysisWorkflow
from app.workflows.recovery import RecoveryWorkflow

from tests.helpers import ValidInterpreter, disruption_event


def policy(now: datetime) -> AutonomyPolicy:
    return AutonomyPolicy(
        policy_id="policy-resume",
        user_id="traveler-resume",
        version=1,
        automatic_spending_enabled=True,
        incident_spending_limit=Money(currency="EUR", minor_units=2_000),
        created_at=now,
        updated_at=now,
    )


class CrashAfterFlightEffect(PersistentDemoProvider):
    def __init__(self, repository: InMemoryIncidentRepository) -> None:
        super().__init__(repository)
        self.crashed = False

    async def apply(self, action: PlannedAction) -> str:
        reference = await super().apply(action)
        if action.provider == "demo-flight" and not self.crashed:
            self.crashed = True
            raise RuntimeError("simulated response loss after provider mutation")
        return reference


async def prepared_recovery() -> tuple[InMemoryIncidentRepository, RecoveryWorkflow, datetime, str]:
    now = datetime(2026, 8, 16, tzinfo=UTC)
    repository = InMemoryIncidentRepository()
    await repository.seed_trip(build_demo_trip())
    outcome = await ImpactAnalysisWorkflow(repository, ValidInterpreter()).process(
        disruption_event(event_id="resume-delay-001")
    )
    return repository, RecoveryWorkflow(repository), now, outcome.incident_id


async def test_restart_after_provider_effect_reconciles_without_second_mutation() -> None:
    repository, _, now, incident_id = await prepared_recovery()
    crashing_provider = CrashAfterFlightEffect(repository)
    first_process = RecoveryWorkflow(repository, provider=crashing_provider)
    started = await first_process.start(
        incident_id=incident_id,
        policy=policy(now),
        telegram_user_id="telegram-user-resume",
        telegram_chat_id="telegram-chat-resume",
        now=now,
    )
    assert started.approval is not None

    with pytest.raises(RuntimeError, match="response loss"):
        await first_process.approve(
            approval_id=started.approval.approval_id,
            callback_token_hash=started.approval.callback_token_hash,
            telegram_user_id="telegram-user-resume",
            telegram_chat_id="telegram-chat-resume",
            update_id="resume-update-1",
            now=now,
        )

    assert (await repository.get_incident(incident_id)).status == IncidentStatus.EXECUTING_APPROVED  # type: ignore[union-attr]
    assert len(repository.effects) == 3
    assert repository.demo_provider_state["demo-flight:booking-001"]["desired_state"]

    restarted = RecoveryWorkflow(repository)
    status = await restarted.resume_after_approval(
        approval_id=started.approval.approval_id, now=now
    )
    duplicate = await restarted.resume_after_approval(
        approval_id=started.approval.approval_id, now=now
    )

    assert status == IncidentStatus.RECOVERED
    assert duplicate == IncidentStatus.RECOVERED
    assert len(repository.effects) == 4
    assert crashing_provider.crashed is True
    flight = next(
        action
        for action in await repository.list_actions(incident_id)
        if action.provider == "demo-flight"
    )
    attempts = await repository.list_action_attempts(flight.action_id)
    assert len(attempts) == 1
    assert attempts[0].outcome.value == "RECONCILED"
    assert attempts[0].retry_class.value == "UNKNOWN"


async def test_committed_plan_resumes_from_notifying_phase() -> None:
    repository, workflow, now, incident_id = await prepared_recovery()
    incident = await repository.get_incident(incident_id)
    assert incident is not None
    plan = workflow._planner.create_plan(incident=incident, policy=policy(now), now=now)
    assert await repository.commit_plan(plan=plan, expected_incident_version=incident.version)
    for action in plan.actions:
        assert await repository.put_action(action)
    current = await repository.get_incident(incident_id)
    assert current is not None
    notifying = await repository.transition_incident(
        incident_id=incident_id,
        expected_version=current.version,
        from_states={IncidentStatus.PLANNING},
        to_state=IncidentStatus.NOTIFYING,
        updated_at=now,
    )
    assert notifying is not None

    restarted = RecoveryWorkflow(repository)
    result = await restarted.continue_plan(
        plan=plan,
        policy=policy(now),
        telegram_user_id="telegram-user-resume",
        telegram_chat_id="telegram-chat-resume",
        now=now,
        notification_delivered=True,
    )

    assert result.incident_status == IncidentStatus.WAITING_APPROVAL
    assert result.approval is not None
    assert len(repository.effects) == 3


async def test_provider_actions_wait_until_initial_notification_is_delivered() -> None:
    repository, workflow, now, incident_id = await prepared_recovery()
    prepared = await workflow.prepare(
        incident_id=incident_id,
        policy=policy(now),
        now=now,
    )

    waiting = await workflow.continue_plan(
        plan=prepared.plan,
        policy=policy(now),
        telegram_user_id="telegram-user-resume",
        telegram_chat_id="telegram-chat-resume",
        now=now,
    )

    assert waiting.incident_status == IncidentStatus.NOTIFYING
    assert repository.effects == {}

    continued = await workflow.continue_plan(
        plan=prepared.plan,
        policy=policy(now),
        telegram_user_id="telegram-user-resume",
        telegram_chat_id="telegram-chat-resume",
        now=now,
        notification_delivered=True,
    )

    assert continued.incident_status == IncidentStatus.WAITING_APPROVAL
    assert len(repository.effects) == 3


async def test_restart_regenerates_same_pending_approval_callback() -> None:
    repository, workflow, now, incident_id = await prepared_recovery()
    token_manager = ApprovalTokenManager("pilot-signing-key-that-is-at-least-32-bytes")
    workflow = RecoveryWorkflow(repository, approval_tokens=token_manager)
    started = await workflow.start(
        incident_id=incident_id,
        policy=policy(now),
        telegram_user_id="telegram-user-resume",
        telegram_chat_id="telegram-chat-resume",
        now=now,
    )
    assert started.approval_callback_token is not None

    restarted = RecoveryWorkflow(repository, approval_tokens=token_manager)
    resumed = await restarted.continue_plan(
        plan=started.plan,
        policy=policy(now),
        telegram_user_id="telegram-user-resume",
        telegram_chat_id="telegram-chat-resume",
        now=now,
        notification_delivered=True,
    )

    assert resumed.approval_callback_token == started.approval_callback_token
    assert resumed.approval is not None
    assert callback_token_hash(resumed.approval_callback_token) == (
        resumed.approval.callback_token_hash
    )
