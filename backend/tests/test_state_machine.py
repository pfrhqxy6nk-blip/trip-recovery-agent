import pytest
from app.models.enums import IncidentStatus
from app.services.state_machine import (
    InvalidStateTransition,
    assert_incident_transition,
)


def test_happy_path_transitions_are_explicit() -> None:
    assert_incident_transition(IncidentStatus.PLANNING, IncidentStatus.NOTIFYING)
    assert_incident_transition(IncidentStatus.NOTIFYING, IncidentStatus.EXECUTING_AUTO)
    assert_incident_transition(IncidentStatus.EXECUTING_AUTO, IncidentStatus.WAITING_APPROVAL)
    assert_incident_transition(IncidentStatus.EXECUTING_APPROVED, IncidentStatus.VERIFYING)
    assert_incident_transition(IncidentStatus.VERIFYING, IncidentStatus.RECOVERED)


def test_illegal_transition_is_rejected() -> None:
    with pytest.raises(InvalidStateTransition, match="cannot transition"):
        assert_incident_transition(IncidentStatus.PLANNING, IncidentStatus.RECOVERED)


def test_recovered_incident_has_no_forward_transition() -> None:
    with pytest.raises(InvalidStateTransition):
        assert_incident_transition(IncidentStatus.RECOVERED, IncidentStatus.PLANNING)
