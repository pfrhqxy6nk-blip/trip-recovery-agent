from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid4, uuid5

from pydantic import BaseModel

from app.models.domain import DisruptionEvent, Incident, TravelInterpretation
from app.models.enums import ClaimKind, IncidentStatus
from app.services.impact import DeterministicImpactEngine
from app.services.ports import IncidentRepository, TravelInterpreter

logger = logging.getLogger(__name__)


class WorkflowProcessingError(RuntimeError):
    pass


class ProcessOutcome(BaseModel):
    event_id: str
    incident_id: str
    claim: ClaimKind
    processed: bool
    incident_status: IncidentStatus


class ImpactAnalysisWorkflow:
    def __init__(
        self,
        repository: IncidentRepository,
        interpreter: TravelInterpreter,
        *,
        lease_seconds: int = 60,
        impact_engine: DeterministicImpactEngine | None = None,
    ) -> None:
        self._repository = repository
        self._interpreter = interpreter
        self._lease_seconds = lease_seconds
        self._impact_engine = impact_engine or DeterministicImpactEngine()

    @staticmethod
    def stable_incident_id(event_id: str) -> str:
        digest = hashlib.sha256(event_id.encode("utf-8")).hexdigest()[:24]
        return f"incident-{digest}"

    async def process(self, event: DisruptionEvent) -> ProcessOutcome:
        now = datetime.now(UTC)
        incident_id = self.stable_incident_id(event.event_id)
        correlation_id = str(uuid5(NAMESPACE_URL, f"trip-recovery:{event.event_id}"))
        worker_id = str(uuid4())
        draft = Incident(
            incident_id=incident_id,
            trip_id=event.trip_id,
            external_event_id=event.event_id,
            correlation_id=correlation_id,
            trigger=event,
            updated_at=now,
            lease_owner=worker_id,
            lease_expires_at=now + timedelta(seconds=self._lease_seconds),
        )
        claim = await self._repository.claim_event(
            event=event,
            incident=draft,
            worker_id=worker_id,
            lease_expires_at=now + timedelta(seconds=self._lease_seconds),
        )
        if not claim.acquired:
            existing = await self._repository.get_incident(claim.incident_id)
            status = existing.status if existing else IncidentStatus.RECEIVED
            return ProcessOutcome(
                event_id=event.event_id,
                incident_id=claim.incident_id,
                claim=claim.kind,
                processed=False,
                incident_status=status,
            )

        incident = await self._repository.get_incident(claim.incident_id)
        if incident is None:
            raise WorkflowProcessingError("claimed incident is missing")

        try:
            logger.info(
                "IMPACT_ANALYSIS_STARTED",
                extra={"incident_id": incident.incident_id, "correlation_id": correlation_id},
            )
            incident.status = IncidentStatus.ANALYZING
            incident.analysis_started_at = now
            self._touch(incident)
            await self._repository.save_incident(incident)

            trip = await self._repository.get_trip(event.trip_id)
            if trip is None:
                raise ValueError(f"trip {event.trip_id!r} was not found")

            impact = self._impact_engine.calculate(event, trip)
            incident.deterministic_impact = impact
            incident.gemini_model_id = self._interpreter.model_id
            incident.prompt_version = self._interpreter.prompt_version
            self._touch(incident)
            await self._repository.save_incident(incident)

            raw_interpretation = await self._interpreter.interpret(event, trip, impact)
            interpretation = TravelInterpretation.model_validate(raw_interpretation)
            incident.interpretation = interpretation
            incident.status = IncidentStatus.PLANNING
            incident.analysis_completed_at = datetime.now(UTC)
            incident.last_error = None
            incident.lease_owner = None
            incident.lease_expires_at = None
            self._touch(incident)
            await self._repository.save_incident(incident)
            await self._repository.mark_event_completed(event.event_id, incident.updated_at)
            logger.info(
                "IMPACT_ANALYSIS_COMPLETED",
                extra={"incident_id": incident.incident_id, "correlation_id": correlation_id},
            )
            return ProcessOutcome(
                event_id=event.event_id,
                incident_id=incident.incident_id,
                claim=claim.kind,
                processed=True,
                incident_status=incident.status,
            )
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            incident.status = IncidentStatus.FAILED
            incident.last_error = error
            incident.lease_owner = None
            incident.lease_expires_at = None
            self._touch(incident)
            await self._repository.mark_event_retryable(
                event.event_id, incident, error, incident.updated_at
            )
            logger.exception(
                "IMPACT_ANALYSIS_FAILED",
                extra={"incident_id": incident.incident_id, "correlation_id": correlation_id},
            )
            raise WorkflowProcessingError(error) from exc

    @staticmethod
    def _touch(incident: Incident) -> None:
        incident.version += 1
        incident.updated_at = datetime.now(UTC)
