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


def _bounded_error(error: Exception) -> str:
    """Persist only an operational class, never provider/request details."""

    error_type = type(error).__name__
    code = getattr(error, "code", None)
    if (
        isinstance(code, str)
        and code.isascii()
        and code.isupper()
        and all(character.isalnum() or character == "_" for character in code)
        and 1 <= len(code) <= 80
    ):
        return f"{error_type}:{code}"
    return error_type


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
            analyzing = await self._repository.transition_incident(
                incident_id=incident.incident_id,
                expected_version=incident.version,
                from_states={IncidentStatus.RECEIVED},
                to_state=IncidentStatus.ANALYZING,
                updated_at=now,
            )
            if analyzing is None:
                raise RuntimeError("incident changed before analysis started")
            incident = analyzing

            trip = await self._repository.get_trip(event.trip_id)
            if trip is None:
                raise ValueError(f"trip {event.trip_id!r} was not found")

            impact = self._impact_engine.calculate(event, trip)
            committed = await self._repository.commit_impact(
                incident_id=incident.incident_id,
                expected_version=incident.version,
                impact=impact,
                gemini_model_id=self._interpreter.model_id,
                prompt_version=self._interpreter.prompt_version,
                updated_at=datetime.now(UTC),
            )
            if committed is None:
                raise RuntimeError("incident changed before impact commit")
            incident = committed

            raw_interpretation = await self._interpreter.interpret(event, trip, impact)
            interpretation = TravelInterpretation.model_validate(raw_interpretation)
            completed = await self._repository.complete_analysis(
                event_id=event.event_id,
                incident_id=incident.incident_id,
                expected_version=incident.version,
                interpretation=interpretation,
                completed_at=datetime.now(UTC),
            )
            if completed is None:
                raise RuntimeError("incident changed before analysis completion")
            incident = completed
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
            # The durable incident is operational state, not a traceback sink.
            # Provider messages may contain URLs, credentials, or traveler data.
            error = _bounded_error(exc)
            latest = await self._repository.get_incident(incident.incident_id)
            if latest is not None:
                latest.status = IncidentStatus.FAILED
                latest.last_error = error
                latest.lease_owner = None
                latest.lease_expires_at = None
                await self._repository.mark_event_retryable(
                    event.event_id, latest, error, datetime.now(UTC)
                )
            logger.error(
                "IMPACT_ANALYSIS_FAILED",
                extra={
                    "incident_id": incident.incident_id,
                    "correlation_id": correlation_id,
                    "error_code": error,
                },
            )
            raise WorkflowProcessingError(error) from None
