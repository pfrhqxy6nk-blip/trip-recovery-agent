from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from app.demo_data import build_demo_trip
from app.models.domain import Incident
from app.models.enums import ClaimKind, IncidentStatus
from app.services.memory import InMemoryIncidentRepository
from app.workflows.impact_analysis import ImpactAnalysisWorkflow, WorkflowProcessingError

from tests.helpers import InvalidInterpreter, ValidInterpreter, disruption_event


async def test_atomic_claim_has_one_winner() -> None:
    repository = InMemoryIncidentRepository()
    event = disruption_event()
    now = datetime.now(UTC)
    incident_id = ImpactAnalysisWorkflow.stable_incident_id(event.event_id)

    def draft() -> Incident:
        return Incident(
            incident_id=incident_id,
            trip_id=event.trip_id,
            external_event_id=event.event_id,
            correlation_id="correlation-test",
            trigger=event,
            updated_at=now,
        )

    results = await asyncio.gather(
        repository.claim_event(
            event=event,
            incident=draft(),
            worker_id="worker-a",
            lease_expires_at=now + timedelta(seconds=60),
        ),
        repository.claim_event(
            event=event,
            incident=draft(),
            worker_id="worker-b",
            lease_expires_at=now + timedelta(seconds=60),
        ),
    )

    assert [result.kind for result in results].count(ClaimKind.NEW) == 1
    assert [result.kind for result in results].count(ClaimKind.IN_PROGRESS) == 1
    assert len(repository.processed_events) == 1
    assert len(repository.incidents) == 1


async def test_concurrent_duplicate_delivery_creates_one_incident() -> None:
    repository = InMemoryIncidentRepository()
    await repository.seed_trip(build_demo_trip())
    interpreter = ValidInterpreter(delay=0.03)
    workflow = ImpactAnalysisWorkflow(repository, interpreter)
    event = disruption_event()

    outcomes = await asyncio.gather(workflow.process(event), workflow.process(event))

    assert sum(outcome.processed for outcome in outcomes) == 1
    assert {outcome.incident_id for outcome in outcomes} == {
        ImpactAnalysisWorkflow.stable_incident_id(event.event_id)
    }
    assert interpreter.calls == 1
    assert len(repository.processed_events) == 1
    assert len(repository.incidents) == 1
    incident = next(iter(repository.incidents.values()))
    assert incident.status == IncidentStatus.PLANNING
    assert incident.deterministic_impact is not None
    assert incident.deterministic_impact.connection_feasible is False
    assert incident.interpretation is not None
    assert incident.correlation_id
    assert incident.gemini_model_id == "gemini-test-model"
    assert incident.prompt_version == "impact-interpretation-test-v1"
    assert incident.analysis_started_at is not None
    assert incident.analysis_completed_at is not None


async def test_invalid_gemini_output_cannot_corrupt_deterministic_state() -> None:
    repository = InMemoryIncidentRepository()
    await repository.seed_trip(build_demo_trip())
    workflow = ImpactAnalysisWorkflow(repository, InvalidInterpreter())
    event = disruption_event(event_id="invalid-gemini-output")

    with pytest.raises(WorkflowProcessingError):
        await workflow.process(event)

    incident = next(iter(repository.incidents.values()))
    assert incident.status == IncidentStatus.FAILED
    assert incident.interpretation is None
    assert incident.deterministic_impact is not None
    assert incident.deterministic_impact.connection_feasible is False
    assert "ValidationError" in (incident.last_error or "")


async def test_retry_after_restart_resumes_same_incident() -> None:
    repository = InMemoryIncidentRepository()
    await repository.seed_trip(build_demo_trip())
    event = disruption_event(event_id="restart-safe-event")
    first_process = ImpactAnalysisWorkflow(repository, InvalidInterpreter())

    with pytest.raises(WorkflowProcessingError):
        await first_process.process(event)

    original_incident_id = next(iter(repository.incidents))
    restarted_process = ImpactAnalysisWorkflow(repository, ValidInterpreter())
    outcome = await restarted_process.process(event)

    assert outcome.claim == ClaimKind.RESUMED
    assert outcome.incident_id == original_incident_id
    assert len(repository.incidents) == 1
    incident = repository.incidents[original_incident_id]
    assert incident.status == IncidentStatus.PLANNING
    assert incident.retry_count == 1
    assert incident.interpretation is not None


async def test_completed_event_is_acknowledged_without_reprocessing() -> None:
    repository = InMemoryIncidentRepository()
    await repository.seed_trip(build_demo_trip())
    interpreter = ValidInterpreter()
    workflow = ImpactAnalysisWorkflow(repository, interpreter)
    event = disruption_event(event_id="completed-duplicate")

    first = await workflow.process(event)
    second = await workflow.process(event)

    assert first.processed is True
    assert second.processed is False
    assert second.claim == ClaimKind.COMPLETED
    assert interpreter.calls == 1
    assert len(repository.incidents) == 1
