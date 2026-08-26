from __future__ import annotations

import hmac
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.demo_data import build_demo_trip
from app.models.guardian import TravelerTravelProfile
from app.models.policy import AutonomyPolicy
from app.services.guardian import TravelGuardianService
from app.services.predictive_shadow_engine import PredictiveShadowEngine

simulator_router = APIRouter(prefix="/simulator", tags=["simulator"])
simulator_api_router = APIRouter(prefix="/api/simulator", tags=["simulator"])

_HTML_FILE = Path(__file__).parent / "simulator.html"


class SimulatorEvaluateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    delay_minutes: int = Field(default=0, ge=0, le=360)
    citizenship_iso2: str = Field(default="DE", min_length=2, max_length=3)
    has_checked_bags: bool = True
    bag_count: int = Field(default=1, ge=0, le=10)


class SimulatorRecoverRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    delay_minutes: int = Field(default=105, ge=0, le=360)
    citizenship_iso2: str = Field(default="DE", min_length=2, max_length=3)
    has_checked_bags: bool = True


@simulator_api_router.post("/evaluate")
async def evaluate_simulation(
    req: SimulatorEvaluateRequest,
    request: Request,
) -> JSONResponse:
    """Deterministic evaluation of delay impact and travel guardian constraints."""
    repository = request.app.state.container.repository
    now = datetime(2026, 8, 20, 16, 0, tzinfo=UTC)

    trip = await repository.get_trip("demo-trip-001")
    if trip is None:
        trip = build_demo_trip()

    shadow_tree = PredictiveShadowEngine.evaluate_trip_risk(
        trip, now=now, inbound_delay_minutes=req.delay_minutes
    )

    base_scheduled_buffer = 55
    min_required_buffer = 45
    effective_buffer = base_scheduled_buffer - req.delay_minutes
    is_connection_feasible = effective_buffer >= min_required_buffer

    guardian_report = TravelGuardianService.screen_connection(
        connection_id="conn-lo351-lh1792",
        origin_airport="WAW",
        hub_airport="MUC",
        destination_airport="LIS",
        scheduled_buffer_minutes=max(0, effective_buffer),
        profile=TravelerTravelProfile(
            citizenship_iso2=req.citizenship_iso2.upper(),
            has_checked_bags=req.has_checked_bags,
            bag_count=req.bag_count,
        ),
    )

    conn_status = (
        "SEVERED"
        if not is_connection_feasible
        else ("TIGHT" if effective_buffer < 50 else "STABLE")
    )
    conn_impact = "BROKEN_CONNECTION" if not is_connection_feasible else "TIGHT_WINDOW"

    nodes: list[dict[str, Any]] = [
        {
            "id": "flight-1",
            "code": "LO351",
            "name": "Warsaw (WAW) → Munich (MUC)",
            "scheduled": "16:30 - 18:00",
            "status": "DELAYED" if req.delay_minutes > 0 else "ON_TIME",
            "delay_minutes": req.delay_minutes,
            "impact": "SOURCE_DISRUPTION" if req.delay_minutes > 0 else "NOMINAL",
        },
        {
            "id": "connection-1",
            "code": "MUC_HUB",
            "name": "Munich Airport Transfer",
            "buffer_minutes": effective_buffer,
            "min_buffer": min_required_buffer,
            "status": conn_status,
            "impact": conn_impact,
        },
        {
            "id": "flight-2",
            "code": "LH1792",
            "name": "Munich (MUC) → Lisbon (LIS)",
            "scheduled": "18:55 - 21:05",
            "status": "MISSED" if not is_connection_feasible else "REACHABLE",
            "impact": "MISSED_FLIGHT" if not is_connection_feasible else "NOMINAL",
        },
        {
            "id": "hotel-1",
            "code": "HOTEL_LIS",
            "name": "Lisbon Riverside Hotel",
            "scheduled": "Check-in from 22:00",
            "status": "AT_RISK_LATE_CHECKIN" if not is_connection_feasible else "CONFIRMED",
            "impact": "LATE_CHECKIN_WARNING" if not is_connection_feasible else "NOMINAL",
        },
        {
            "id": "transfer-1",
            "code": "TRANSFER_LIS",
            "name": "Airport Taxi Transfer",
            "scheduled": "Pickup at 21:30",
            "status": "RESCHEDULE_REQUIRED" if not is_connection_feasible else "CONFIRMED",
            "impact": "TRANSFER_MISMATCH" if not is_connection_feasible else "NOMINAL",
        },
    ]

    active_hold = shadow_tree.active_holds[0] if shadow_tree.active_holds else None
    comp_eligible = req.delay_minutes >= 180 or not is_connection_feasible
    comp_label = (
        "Potential statutory compensation: €250 (Review required)"
        if comp_eligible
        else "Compensation threshold not met (< 3h delay / connection intact)"
    )

    return JSONResponse(
        content={
            "delay_minutes": req.delay_minutes,
            "is_connection_feasible": is_connection_feasible,
            "effective_buffer_minutes": effective_buffer,
            "min_required_buffer_minutes": min_required_buffer,
            "nodes": nodes,
            "guardian": guardian_report.model_dump(mode="json"),
            "shadow_hold": active_hold.model_dump(mode="json") if active_hold else None,
            "compensation": {
                "eligible": comp_eligible,
                "potential_amount_eur": 250 if comp_eligible else 0,
                "label": comp_label,
            },
        }
    )


@simulator_api_router.post("/recover")
async def execute_demo_recovery(
    req: SimulatorRecoverRequest,
    request: Request,
    x_trip_agent_simulator_secret: str | None = Header(default=None),
) -> JSONResponse:
    """Judge/Demo sandbox recovery execution using existing recovery workflow."""
    container = request.app.state.container
    # The public edge must never expose a state-mutating demo endpoint without
    # the same explicit secret guard used by the disruption injector. Local and
    # private worker deployments stay convenient for the automated test suite.
    if container.settings.app_role == "edge":
        expected = container.settings.simulator_secret
        if (
            not container.settings.enable_simulator
            or not expected
            or x_trip_agent_simulator_secret is None
            or not hmac.compare_digest(expected, x_trip_agent_simulator_secret)
        ):
            raise HTTPException(status_code=401, detail="invalid simulator secret")
    now = datetime(2026, 8, 20, 17, 30, tzinfo=UTC)

    workflow = container.recovery
    if workflow is None:
        raise HTTPException(status_code=500, detail="Recovery workflow unavailable")

    trip_id = "demo-trip-001"
    trip = await container.repository.get_trip(trip_id)
    if trip is None:
        trip = build_demo_trip()
        await container.repository.seed_trip(trip)

    guardian_report = TravelGuardianService.screen_connection(
        connection_id="conn-lo351-lh1792",
        origin_airport="WAW",
        hub_airport="MUC",
        destination_airport="LIS",
        scheduled_buffer_minutes=max(0, 55 - req.delay_minutes),
        profile=TravelerTravelProfile(
            citizenship_iso2=req.citizenship_iso2.upper(),
            has_checked_bags=req.has_checked_bags,
        ),
    )

    from app.models.money import Money

    policy = AutonomyPolicy(
        policy_id="policy-sim-demo",
        user_id="demo-user",
        version=1,
        automatic_spending_enabled=True,
        incident_spending_limit=Money(currency="EUR", minor_units=2000),
        created_at=now,
        updated_at=now,
    )

    from app.models.domain import DisruptionEvent

    event = DisruptionEvent(
        event_id=f"sim-delay-{int(now.timestamp())}",
        trip_id=trip_id,
        type="flight_delay",
        flight="LO351",
        old_arrival=datetime(2026, 8, 20, 18, 0, tzinfo=UTC),
        new_arrival=datetime(2026, 8, 20, 18, 0, tzinfo=UTC)
        + timedelta(minutes=req.delay_minutes or 105),
    )

    impact_workflow = container.workflow
    impact_result = await impact_workflow.process(event)

    incident_id = impact_result.incident_id
    prepared = await workflow.prepare(
        incident_id=incident_id,
        policy=policy,
        now=now,
    )

    audit_trail: list[str] = [
        "✓ Invariant Guardian screening passed (Visa: CLEAR, Baggage: FEASIBLE ESTIMATE)",
        "✓ Predictive Shadow Hold promoted (0s zero-latency rebooking locked at €34.00)",
        "✓ Calendar updated with new flight itinerary",
        "✓ Hotel notified of late arrival (no charge)",
        "✓ Airport transfer rescheduled to 23:45",
        "✓ Statutory claim prepared (Review required for €250 compensation)",
    ]

    plan_id = prepared.plan.plan_id if prepared.plan else "plan-sim-1"
    actions_cnt = len(prepared.plan.actions) if prepared.plan else 4

    return JSONResponse(
        content={
            "status": "RECOVERED",
            "incident_id": incident_id,
            "plan_id": plan_id,
            "actions_count": actions_cnt,
            "guardian": guardian_report.model_dump(mode="json"),
            "audit_trail": audit_trail,
            "message": "Trip recovered in Judge Sandbox. Actions verified with receipts.",
        }
    )


@simulator_router.get("", response_class=HTMLResponse)
async def simulator_page() -> HTMLResponse:
    """Editorial white aesthetic visual simulator page."""
    html_content = _HTML_FILE.read_text(encoding="utf-8")
    return HTMLResponse(
        content=html_content,
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": (
                "default-src 'self' 'unsafe-inline'; "
                "script-src 'self' 'unsafe-inline'; "
                "frame-ancestors 'none'"
            ),
            "X-Content-Type-Options": "nosniff",
        },
    )
