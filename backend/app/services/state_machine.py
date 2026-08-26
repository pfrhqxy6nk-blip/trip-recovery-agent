from __future__ import annotations

from app.models.enums import IncidentStatus


class InvalidStateTransition(ValueError):
    pass


_INCIDENT_TRANSITIONS: dict[IncidentStatus, frozenset[IncidentStatus]] = {
    IncidentStatus.RECEIVED: frozenset({IncidentStatus.ANALYZING, IncidentStatus.FAILED}),
    IncidentStatus.ANALYZING: frozenset(
        {IncidentStatus.PLANNING, IncidentStatus.RETRY_SCHEDULED, IncidentStatus.FAILED}
    ),
    IncidentStatus.PLANNING: frozenset(
        {IncidentStatus.NOTIFYING, IncidentStatus.NEEDS_ATTENTION, IncidentStatus.CANCELLED}
    ),
    IncidentStatus.NOTIFYING: frozenset(
        {
            IncidentStatus.EXECUTING_AUTO,
            IncidentStatus.WAITING_APPROVAL,
            IncidentStatus.RETRY_SCHEDULED,
            IncidentStatus.NEEDS_ATTENTION,
        }
    ),
    IncidentStatus.EXECUTING_AUTO: frozenset(
        {
            IncidentStatus.WAITING_APPROVAL,
            IncidentStatus.VERIFYING,
            IncidentStatus.RETRY_SCHEDULED,
            IncidentStatus.NEEDS_ATTENTION,
            IncidentStatus.CANCELLED,
        }
    ),
    IncidentStatus.WAITING_APPROVAL: frozenset(
        {
            IncidentStatus.EXECUTING_APPROVED,
            IncidentStatus.PLANNING,
            IncidentStatus.CANCELLED,
            IncidentStatus.NEEDS_ATTENTION,
        }
    ),
    IncidentStatus.EXECUTING_APPROVED: frozenset(
        {
            IncidentStatus.VERIFYING,
            IncidentStatus.RETRY_SCHEDULED,
            IncidentStatus.NEEDS_ATTENTION,
            IncidentStatus.CANCELLED,
        }
    ),
    IncidentStatus.VERIFYING: frozenset(
        {IncidentStatus.RECOVERED, IncidentStatus.RETRY_SCHEDULED, IncidentStatus.NEEDS_ATTENTION}
    ),
    IncidentStatus.RETRY_SCHEDULED: frozenset(
        {
            IncidentStatus.ANALYZING,
            IncidentStatus.PLANNING,
            IncidentStatus.NOTIFYING,
            IncidentStatus.EXECUTING_AUTO,
            IncidentStatus.EXECUTING_APPROVED,
            IncidentStatus.VERIFYING,
            IncidentStatus.NEEDS_ATTENTION,
        }
    ),
    IncidentStatus.NEEDS_ATTENTION: frozenset({IncidentStatus.PLANNING, IncidentStatus.CANCELLED}),
    IncidentStatus.EXECUTING: frozenset(
        {IncidentStatus.VERIFYING, IncidentStatus.RETRY_SCHEDULED, IncidentStatus.FAILED}
    ),
    IncidentStatus.RECOVERED: frozenset(),
    IncidentStatus.FAILED: frozenset({IncidentStatus.ANALYZING}),
    IncidentStatus.CANCELLED: frozenset({IncidentStatus.PLANNING}),
}


def allowed_incident_transitions(status: IncidentStatus) -> frozenset[IncidentStatus]:
    return _INCIDENT_TRANSITIONS[status]


def assert_incident_transition(current: IncidentStatus, target: IncidentStatus) -> None:
    if target not in allowed_incident_transitions(current):
        raise InvalidStateTransition(f"cannot transition incident from {current} to {target}")
