from __future__ import annotations

from typing import cast

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel

from app.models.domain import DisruptionEvent
from app.models.enums import ClaimKind
from app.models.pubsub import PubSubEnvelope
from app.workflows.impact_analysis import ProcessOutcome, WorkflowProcessingError

router = APIRouter()


class PublishResponse(BaseModel):
    event_id: str
    message_id: str
    processing: ProcessOutcome | None = None


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.post(
    "/simulate-disruption", response_model=PublishResponse, status_code=status.HTTP_202_ACCEPTED
)
async def simulate_disruption(event: DisruptionEvent, request: Request) -> PublishResponse:
    container = request.app.state.container
    message_id = await container.publisher.publish(event)
    processing = None
    if container.settings.process_events_inline:
        processing = await container.workflow.process(event)
    return PublishResponse(
        event_id=event.event_id, message_id=message_id, processing=processing
    )


@router.post("/internal/pubsub/disruptions")
async def consume_disruption(envelope: PubSubEnvelope, request: Request) -> ProcessOutcome:
    try:
        event = envelope.message.decode_event()
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        outcome = cast(
            ProcessOutcome, await request.app.state.container.workflow.process(event)
        )
        if outcome.claim == ClaimKind.IN_PROGRESS:
            # Do not ACK a delivery while another lease is active. If that worker dies,
            # Pub/Sub must redeliver after the lease expires so the incident can resume.
            raise HTTPException(status_code=409, detail="event is already being processed")
        return outcome
    except WorkflowProcessingError as exc:
        # Non-2xx tells Pub/Sub to redeliver. The retry resumes the stable incident.
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/internal/incidents/{incident_id}")
async def get_incident(incident_id: str, request: Request) -> Response:
    incident = await request.app.state.container.repository.get_incident(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="incident not found")
    return Response(content=incident.model_dump_json(), media_type="application/json")
