from datetime import UTC, datetime

from app.demo_data import build_demo_trip
from app.models.domain import Incident
from app.models.enums import PolicyVerdict
from app.models.money import Money
from app.models.policy import AutonomyPolicy
from app.providers.demo import PersistentDemoProvider
from app.services.impact import DeterministicImpactEngine
from app.services.memory import InMemoryIncidentRepository
from app.services.recovery_planner import CanonicalRecoveryPlanner

from tests.helpers import disruption_event


def policy() -> AutonomyPolicy:
    now = datetime(2026, 8, 16, tzinfo=UTC)
    return AutonomyPolicy(
        policy_id="policy-1",
        user_id="traveler-1",
        version=1,
        automatic_spending_enabled=True,
        incident_spending_limit=Money(currency="EUR", minor_units=2_000),
        created_at=now,
        updated_at=now,
    )


def incident() -> Incident:
    event = disruption_event()
    return Incident(
        incident_id="incident-demo-001",
        trip_id=event.trip_id,
        external_event_id=event.event_id,
        correlation_id="correlation-demo-001",
        trigger=event,
        deterministic_impact=DeterministicImpactEngine().calculate(event, build_demo_trip()),
    )


def test_canonical_plan_splits_safe_actions_from_eur34_approval() -> None:
    plan = CanonicalRecoveryPlanner().create_plan(
        incident=incident(), policy=policy(), now=datetime(2026, 8, 16, tzinfo=UTC)
    )

    assert plan.selected_option.arrival_at == datetime(2026, 8, 20, 23, 15, tzinfo=UTC)
    assert plan.total_incremental_cost == Money(currency="EUR", minor_units=3_400)
    decisions = [action.policy_decision for action in plan.actions]
    assert all(decision is not None for decision in decisions)
    assert [decision.verdict for decision in decisions if decision is not None] == [
        PolicyVerdict.AUTO_APPROVED,
        PolicyVerdict.AUTO_APPROVED,
        PolicyVerdict.AUTO_APPROVED,
        PolicyVerdict.APPROVAL_REQUIRED,
    ]


async def test_demo_provider_state_is_idempotent_and_verifiable() -> None:
    repo = InMemoryIncidentRepository()
    plan = CanonicalRecoveryPlanner().create_plan(
        incident=incident(), policy=policy(), now=datetime(2026, 8, 16, tzinfo=UTC)
    )
    action = plan.actions[0]
    provider = PersistentDemoProvider(repo)

    first_reference = await provider.apply(action)
    second_reference = await provider.apply(action)

    assert first_reference == "demo-transfer:transfer-001"
    assert second_reference == first_reference
    assert await provider.verify(action)
