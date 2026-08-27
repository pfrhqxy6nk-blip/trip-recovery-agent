from __future__ import annotations

from collections.abc import Collection
from datetime import datetime

from app.models.enums import ActionCategory, ActionStatus, ApprovalStatus, PolicyVerdict
from app.models.recovery import ApprovalRequest, PlannedAction


def _current_actions(
    actions: list[PlannedAction], current_plan_version: int | None
) -> list[PlannedAction]:
    if not actions:
        return []
    version = current_plan_version or max(action.plan_version for action in actions)
    return [
        action
        for action in actions
        if action.plan_version == version and action.execution_status != ActionStatus.SUPERSEDED
    ]


def all_required_actions_verified(
    actions: list[PlannedAction], *, current_plan_version: int | None = None
) -> bool:
    current = _current_actions(actions, current_plan_version)
    if not current:
        return False
    by_id = {action.action_id: action for action in current}
    return all(
        action.execution_status == ActionStatus.VERIFIED
        and action.provider_reference is not None
        and all(
            prerequisite_id in by_id
            and by_id[prerequisite_id].execution_status == ActionStatus.VERIFIED
            for prerequisite_id in action.prerequisites
        )
        for action in current
    )


def deterministic_itinerary_conflicts(
    actions: list[PlannedAction], *, current_plan_version: int | None = None
) -> tuple[str, ...]:
    """Validate the canonical post-action timeline without trusting provider success text."""

    current = _current_actions(actions, current_plan_version)
    flight_actions = [
        action for action in current if action.category == ActionCategory.FLIGHT_RECOVERY
    ]
    if len(flight_actions) != 1:
        return ("replacement_flight_missing_or_ambiguous",)
    arrival = _parse_datetime(flight_actions[0].desired_state.get("arrival_at"))
    if arrival is None:
        return ("replacement_arrival_missing",)

    conflicts: list[str] = []
    for action in current:
        if action.category == ActionCategory.TRANSFER or action.provider == "demo-transfer":
            pickup = _parse_datetime(action.desired_state.get("pickup_at"))
            if pickup is None or pickup < arrival:
                conflicts.append("transfer_precedes_replacement_arrival")
        elif action.provider == "demo-hotel":
            expected_arrival = _parse_datetime(action.desired_state.get("expected_arrival_at"))
            if expected_arrival is None or expected_arrival < arrival:
                conflicts.append("hotel_notice_precedes_replacement_arrival")
        elif action.category == ActionCategory.CALENDAR:
            calendar_arrival = _parse_datetime(action.desired_state.get("arrival_at"))
            if calendar_arrival != arrival:
                conflicts.append("calendar_arrival_mismatch")
    return tuple(conflicts)


def can_mark_recovered(
    actions: list[PlannedAction],
    approval: ApprovalRequest | None,
    *,
    current_plan_version: int | None = None,
    deterministic_conflicts: Collection[str] = (),
) -> bool:
    """The terminal success guard; workflows must call this before final messaging."""

    if deterministic_conflicts:
        return False
    current = _current_actions(actions, current_plan_version)
    if not current:
        return False
    approval_required_ids = {
        action.action_id
        for action in current
        if action.policy_decision is not None
        and action.policy_decision.verdict == PolicyVerdict.APPROVAL_REQUIRED
    }
    if approval_required_ids:
        if approval is None or approval.status != ApprovalStatus.APPROVED:
            return False
        if approval.plan_version != current[0].plan_version:
            return False
        # Empty scope is accepted only as schema-v1 compatibility. New workflow-created
        # approvals always persist an explicit scope and are checked strictly.
        if approval.approved_action_ids and not approval_required_ids.issubset(
            set(approval.approved_action_ids)
        ):
            return False
    elif approval is not None and approval.status != ApprovalStatus.APPROVED:
        return False
    return all_required_actions_verified(
        current, current_plan_version=current[0].plan_version
    ) and not deterministic_itinerary_conflicts(
        current, current_plan_version=current[0].plan_version
    )


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
