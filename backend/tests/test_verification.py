from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.demo_data import build_demo_trip
from app.models.domain import Incident
from app.models.enums import ActionStatus, ApprovalStatus
from app.models.money import Money
from app.models.policy import AutonomyPolicy
from app.models.recovery import ApprovalRequest, PlannedAction, RecoveryPlan
from app.services.impact import DeterministicImpactEngine
from app.services.recovery_planner import CanonicalRecoveryPlanner
from app.services.verifier import can_mark_recovered, deterministic_itinerary_conflicts

from tests.helpers import disruption_event


def recovered_fixture() -> tuple[RecoveryPlan, list[PlannedAction], ApprovalRequest]:
    now = datetime(2026, 8, 16, tzinfo=UTC)
    event = disruption_event(event_id="verification-event")
    incident = Incident(
        incident_id="incident-verification-001",
        trip_id=event.trip_id,
        external_event_id=event.event_id,
        correlation_id="correlation-verification",
        trigger=event,
        deterministic_impact=DeterministicImpactEngine().calculate(event, build_demo_trip()),
    )
    policy = AutonomyPolicy(
        policy_id="policy-verification",
        user_id="traveler-verification",
        version=1,
        automatic_spending_enabled=True,
        incident_spending_limit=Money(currency="EUR", minor_units=2_000),
        created_at=now,
        updated_at=now,
    )
    plan = CanonicalRecoveryPlanner().create_plan(incident=incident, policy=policy, now=now)
    actions = [
        action.model_copy(
            update={
                "execution_status": ActionStatus.VERIFIED,
                "provider_reference": action.verification_spec["resource_id"],
            }
        )
        for action in plan.actions
    ]
    approval_required = [
        action.action_id
        for action in plan.actions
        if action.policy_decision is not None
        and action.policy_decision.verdict.value == "APPROVAL_REQUIRED"
    ]
    approval = ApprovalRequest(
        approval_id="approval-verification",
        incident_id=incident.incident_id,
        plan_version=plan.version,
        plan_hash=plan.plan_hash,
        policy_version=policy.version,
        approved_action_ids=approval_required,
        maximum_authorized=plan.total_incremental_cost,
        option_fingerprint=plan.selected_option.option_fingerprint,
        expires_at=now + timedelta(minutes=5),
        telegram_user_id="user-1",
        telegram_chat_id="chat-1",
        callback_token_hash="a" * 64,
        status=ApprovalStatus.APPROVED,
    )
    return plan, actions, approval


def test_recovery_guard_rejects_non_approved_and_unscoped_authority() -> None:
    _, actions, approval = recovered_fixture()

    assert (
        can_mark_recovered(actions, approval.model_copy(update={"status": ApprovalStatus.PENDING}))
        is False
    )
    assert (
        can_mark_recovered(
            actions,
            approval.model_copy(
                update={"status": ApprovalStatus.DECLINED, "approved_action_ids": []}
            ),
        )
        is False
    )
    assert (
        can_mark_recovered(
            actions, approval.model_copy(update={"approved_action_ids": ["another-action"]})
        )
        is False
    )


def test_recovery_guard_rejects_unverified_required_action_and_conflict() -> None:
    _, actions, approval = recovered_fixture()
    pending = actions.copy()
    pending[-1] = pending[-1].model_copy(update={"execution_status": ActionStatus.SUCCEEDED})
    conflicting = actions.copy()
    conflicting[0] = conflicting[0].model_copy(
        update={"desired_state": {"pickup_at": "2026-08-20T22:00:00Z"}}
    )

    assert can_mark_recovered(pending, approval) is False
    assert deterministic_itinerary_conflicts(conflicting) == (
        "transfer_precedes_replacement_arrival",
    )
    assert can_mark_recovered(conflicting, approval) is False


def test_recovery_guard_accepts_only_fully_verified_consistent_plan() -> None:
    _, actions, approval = recovered_fixture()

    assert deterministic_itinerary_conflicts(actions) == ()
    assert can_mark_recovered(actions, approval)
