from __future__ import annotations

import hmac
import logging
from typing import cast

from fastapi import APIRouter, Header, HTTPException, Request, Response, status
from pydantic import BaseModel

from app.models.domain import DisruptionEvent
from app.models.enums import ClaimKind, IncidentStatus
from app.models.pubsub import PubSubEnvelope
from app.services.onboarding import OnboardingError
from app.services.telegram_delivery import DurableTelegramDelivery
from app.services.trip_watch_notifications import TripWatchSignalNotifier
from app.workflows.impact_analysis import ProcessOutcome, WorkflowProcessingError
from app.workflows.recovery import RecoveryWorkflowError

router = APIRouter()
logger = logging.getLogger(__name__)


def _bounded_error_code(error: Exception) -> str:
    code = getattr(error, "code", None)
    if (
        isinstance(code, str)
        and code.isascii()
        and code.isupper()
        and all(character.isalnum() or character == "_" for character in code)
        and 1 <= len(code) <= 80
    ):
        return code
    return "PROVIDER_ERROR"


async def dispatch_pending_workflow_commands(request: Request) -> None:
    """Best-effort immediate delivery; the durable record remains retryable."""

    dispatcher = request.app.state.container.outbox_dispatcher
    if dispatcher is None:
        return
    try:
        await dispatcher.dispatch_pending(now=request.app.state.container.clock(), limit=20)
    except Exception as error:
        # Approval has already been committed atomically. A later scheduler/outbox
        # sweep can safely republish the same command if this fast path is down.
        # Keep provider/Telegram details out of logs; the durable outbox is the
        # source of truth for retry and the bounded code is enough for operations.
        logger.error(
            "WORKFLOW_OUTBOX_DISPATCH_FAILED",
            extra={"provider": "workflow_outbox", "error_code": _bounded_error_code(error)},
        )


async def _advance_recovery_for_owner(incident_id: str, request: Request) -> None:
    container = request.app.state.container
    if (
        container.recovery is None
        or container.telegram_recovery is None
        or container.telegram_gateway is None
        or container.onboarding is None
    ):
        return
    incident = await container.repository.get_incident(incident_id)
    if incident is None:
        return
    trip = await container.repository.get_trip(incident.trip_id)
    if trip is None or trip.owner_user_id is None:
        return
    prefix = "telegram:"
    if not trip.owner_user_id.startswith(prefix):
        return
    telegram_user_id = trip.owner_user_id.removeprefix(prefix)
    traveler = await container.repository.get_traveler(telegram_user_id)
    if traveler is None or traveler.user_id != trip.owner_user_id:
        return
    if traveler.active_policy_version is None:
        return
    policy = await container.repository.get_traveler_policy(
        user_id=traveler.user_id,
        version=traveler.active_policy_version,
    )
    if policy is None:
        try:
            policy = container.onboarding.policy(traveler)
        except OnboardingError:
            return

    now = container.clock()
    delivery = DurableTelegramDelivery(container.repository, container.telegram_gateway)
    if incident.status == IncidentStatus.PLANNING:
        prepared = await container.recovery.prepare(
            incident_id=incident_id,
            policy=policy,
            now=now,
        )
        plan = prepared.plan
    else:
        plan = await container.repository.get_current_plan(incident_id)
        if plan is None:
            return

    current = await container.repository.get_incident(incident_id)
    if current is None:
        return
    notification_delivered = current.status != IncidentStatus.NOTIFYING
    if current.status == IncidentStatus.NOTIFYING:
        notification_delivered = await delivery.send_once(
            incident_id=incident_id,
            kind="AWARENESS",
            dedupe_key=plan.plan_hash,
            chat_id=traveler.telegram_chat_id,
            view=container.telegram_recovery.awareness_view(plan),
            now=now,
        )

    result = await container.recovery.continue_plan(
        plan=plan,
        policy=policy,
        telegram_user_id=traveler.telegram_user_id,
        telegram_chat_id=traveler.telegram_chat_id,
        now=now,
        notification_delivered=notification_delivered,
    )
    if result.approval is not None and result.approval_callback_token is not None:
        await delivery.send_once(
            incident_id=incident_id,
            kind="APPROVAL",
            dedupe_key=result.approval.approval_id,
            chat_id=traveler.telegram_chat_id,
            view=await container.telegram_recovery.approval_view(result),
            now=now,
        )
    elif result.incident_status in {IncidentStatus.RECOVERED, IncidentStatus.NEEDS_ATTENTION}:
        await delivery.send_once(
            incident_id=incident_id,
            kind="FINAL",
            dedupe_key=f"{plan.plan_hash}:{result.incident_status.value}",
            chat_id=traveler.telegram_chat_id,
            view=await container.telegram_recovery.status_view(
                incident_id,
                result.incident_status,
                telegram_user_id=traveler.telegram_user_id,
                telegram_chat_id=traveler.telegram_chat_id,
            ),
            now=now,
        )


class PublishResponse(BaseModel):
    event_id: str
    message_id: str
    processing: ProcessOutcome | None = None


class WatchTickResponse(BaseModel):
    checked: int
    recorded_signals: int
    published_events: int
    failed_watchpoints: int = 0


@router.post(
    "/simulate-disruption", response_model=PublishResponse, status_code=status.HTTP_202_ACCEPTED
)
async def simulate_disruption(
    event: DisruptionEvent,
    request: Request,
    x_trip_agent_simulator_secret: str | None = Header(default=None),
) -> PublishResponse:
    container = request.app.state.container
    if not container.settings.enable_simulator:
        raise HTTPException(status_code=404, detail="simulator is disabled")
    if x_trip_agent_simulator_secret is None or not hmac.compare_digest(
        container.settings.simulator_secret, x_trip_agent_simulator_secret
    ):
        raise HTTPException(status_code=401, detail="invalid simulator secret")
    message_id = await container.publisher.publish(event)
    processing = None
    if container.settings.process_events_inline:
        processing = await container.workflow.process(event)
        await _advance_recovery_for_owner(processing.incident_id, request)
    return PublishResponse(event_id=event.event_id, message_id=message_id, processing=processing)


@router.post("/internal/pubsub/disruptions")
async def consume_disruption(envelope: PubSubEnvelope, request: Request) -> ProcessOutcome:
    try:
        event = envelope.message.decode_event()
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        outcome = cast(ProcessOutcome, await request.app.state.container.workflow.process(event))
        if outcome.claim == ClaimKind.IN_PROGRESS:
            # Do not ACK a delivery while another lease is active. If that worker dies,
            # Pub/Sub must redeliver after the lease expires so the incident can resume.
            raise HTTPException(status_code=409, detail="event is already being processed")
        await _advance_recovery_for_owner(outcome.incident_id, request)
        return outcome
    except WorkflowProcessingError as exc:
        # Non-2xx tells Pub/Sub to redeliver. The retry resumes the stable incident.
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/internal/pubsub/commands")
async def consume_workflow_command(envelope: PubSubEnvelope, request: Request) -> dict[str, str]:
    """Resume a persisted recovery command after an approval or retry boundary."""

    try:
        command = envelope.message.decode_command()
        recovery = request.app.state.container.recovery
        if recovery is None:
            raise RecoveryWorkflowError("recovery workflow is unavailable")
        status_value = await recovery.process_command(
            command=command,
            worker_id=f"pubsub-command:{envelope.message.message_id or command.command_id}",
            now=request.app.state.container.clock(),
        )
        await _advance_recovery_for_owner(command.incident_id, request)
        return {"command_id": command.command_id, "status": status_value.value}
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RecoveryWorkflowError as exc:
        # Pub/Sub must redeliver while a command cannot be completed.
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/internal/watch/tick", response_model=WatchTickResponse)
async def run_watch_tick(request: Request) -> WatchTickResponse:
    container = request.app.state.container
    workflow = container.trip_watch
    if workflow is None:
        raise HTTPException(status_code=404, detail="Trip Watch is disabled")
    # The Telegram approval path dispatches immediately, but this scheduler tick
    # is the durable retry boundary when the worker or Pub/Sub is temporarily down.
    await dispatch_pending_workflow_commands(request)
    watchpoints = await container.repository.list_due_watchpoints(
        container.clock(), limit=container.settings.trip_watch_max_checks_per_tick
    )
    recorded = 0
    published = 0
    failed = 0
    watch_notifier = (
        TripWatchSignalNotifier(container.repository, container.telegram_gateway)
        if container.telegram_gateway is not None
        else None
    )
    # First flush facts recorded by a previous tick whose Pub/Sub publish was
    # interrupted. This makes the watcher durable across transient outages.
    try:
        published += await workflow.publish_pending_events(
            limit=container.settings.trip_watch_max_checks_per_tick,
            notifier=watch_notifier,
        )
    except Exception as error:
        failed += 1
        logger.error(
            "TRIP_WATCH_PENDING_FLUSH_FAILED",
            extra={"provider": "watch_outbox", "error_code": _bounded_error_code(error)},
        )
    for watchpoint in watchpoints:
        try:
            signal = await workflow.run_watchpoint(watchpoint)
        except Exception as error:
            failed += 1
            logger.error(
                "TRIP_WATCHPOINT_FAILED",
                extra={
                    "watchpoint_id": watchpoint.watchpoint_id,
                    "provider": "google_search",
                    "error_code": _bounded_error_code(error),
                },
            )
            continue
        if signal is None:
            continue
        recorded += 1
        try:
            if await workflow.publish_recovery_event(
                watchpoint=watchpoint,
                signal=signal,
                notifier=watch_notifier,
            ):
                published += 1
        except Exception as error:
            failed += 1
            logger.error(
                "TRIP_WATCHPOINT_PUBLISH_FAILED",
                extra={
                    "watchpoint_id": watchpoint.watchpoint_id,
                    "provider": "pubsub",
                    "error_code": _bounded_error_code(error),
                },
            )
    return WatchTickResponse(
        checked=len(watchpoints),
        recorded_signals=recorded,
        published_events=published,
        failed_watchpoints=failed,
    )


@router.get("/internal/incidents/{incident_id}")
async def get_incident(incident_id: str, request: Request) -> Response:
    incident = await request.app.state.container.repository.get_incident(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="incident not found")
    return Response(content=incident.model_dump_json(), media_type="application/json")


@router.get("/internal/incidents/{incident_id}/compensation")
async def get_incident_compensation(incident_id: str, request: Request) -> Response:
    from app.services.compensation import PassengerCompensationService

    repository = request.app.state.container.repository
    incident = await repository.get_incident(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="incident not found")
    trip = await repository.get_trip(incident.trip_id)
    assessment, _ = PassengerCompensationService.assess_incident(incident, trip)
    return Response(content=assessment.model_dump_json(), media_type="application/json")


@router.get("/internal/incidents/{incident_id}/claim-letter")
async def get_incident_claim_letter(
    incident_id: str, request: Request, passenger_name: str = "Traveler"
) -> Response:
    from app.services.compensation import PassengerCompensationService

    repository = request.app.state.container.repository
    incident = await repository.get_incident(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="incident not found")
    trip = await repository.get_trip(incident.trip_id)
    _, claim_letter = PassengerCompensationService.assess_incident(
        incident, trip, passenger_name=passenger_name
    )
    if claim_letter is None:
        raise HTTPException(
            status_code=400,
            detail="Incident is not eligible for statutory cash compensation claim",
        )
    return Response(content=claim_letter.model_dump_json(), media_type="application/json")


@router.get("/internal/trips/{trip_id}/shadow-tree")
async def get_trip_shadow_tree(trip_id: str, request: Request) -> Response:
    from app.services.predictive_shadow_engine import PredictiveShadowEngine

    repository = request.app.state.container.repository
    trip = await repository.get_trip(trip_id)
    if trip is None:
        raise HTTPException(status_code=404, detail="trip not found")
    now = request.app.state.container.clock()
    tree = PredictiveShadowEngine.evaluate_trip_risk(trip, now=now)
    return Response(content=tree.model_dump_json(), media_type="application/json")


@router.post("/internal/trips/{trip_id}/predictive-scan")
async def trigger_predictive_scan(
    trip_id: str, request: Request, inbound_delay_minutes: int = 0
) -> Response:
    from app.services.predictive_shadow_engine import PredictiveShadowEngine

    repository = request.app.state.container.repository
    trip = await repository.get_trip(trip_id)
    if trip is None:
        raise HTTPException(status_code=404, detail="trip not found")
    now = request.app.state.container.clock()
    tree = PredictiveShadowEngine.evaluate_trip_risk(
        trip, now=now, inbound_delay_minutes=inbound_delay_minutes
    )
    return Response(content=tree.model_dump_json(), media_type="application/json")
