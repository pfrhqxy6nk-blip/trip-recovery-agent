import asyncio
import base64
from datetime import UTC, datetime

from app.config import Settings
from app.demo_data import build_demo_trip
from app.main import AppContainer, create_app
from app.models.domain import DisruptionEvent
from app.models.watch import GroundedTravelSignal, SourceTrust, TripWatchpoint
from app.services.memory import InMemoryIncidentRepository, LocalEventPublisher
from app.services.trip_watch_workflow import TripWatchWorkflow
from app.workflows.impact_analysis import ImpactAnalysisWorkflow
from httpx import ASGITransport, AsyncClient

from tests.helpers import ValidInterpreter, disruption_event


class OfficialDelayGrounder:
    async def observe(self, watchpoint: TripWatchpoint) -> GroundedTravelSignal:
        return GroundedTravelSignal(
            watchpoint_id=watchpoint.watchpoint_id,
            summary="Official delay",
            source_url="https://airline.example/status",
            source_title="Airline status",
            trust=SourceTrust.OFFICIAL,
            observed_at=datetime(2026, 8, 20, 12, tzinfo=UTC),
            affects_trip=True,
            suggested_event_type="FLIGHT_ARRIVAL_DELAY",
            observed_flight="LO351",
            old_arrival=datetime(2026, 8, 20, 18, tzinfo=UTC),
            new_arrival=datetime(2026, 8, 20, 19, 45, tzinfo=UTC),
        )


class FlakyPublisher(LocalEventPublisher):
    def __init__(self) -> None:
        super().__init__()
        self.fail_once = True

    async def publish(self, event: DisruptionEvent) -> str:
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError("transient Pub/Sub outage")
        return await super().publish(event)


async def test_simulator_is_not_public_by_default() -> None:
    settings = Settings(pubsub_transport="local")
    repository = InMemoryIncidentRepository()
    container = AppContainer(
        settings,
        repository,
        LocalEventPublisher(),
        ImpactAnalysisWorkflow(repository, ValidInterpreter()),
    )
    app = create_app(settings, container=container)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/simulate-disruption",
                json=disruption_event(event_id="forbidden-simulation").model_dump(mode="json"),
            )

    assert response.status_code == 404


async def test_private_worker_loads_internal_routes_without_public_connection_page() -> None:
    repository = InMemoryIncidentRepository()
    publisher = LocalEventPublisher()
    workflow = ImpactAnalysisWorkflow(repository, ValidInterpreter())
    worker_settings = Settings(pubsub_transport="local", app_role="worker")
    worker = create_app(
        worker_settings,
        container=AppContainer(worker_settings, repository, publisher, workflow),
    )
    async with AsyncClient(
        transport=ASGITransport(app=worker), base_url="http://worker"
    ) as worker_client:
        assert (await worker_client.get("/healthz")).status_code == 200
        assert (await worker_client.get("/connections/gemini")).status_code == 404
        private_connection = await worker_client.post("/connections/gemini/complete", json={})
        assert private_connection.status_code == 422
        internal = await worker_client.post("/internal/pubsub/disruptions", json={})
        assert internal.status_code == 422


async def test_api_publishes_then_push_handler_processes_once() -> None:
    settings = Settings(
        pubsub_transport="local",
        process_events_inline=False,
        gemini_model_id="gemini-test-model",
        enable_simulator=True,
        simulator_secret="test-simulator-secret",
    )
    repository = InMemoryIncidentRepository()
    await repository.seed_trip(build_demo_trip())
    publisher = LocalEventPublisher()
    workflow = ImpactAnalysisWorkflow(repository, ValidInterpreter())
    container = AppContainer(settings, repository, publisher, workflow)
    app = create_app(settings, container=container)
    event = disruption_event(event_id="api-duplicate-event")

    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            publish_response = await client.post(
                "/simulate-disruption",
                json=event.model_dump(mode="json"),
                headers={"X-Trip-Agent-Simulator-Secret": "test-simulator-secret"},
            )
            assert publish_response.status_code == 202
            assert publish_response.json()["message_id"] == "local-1"
            assert len(publisher.events) == 1

            encoded = base64.b64encode(event.model_dump_json().encode()).decode()
            envelope = {"message": {"data": encoded, "messageId": "pubsub-001"}}
            first = await client.post("/internal/pubsub/disruptions", json=envelope)
            second = await client.post("/internal/pubsub/disruptions", json=envelope)

    assert first.status_code == 200
    assert first.json()["processed"] is True
    assert second.status_code == 200
    assert second.json()["processed"] is False
    assert len(repository.incidents) == 1


async def test_push_does_not_ack_an_active_competing_lease() -> None:
    settings = Settings(
        pubsub_transport="local",
        process_events_inline=False,
        gemini_model_id="gemini-test-model",
    )
    repository = InMemoryIncidentRepository()
    await repository.seed_trip(build_demo_trip())
    publisher = LocalEventPublisher()
    workflow = ImpactAnalysisWorkflow(repository, ValidInterpreter(delay=0.03))
    container = AppContainer(settings, repository, publisher, workflow)
    app = create_app(settings, container=container)
    event = disruption_event(event_id="api-concurrent-event")
    encoded = base64.b64encode(event.model_dump_json().encode()).decode()
    envelope = {"message": {"data": encoded, "messageId": "pubsub-concurrent"}}

    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            first, competing = await asyncio.gather(
                client.post("/internal/pubsub/disruptions", json=envelope),
                client.post("/internal/pubsub/disruptions", json=envelope),
            )
            completed_redelivery = await client.post("/internal/pubsub/disruptions", json=envelope)

    assert sorted([first.status_code, competing.status_code]) == [200, 409]
    assert completed_redelivery.status_code == 200
    assert completed_redelivery.json()["claim"] == "COMPLETED"
    assert len(repository.incidents) == 1


async def test_private_watch_tick_autonomously_publishes_an_official_delay() -> None:
    settings = Settings(pubsub_transport="local", gemini_model_id="gemini-test-model")
    repository = InMemoryIncidentRepository()
    trip = build_demo_trip()
    await repository.seed_trip(trip)
    publisher = LocalEventPublisher()
    watchpoint = TripWatchpoint(
        watchpoint_id="watch:demo-trip-001:flight-waw-muc:flight_status",
        trip_id=trip.trip_id,
        item_id="flight-lo351",
        kind="FLIGHT_STATUS",
        query="LO351 flight status",
        trusted_domains=["airline.example"],
        due_at=datetime(2026, 8, 20, 12, tzinfo=UTC),
    )
    await repository.put_watchpoint(watchpoint)
    container = AppContainer(
        settings,
        repository,
        publisher,
        ImpactAnalysisWorkflow(repository, ValidInterpreter()),
        trip_watch=TripWatchWorkflow(
            repository,
            OfficialDelayGrounder(),
            publisher,
            clock=lambda: datetime(2026, 8, 20, 12, tzinfo=UTC),
        ),
        clock=lambda: datetime(2026, 8, 20, 12, tzinfo=UTC),
    )
    app = create_app(settings, container=container)

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://worker"
        ) as client:
            response = await client.post("/internal/watch/tick")

    assert response.status_code == 200
    assert response.json() == {
        "checked": 1,
        "recorded_signals": 1,
        "published_events": 1,
        "failed_watchpoints": 0,
    }
    assert publisher.events[0].flight == "LO351"


async def test_watch_tick_retries_signal_after_pubsub_failure() -> None:
    settings = Settings(pubsub_transport="local", gemini_model_id="gemini-test-model")
    repository = InMemoryIncidentRepository()
    trip = build_demo_trip()
    await repository.seed_trip(trip)
    publisher = FlakyPublisher()
    watchpoint = TripWatchpoint(
        watchpoint_id="watch:demo-trip-001:flight-lo351:flight_status",
        trip_id=trip.trip_id,
        item_id="flight-lo351",
        kind="FLIGHT_STATUS",
        query="LO351 flight status",
        trusted_domains=["airline.example"],
        due_at=datetime(2026, 8, 20, 12, tzinfo=UTC),
    )
    await repository.put_watchpoint(watchpoint)
    container = AppContainer(
        settings,
        repository,
        publisher,
        ImpactAnalysisWorkflow(repository, ValidInterpreter()),
        trip_watch=TripWatchWorkflow(
            repository,
            OfficialDelayGrounder(),
            publisher,
            clock=lambda: datetime(2026, 8, 20, 12, tzinfo=UTC),
        ),
        clock=lambda: datetime(2026, 8, 20, 12, tzinfo=UTC),
    )
    app = create_app(settings, container=container)

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://worker"
        ) as client:
            first = await client.post("/internal/watch/tick")
            second = await client.post("/internal/watch/tick")

    assert first.status_code == 200
    assert first.json()["failed_watchpoints"] == 1
    assert second.status_code == 200
    assert second.json()["published_events"] == 1
    assert len(publisher.events) == 1
