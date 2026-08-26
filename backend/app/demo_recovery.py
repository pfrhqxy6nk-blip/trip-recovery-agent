from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from app.demo_data import build_demo_trip
from app.models.enums import ActionStatus
from app.models.money import Money
from app.models.policy import AutonomyPolicy
from app.services.memory import InMemoryIncidentRepository
from app.workflows.impact_analysis import ImpactAnalysisWorkflow
from app.workflows.recovery import RecoveryWorkflow


class DemoInterpreter:
    model_id = "demo-interpreter"
    prompt_version = "demo-v1"

    async def interpret(self, *_: object) -> dict[str, object]:
        return {
            "normalized_event_type": "flight_delay",
            "summary": "The Munich connection is no longer feasible.",
            "contextual_factors": [],
            "explanation": "Deterministic impact requires a recovery plan.",
            "confidence": 1.0,
        }


async def run_demo() -> None:
    from app.models.domain import DisruptionEvent

    now = datetime(2026, 8, 16, tzinfo=UTC)
    event = DisruptionEvent(
        event_id="showcase-delay-001",
        trip_id="demo-trip-001",
        type="flight_delay",
        flight="LO351",
        old_arrival=datetime(2026, 8, 20, 18, 0, tzinfo=UTC),
        new_arrival=datetime(2026, 8, 20, 19, 45, tzinfo=UTC),
    )
    policy = AutonomyPolicy(
        policy_id="showcase-policy-001",
        user_id="showcase-traveler",
        version=1,
        automatic_spending_enabled=True,
        incident_spending_limit=Money(currency="EUR", minor_units=2_000),
        created_at=now,
        updated_at=now,
    )
    repository = InMemoryIncidentRepository()
    await repository.seed_trip(build_demo_trip())
    impact_result = await ImpactAnalysisWorkflow(repository, DemoInterpreter()).process(event)
    workflow = RecoveryWorkflow(repository)
    started = await workflow.start(
        incident_id=impact_result.incident_id,
        policy=policy,
        telegram_user_id="showcase-user",
        telegram_chat_id="showcase-chat",
        now=now,
    )

    print("Trip change detected: Warsaw → Lisbon")
    print("LO351 is 105 minutes late. The Munich connection is no longer feasible.")
    print("Automatically handled and verified:")
    for action in await repository.list_actions(impact_result.incident_id):
        if action.execution_status == ActionStatus.VERIFIED:
            print(f"✓ {action.category.value.lower().replace('_', ' ')}")
    assert started.approval is not None
    print("Flight change: +€34; automatic limit: €20")
    print("Approval required — simulating [Approve recovery]")

    status = await workflow.approve(
        approval_id=started.approval.approval_id,
        callback_token_hash=started.approval.callback_token_hash,
        telegram_user_id="showcase-user",
        telegram_chat_id="showcase-chat",
        update_id="showcase-telegram-update-001",
        now=now,
    )
    print(f"Trip recovered: {status.value}")
    for action in await repository.list_actions(impact_result.incident_id):
        print(f"✓ {action.category.value.lower().replace('_', ' ')}: {action.execution_status}")


if __name__ == "__main__":
    asyncio.run(run_demo())
