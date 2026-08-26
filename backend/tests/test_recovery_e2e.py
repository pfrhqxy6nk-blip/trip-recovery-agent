from __future__ import annotations

from datetime import UTC, datetime

from app.demo_data import build_demo_trip
from app.models.enums import ActionStatus, IncidentStatus
from app.models.money import Money
from app.models.policy import AutonomyPolicy
from app.services.memory import InMemoryIncidentRepository
from app.workflows.impact_analysis import ImpactAnalysisWorkflow
from app.workflows.recovery import RecoveryWorkflow

from tests.helpers import ValidInterpreter, disruption_event


def demo_policy() -> AutonomyPolicy:
    now = datetime(2026, 8, 16, tzinfo=UTC)
    return AutonomyPolicy(
        policy_id="policy-e2e-001",
        user_id="telegram-user-1",
        version=1,
        automatic_spending_enabled=True,
        incident_spending_limit=Money(currency="EUR", minor_units=2_000),
        created_at=now,
        updated_at=now,
    )


async def test_canonical_recovery_runs_end_to_end_and_duplicate_approval_is_safe() -> None:
    now = datetime(2026, 8, 16, tzinfo=UTC)
    repository = InMemoryIncidentRepository()
    await repository.seed_trip(build_demo_trip())
    impact = ImpactAnalysisWorkflow(repository, ValidInterpreter())
    outcome = await impact.process(disruption_event(event_id="e2e-delay-001"))
    workflow = RecoveryWorkflow(repository)

    started = await workflow.start(
        incident_id=outcome.incident_id,
        policy=demo_policy(),
        telegram_user_id="telegram-user-1",
        telegram_chat_id="telegram-chat-1",
        now=now,
    )

    assert started.incident_status == IncidentStatus.WAITING_APPROVAL
    assert started.approval is not None
    assert len(repository.effects) == 3
    waiting_actions = await repository.list_actions(outcome.incident_id)
    assert [action.execution_status for action in waiting_actions] == [
        ActionStatus.VERIFIED,
        ActionStatus.VERIFIED,
        ActionStatus.VERIFIED,
        ActionStatus.PENDING,
    ]

    status = await workflow.approve(
        approval_id=started.approval.approval_id,
        callback_token_hash=started.approval.callback_token_hash,
        telegram_user_id="telegram-user-1",
        telegram_chat_id="telegram-chat-1",
        update_id="telegram-update-001",
        now=now,
    )
    duplicate_status = await workflow.approve(
        approval_id=started.approval.approval_id,
        callback_token_hash=started.approval.callback_token_hash,
        telegram_user_id="telegram-user-1",
        telegram_chat_id="telegram-chat-1",
        update_id="telegram-update-002",
        now=now,
    )

    assert status == IncidentStatus.RECOVERED
    assert duplicate_status == IncidentStatus.RECOVERED
    assert len(repository.effects) == 4
    assert len(repository.outbox) == 2
    assert all(
        action.execution_status == ActionStatus.VERIFIED
        for action in await repository.list_actions(outcome.incident_id)
    )


async def test_no_approval_policy_reaches_recovered_without_callback() -> None:
    now = datetime(2026, 8, 16, tzinfo=UTC)
    repository = InMemoryIncidentRepository()
    await repository.seed_trip(build_demo_trip())
    outcome = await ImpactAnalysisWorkflow(repository, ValidInterpreter()).process(
        disruption_event(event_id="e2e-auto-delay-001")
    )
    policy = demo_policy().model_copy(
        update={"incident_spending_limit": Money(currency="EUR", minor_units=5_000)}
    )

    result = await RecoveryWorkflow(repository).start(
        incident_id=outcome.incident_id,
        policy=policy,
        telegram_user_id="telegram-user-1",
        telegram_chat_id="telegram-chat-1",
        now=now,
    )

    assert result.approval is None
    assert result.incident_status == IncidentStatus.RECOVERED
    assert len(repository.effects) == 4
    assert all(
        action.execution_status == ActionStatus.VERIFIED
        for action in await repository.list_actions(outcome.incident_id)
    )


async def test_newer_disruption_invalidates_old_pending_authority() -> None:
    now = datetime(2026, 8, 16, tzinfo=UTC)
    repository = InMemoryIncidentRepository()
    await repository.seed_trip(build_demo_trip())
    impact = ImpactAnalysisWorkflow(repository, ValidInterpreter())
    first = await impact.process(disruption_event(event_id="older-delay"))
    workflow = RecoveryWorkflow(repository)
    started = await workflow.start(
        incident_id=first.incident_id,
        policy=demo_policy(),
        telegram_user_id="telegram-user-1",
        telegram_chat_id="telegram-chat-1",
        now=now,
    )
    assert started.approval is not None
    await impact.process(disruption_event(event_id="newer-delay"))

    status = await workflow.approve(
        approval_id=started.approval.approval_id,
        callback_token_hash=started.approval.callback_token_hash,
        telegram_user_id="telegram-user-1",
        telegram_chat_id="telegram-chat-1",
        update_id="stale-approval-update",
        now=now,
    )

    assert status == IncidentStatus.WAITING_APPROVAL
    assert len(repository.effects) == 3
