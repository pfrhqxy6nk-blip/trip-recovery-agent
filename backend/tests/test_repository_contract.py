from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from app.demo_data import build_demo_trip
from app.models.commands import OutboxRecord, WorkflowCommand
from app.models.domain import Incident
from app.models.enums import (
    ActionCategory,
    IncidentStatus,
    WorkflowCommandType,
)
from app.models.money import Money
from app.models.recovery import ApprovalRequest, PlannedAction, RecoveryOption, RecoveryPlan
from app.services.impact import DeterministicImpactEngine
from app.services.memory import InMemoryIncidentRepository
from app.services.ports import EventPayloadConflict
from app.workflows.impact_analysis import ImpactAnalysisWorkflow

from tests.helpers import disruption_event


async def seeded_repository() -> tuple[InMemoryIncidentRepository, Incident]:
    repository = InMemoryIncidentRepository()
    await repository.seed_trip(build_demo_trip())
    event = disruption_event()
    incident = Incident(
        incident_id=ImpactAnalysisWorkflow.stable_incident_id(event.event_id),
        trip_id=event.trip_id,
        external_event_id=event.event_id,
        correlation_id="correlation-1",
        trigger=event,
        updated_at=datetime(2026, 8, 16, tzinfo=UTC),
    )
    await repository.claim_event(
        event=event,
        incident=incident,
        worker_id="worker-1",
        lease_expires_at=incident.updated_at + timedelta(minutes=1),
    )
    return repository, incident


async def test_reused_event_id_with_new_payload_is_rejected() -> None:
    repository, incident = await seeded_repository()
    changed = disruption_event(new_arrival=datetime(2026, 8, 20, 20, 0, tzinfo=UTC))

    with pytest.raises(EventPayloadConflict):
        await repository.claim_event(
            event=changed,
            incident=incident,
            worker_id="worker-2",
            lease_expires_at=incident.updated_at + timedelta(minutes=1),
        )


async def test_compare_and_set_transition_has_one_winner() -> None:
    repository, incident = await seeded_repository()
    current = await repository.get_incident(incident.incident_id)
    assert current is not None

    outcomes = await asyncio.gather(
        repository.transition_incident(
            incident_id=current.incident_id,
            expected_version=current.version,
            from_states={IncidentStatus.RECEIVED},
            to_state=IncidentStatus.ANALYZING,
            updated_at=current.updated_at,
        ),
        repository.transition_incident(
            incident_id=current.incident_id,
            expected_version=current.version,
            from_states={IncidentStatus.RECEIVED},
            to_state=IncidentStatus.ANALYZING,
            updated_at=current.updated_at,
        ),
    )

    assert sum(outcome is not None for outcome in outcomes) == 1


async def test_stale_analysis_cannot_overwrite_newer_incident_version() -> None:
    repository, incident = await seeded_repository()
    current = await repository.get_incident(incident.incident_id)
    assert current is not None
    analyzing = await repository.transition_incident(
        incident_id=current.incident_id,
        expected_version=current.version,
        from_states={IncidentStatus.RECEIVED},
        to_state=IncidentStatus.ANALYZING,
        updated_at=current.updated_at,
    )
    assert analyzing is not None
    superseded = await repository.transition_incident(
        incident_id=current.incident_id,
        expected_version=analyzing.version,
        from_states={IncidentStatus.ANALYZING},
        to_state=IncidentStatus.FAILED,
        updated_at=current.updated_at,
    )
    assert superseded is not None
    impact = DeterministicImpactEngine().calculate(incident.trigger, build_demo_trip())

    stale_commit = await repository.commit_impact(
        incident_id=current.incident_id,
        expected_version=analyzing.version,
        impact=impact,
        gemini_model_id="gemini-test",
        prompt_version="prompt-test",
        updated_at=current.updated_at,
    )

    assert stale_commit is None
    latest = await repository.get_incident(current.incident_id)
    assert latest is not None and latest.status == IncidentStatus.FAILED


def planned_action(incident_id: str) -> PlannedAction:
    return PlannedAction(
        action_id="action-flight-001",
        incident_id=incident_id,
        plan_version=1,
        category=ActionCategory.FLIGHT_RECOVERY,
        provider="demo-flight",
        target_external_id="booking-001",
        desired_state={"replacement": "option-001"},
        cost=Money(currency="EUR", minor_units=3_400),
        effect_key="incident-001:demo-flight:booking-001:replace:0123456789abcdef",
    )


async def test_concurrent_action_claim_has_one_winner_and_effect_is_idempotent() -> None:
    repository, incident = await seeded_repository()
    action = planned_action(incident.incident_id)
    assert await repository.put_action(action)

    claims = await asyncio.gather(
        repository.claim_action(
            action_id=action.action_id,
            worker_id="worker-a",
            lease_expires_at=incident.updated_at + timedelta(minutes=1),
            now=incident.updated_at,
        ),
        repository.claim_action(
            action_id=action.action_id,
            worker_id="worker-b",
            lease_expires_at=incident.updated_at + timedelta(minutes=1),
            now=incident.updated_at,
        ),
    )

    claimed = [claim for claim in claims if claim is not None]
    assert len(claimed) == 1
    assert await repository.complete_action_and_create_effect_receipt(
        action_id=action.action_id,
        effect_key=action.effect_key,
        provider_reference="demo-order-001",
        completed_at=incident.updated_at,
    )
    assert await repository.complete_action_and_create_effect_receipt(
        action_id=action.action_id,
        effect_key=action.effect_key,
        provider_reference="demo-order-001",
        completed_at=incident.updated_at,
    )
    assert len(repository.effects) == 1


def approval(incident_id: str, now: datetime) -> ApprovalRequest:
    return ApprovalRequest(
        approval_id="approval-001",
        incident_id=incident_id,
        plan_version=1,
        plan_hash="a" * 64,
        policy_version=1,
        approved_action_ids=["action-flight-001"],
        maximum_authorized=Money(currency="EUR", minor_units=3_400),
        option_fingerprint="b" * 64,
        expires_at=now + timedelta(minutes=5),
        telegram_user_id="telegram-user-1",
        telegram_chat_id="telegram-chat-1",
        callback_token_hash="c" * 64,
    )


def outbox(incident_id: str, now: datetime) -> OutboxRecord:
    return OutboxRecord(
        outbox_id="d" * 32,
        command=WorkflowCommand(
            command_id="e" * 32,
            type=WorkflowCommandType.RESUME_AFTER_APPROVAL,
            incident_id=incident_id,
            plan_version=1,
            created_at=now,
            correlation_id="correlation-1",
        ),
        created_at=now,
    )


async def test_concurrent_approval_consumption_creates_one_outbox_record() -> None:
    repository, incident = await seeded_repository()
    now = incident.updated_at
    current = await repository.get_incident(incident.incident_id)
    assert current is not None
    option = RecoveryOption(
        provider="demo-flight",
        provider_option_id="option-001",
        option_fingerprint="b" * 64,
        incremental_cost=Money(currency="EUR", minor_units=3_400),
        quote_expires_at=now + timedelta(minutes=10),
        provider_snapshot_hash="f" * 64,
        arrival_at=now + timedelta(hours=4),
    )
    plan = RecoveryPlan(
        plan_id="plan-001",
        incident_id=incident.incident_id,
        version=1,
        source_incident_version=current.version,
        policy_version=1,
        impact_hash="9" * 64,
        selected_option=option,
        actions=[planned_action(incident.incident_id)],
        total_incremental_cost=Money(currency="EUR", minor_units=3_400),
        valid_until=now + timedelta(minutes=10),
        plan_hash="a" * 64,
    )
    assert await repository.commit_plan(plan=plan, expected_incident_version=current.version)
    current = await repository.get_incident(incident.incident_id)
    assert current is not None
    waiting = await repository.transition_incident(
        incident_id=incident.incident_id,
        expected_version=current.version,
        from_states={IncidentStatus.RECEIVED},
        to_state=IncidentStatus.WAITING_APPROVAL,
        updated_at=now,
    )
    assert waiting is not None
    assert await repository.store_approval(approval(incident.incident_id, now))

    outcomes = await asyncio.gather(
        repository.consume_approval(
            approval_id="approval-001",
            callback_token_hash="c" * 64,
            telegram_user_id="telegram-user-1",
            telegram_chat_id="telegram-chat-1",
            update_id="update-001",
            now=now,
            outbox=outbox(incident.incident_id, now),
        ),
        repository.consume_approval(
            approval_id="approval-001",
            callback_token_hash="c" * 64,
            telegram_user_id="telegram-user-1",
            telegram_chat_id="telegram-chat-1",
            update_id="update-002",
            now=now,
            outbox=outbox(incident.incident_id, now),
        ),
    )

    assert outcomes.count(True) == 1
    assert len(repository.outbox) == 1
    assert repository.approvals["approval-001"].consumed_update_id in {"update-001", "update-002"}
