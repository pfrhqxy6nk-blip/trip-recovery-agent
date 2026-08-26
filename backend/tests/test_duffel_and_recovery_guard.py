from datetime import UTC, datetime

import httpx
import pytest
from app.demo_data import build_owned_demo_trip
from app.models.domain import DisruptionEvent, Incident
from app.models.enums import IncidentStatus
from app.models.money import Money
from app.models.policy import AutonomyPolicy
from app.providers.duffel import DuffelFlightQuoteClient
from app.providers.guarded_demo import JudgeOnlyDemoProvider
from app.services.impact import DeterministicImpactEngine
from app.services.memory import InMemoryIncidentRepository
from app.workflows.impact_analysis import ImpactAnalysisWorkflow
from app.workflows.recovery import RecoveryWorkflow

from tests.helpers import ValidInterpreter


def _policy() -> AutonomyPolicy:
    now = datetime(2026, 8, 20, 12, tzinfo=UTC)
    return AutonomyPolicy(
        policy_id="policy-real-1",
        user_id="telegram:real-user",
        version=1,
        automatic_spending_enabled=True,
        incident_spending_limit=Money(currency="EUR", minor_units=5_000),
        created_at=now,
        updated_at=now,
    )


def _duffel_response() -> dict[str, object]:
    return {
        "data": {
            "offers": [
                {
                    "id": "off_expensive",
                    "total_amount": {"amount": "99.00", "currency": "eur"},
                    "slices": [{"segments": [{"arriving_at": "2026-08-20T23:45:00Z"}]}],
                },
                {
                    "id": "off_cheap",
                    "total_amount": {"amount": "34.00", "currency": "eur"},
                    "expires_at": "2026-08-20T12:12:00Z",
                    "slices": [{"segments": [{"arriving_at": "2026-08-20T23:15:00Z"}]}],
                },
            ]
        }
    }


@pytest.mark.asyncio
async def test_duffel_quote_selects_cheapest_expiring_option_without_booking() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_duffel_response())

    client = DuffelFlightQuoteClient(
        access_token="duffel_test_token",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    trip = build_owned_demo_trip(owner_user_id="telegram:real-user", trip_id="real-trip")
    event = DisruptionEvent(
        event_id="real-delay",
        trip_id=trip.trip_id,
        type="flight_delay",
        flight="LO351",
        old_arrival=datetime(2026, 8, 20, 18, tzinfo=UTC),
        new_arrival=datetime(2026, 8, 20, 19, 45, tzinfo=UTC),
    )
    incident = Incident(
        incident_id="real-incident",
        trip_id=trip.trip_id,
        external_event_id=event.event_id,
        correlation_id="real-correlation",
        trigger=event,
        deterministic_impact=DeterministicImpactEngine().calculate(event, trip),
    )

    option = await client.search_recovery_option(
        trip=trip, incident=incident, now=datetime(2026, 8, 20, 12, tzinfo=UTC)
    )

    assert option.provider == "duffel"
    assert option.provider_option_id == "off_cheap"
    assert option.incremental_cost == Money(currency="EUR", minor_units=3_400)
    assert option.reversible is False
    assert len(requests) == 1
    assert requests[0].url.path == "/air/offer_requests"
    assert requests[0].headers["Duffel-Version"] == "v2"


@pytest.mark.asyncio
async def test_main_demo_provider_cannot_mark_real_incident_recovered() -> None:
    repository = InMemoryIncidentRepository()
    trip = build_owned_demo_trip(owner_user_id="telegram:real-user", trip_id="real-trip")
    await repository.seed_trip(trip)
    event = DisruptionEvent(
        event_id="real-delay-guard",
        trip_id=trip.trip_id,
        type="flight_delay",
        flight="LO351",
        old_arrival=datetime(2026, 8, 20, 18, tzinfo=UTC),
        new_arrival=datetime(2026, 8, 20, 19, 45, tzinfo=UTC),
    )
    outcome = await ImpactAnalysisWorkflow(repository, ValidInterpreter()).process(event)
    workflow = RecoveryWorkflow(repository, provider=JudgeOnlyDemoProvider(repository))

    result = await workflow.start(
        incident_id=outcome.incident_id,
        policy=_policy(),
        telegram_user_id="real-user",
        telegram_chat_id="real-chat",
        now=datetime(2026, 8, 20, 12, tzinfo=UTC),
    )

    assert result.incident_status == IncidentStatus.NEEDS_ATTENTION
    assert not repository.effects
