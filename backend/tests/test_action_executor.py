from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.demo_data import build_demo_trip
from app.models.domain import Incident
from app.models.enums import ActionStatus, ApprovalStatus
from app.models.money import Money
from app.models.policy import AutonomyPolicy
from app.models.recovery import ApprovalRequest, PlannedAction
from app.providers.demo import PersistentDemoProvider
from app.services.action_executor import ActionExecutor, ProviderActionError
from app.services.impact import DeterministicImpactEngine
from app.services.memory import InMemoryIncidentRepository
from app.services.recovery_planner import CanonicalRecoveryPlanner
from app.services.verifier import can_mark_recovered

from tests.helpers import disruption_event


def build_plan() -> tuple[Incident, AutonomyPolicy, datetime]:
    now = datetime(2026, 8, 16, tzinfo=UTC)
    event = disruption_event()
    incident = Incident(
        incident_id="incident-executor-001",
        trip_id=event.trip_id,
        external_event_id=event.event_id,
        correlation_id="correlation-executor-001",
        trigger=event,
        deterministic_impact=DeterministicImpactEngine().calculate(event, build_demo_trip()),
    )
    policy = AutonomyPolicy(
        policy_id="policy-executor-001",
        user_id="traveler-1",
        version=1,
        automatic_spending_enabled=True,
        incident_spending_limit=Money(currency="EUR", minor_units=2_000),
        created_at=now,
        updated_at=now,
    )
    return incident, policy, now


async def test_auto_actions_verify_once_and_flight_waits_for_approval() -> None:
    incident, policy, now = build_plan()
    plan = CanonicalRecoveryPlanner().create_plan(incident=incident, policy=policy, now=now)
    repository = InMemoryIncidentRepository()
    for action in plan.actions:
        assert await repository.put_action(action)
    executor = ActionExecutor(repository, PersistentDemoProvider(repository))

    results = [
        await executor.execute(action=action, worker_id="worker-1", now=now)
        for action in plan.actions
    ]
    actions = await repository.list_actions(incident.incident_id)

    assert [result.execution_status if result else None for result in results] == [
        ActionStatus.VERIFIED,
        ActionStatus.VERIFIED,
        ActionStatus.VERIFIED,
        None,
    ]
    assert can_mark_recovered(actions, None) is False
    assert len(repository.effects) == 3


async def test_approved_flight_completes_once_and_enables_recovery() -> None:
    incident, policy, now = build_plan()
    plan = CanonicalRecoveryPlanner().create_plan(incident=incident, policy=policy, now=now)
    repository = InMemoryIncidentRepository()
    for action in plan.actions:
        assert await repository.put_action(action)
    executor = ActionExecutor(repository, PersistentDemoProvider(repository))
    for action in plan.actions[:3]:
        await executor.execute(action=action, worker_id="worker-1", now=now)

    flight = plan.actions[-1]
    approved = await executor.execute(
        action=flight, worker_id="worker-1", now=now, approval_granted=True
    )
    duplicate = await executor.execute(
        action=flight, worker_id="worker-2", now=now + timedelta(seconds=1), approval_granted=True
    )
    approval = ApprovalRequest(
        approval_id="approval-executor-001",
        incident_id=incident.incident_id,
        plan_version=1,
        plan_hash=plan.plan_hash,
        policy_version=1,
        maximum_authorized=Money(currency="EUR", minor_units=3_400),
        option_fingerprint=plan.selected_option.option_fingerprint,
        expires_at=now + timedelta(minutes=5),
        telegram_user_id="user-1",
        telegram_chat_id="chat-1",
        callback_token_hash="a" * 64,
        status=ApprovalStatus.APPROVED,
    )

    assert approved is not None and approved.execution_status == ActionStatus.VERIFIED
    assert duplicate is not None and duplicate.execution_status == ActionStatus.VERIFIED
    assert len(repository.effects) == 4
    assert can_mark_recovered(await repository.list_actions(incident.incident_id), approval)


class BrokenVerificationProvider(PersistentDemoProvider):
    async def verify(self, action: PlannedAction) -> bool:
        return False


async def test_verification_failure_prevents_recovery() -> None:
    incident, policy, now = build_plan()
    plan = CanonicalRecoveryPlanner().create_plan(incident=incident, policy=policy, now=now)
    repository = InMemoryIncidentRepository()
    action = plan.actions[0]
    assert await repository.put_action(action)
    executor = ActionExecutor(repository, BrokenVerificationProvider(repository))

    result = await executor.execute(action=action, worker_id="worker-1", now=now)

    assert result is not None and result.execution_status == ActionStatus.VERIFICATION_FAILED
    assert can_mark_recovered([result], None) is False


async def test_expired_action_lease_is_reclaimed_after_provider_reread() -> None:
    incident, policy, now = build_plan()
    plan = CanonicalRecoveryPlanner().create_plan(incident=incident, policy=policy, now=now)
    repository = InMemoryIncidentRepository()
    action = plan.actions[0]
    assert await repository.put_action(action)
    assert await repository.claim_action(
        action_id=action.action_id,
        worker_id="crashed-worker",
        lease_expires_at=now + timedelta(seconds=10),
        now=now,
    )
    executor = ActionExecutor(repository, PersistentDemoProvider(repository))

    result = await executor.execute(
        action=action,
        worker_id="replacement-worker",
        now=now + timedelta(seconds=11),
    )

    assert result is not None and result.execution_status == ActionStatus.VERIFIED
    assert len(repository.effects) == 1


async def test_plan_revision_reuses_same_semantic_effect_receipt() -> None:
    incident, policy, now = build_plan()
    plan = CanonicalRecoveryPlanner().create_plan(incident=incident, policy=policy, now=now)
    repository = InMemoryIncidentRepository()
    original = plan.actions[0]
    assert await repository.put_action(original)
    executor = ActionExecutor(repository, PersistentDemoProvider(repository))
    first = await executor.execute(action=original, worker_id="worker-1", now=now)
    assert first is not None and first.execution_status == ActionStatus.VERIFIED
    revised = original.model_copy(
        update={
            "action_id": f"{original.incident_id}:v2:transfer",
            "plan_version": 2,
            "execution_status": ActionStatus.PENDING,
            "provider_reference": None,
        }
    )
    assert await repository.put_action(revised)

    reconciled = await executor.execute(
        action=revised, worker_id="worker-2", now=now + timedelta(seconds=1)
    )

    assert reconciled is not None
    assert reconciled.execution_status == ActionStatus.VERIFIED
    assert len(repository.effects) == 1


class RetryOnceProvider(PersistentDemoProvider):
    def __init__(self, repository: InMemoryIncidentRepository) -> None:
        super().__init__(repository)
        self.apply_calls = 0

    async def apply(self, action: PlannedAction) -> str:
        self.apply_calls += 1
        if self.apply_calls == 1:
            raise ProviderActionError(
                error_code="provider_temporarily_unavailable",
                retryable=True,
                retry_after_seconds=30,
            )
        return await super().apply(action)


async def test_retryable_provider_failure_waits_then_retries_with_immutable_attempts() -> None:
    incident, policy, now = build_plan()
    plan = CanonicalRecoveryPlanner().create_plan(incident=incident, policy=policy, now=now)
    repository = InMemoryIncidentRepository()
    action = plan.actions[0]
    assert await repository.put_action(action)
    provider = RetryOnceProvider(repository)
    executor = ActionExecutor(repository, provider)

    failed = await executor.execute(action=action, worker_id="worker-1", now=now)
    too_early = await executor.execute(
        action=action, worker_id="worker-2", now=now + timedelta(seconds=29)
    )
    recovered = await executor.execute(
        action=action, worker_id="worker-3", now=now + timedelta(seconds=30)
    )
    attempts = await repository.list_action_attempts(action.action_id)

    assert failed is not None and failed.execution_status == ActionStatus.FAILED_RETRYABLE
    assert too_early is not None and too_early.execution_status == ActionStatus.FAILED_RETRYABLE
    assert recovered is not None and recovered.execution_status == ActionStatus.VERIFIED
    assert provider.apply_calls == 2
    assert [attempt.attempt_number for attempt in attempts] == [1, 2]
    assert [attempt.retry_class.value for attempt in attempts] == ["RETRYABLE", "NONE"]
    assert len(repository.effects) == 1


class TerminalFailureProvider(PersistentDemoProvider):
    def __init__(self, repository: InMemoryIncidentRepository) -> None:
        super().__init__(repository)
        self.apply_calls = 0

    async def apply(self, action: PlannedAction) -> str:
        self.apply_calls += 1
        raise ProviderActionError(error_code="provider_rejected_change", retryable=False)


async def test_terminal_provider_failure_is_never_retried() -> None:
    incident, policy, now = build_plan()
    plan = CanonicalRecoveryPlanner().create_plan(incident=incident, policy=policy, now=now)
    repository = InMemoryIncidentRepository()
    action = plan.actions[0]
    assert await repository.put_action(action)
    provider = TerminalFailureProvider(repository)
    executor = ActionExecutor(repository, provider)

    failed = await executor.execute(action=action, worker_id="worker-1", now=now)
    duplicate = await executor.execute(
        action=action, worker_id="worker-2", now=now + timedelta(days=1)
    )
    attempts = await repository.list_action_attempts(action.action_id)

    assert failed is not None and failed.execution_status == ActionStatus.FAILED_TERMINAL
    assert duplicate is not None and duplicate.execution_status == ActionStatus.FAILED_TERMINAL
    assert provider.apply_calls == 1
    assert len(attempts) == 1 and attempts[0].retry_class.value == "TERMINAL"
    assert repository.effects == {}
