import asyncio
import base64

from app.config import Settings
from app.demo_data import build_demo_trip
from app.main import AppContainer, create_app
from app.services.memory import InMemoryIncidentRepository, LocalEventPublisher
from app.workflows.impact_analysis import ImpactAnalysisWorkflow
from httpx import ASGITransport, AsyncClient

from tests.helpers import ValidInterpreter, disruption_event


async def test_api_publishes_then_push_handler_processes_once() -> None:
    settings = Settings(
        pubsub_transport="local",
        process_events_inline=False,
        gemini_model_id="gemini-test-model",
    )
    repository = InMemoryIncidentRepository()
    await repository.seed_trip(build_demo_trip())
    publisher = LocalEventPublisher()
    workflow = ImpactAnalysisWorkflow(repository, ValidInterpreter())
    container = AppContainer(settings, repository, publisher, workflow)
    app = create_app(settings, container=container)
    event = disruption_event(event_id="api-duplicate-event")

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            publish_response = await client.post(
                "/simulate-disruption", json=event.model_dump(mode="json")
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
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            first, competing = await asyncio.gather(
                client.post("/internal/pubsub/disruptions", json=envelope),
                client.post("/internal/pubsub/disruptions", json=envelope),
            )
            completed_redelivery = await client.post(
                "/internal/pubsub/disruptions", json=envelope
            )

    assert sorted([first.status_code, competing.status_code]) == [200, 409]
    assert completed_redelivery.status_code == 200
    assert completed_redelivery.json()["claim"] == "COMPLETED"
    assert len(repository.incidents) == 1
