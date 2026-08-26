from __future__ import annotations

from app.config import Settings
from app.demo_data import build_demo_trip
from app.main import AppContainer, create_app
from app.services.memory import InMemoryIncidentRepository, LocalEventPublisher
from app.workflows.impact_analysis import ImpactAnalysisWorkflow
from app.workflows.recovery import RecoveryWorkflow
from httpx import ASGITransport, AsyncClient

from tests.helpers import ValidInterpreter


async def test_simulator_page_returns_html_editorial() -> None:
    settings = Settings(pubsub_transport="local")
    repository = InMemoryIncidentRepository()
    await repository.seed_trip(build_demo_trip())

    container = AppContainer(
        settings,
        repository,
        LocalEventPublisher(),
        ImpactAnalysisWorkflow(repository, ValidInterpreter()),
        recovery=RecoveryWorkflow(repository),
    )
    app = create_app(settings, container=container)
    app.state.container = container

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get("/simulator")
        assert res.status_code == 200
        assert "text/html" in res.headers["content-type"]
        assert "Live Blast-Radius Simulator" in res.text
        assert "Screening Guardian" in res.text or "Visa & Baggage Screening Guardian" in res.text


async def test_simulator_evaluate_endpoint() -> None:
    settings = Settings(pubsub_transport="local")
    repository = InMemoryIncidentRepository()
    await repository.seed_trip(build_demo_trip())

    container = AppContainer(
        settings,
        repository,
        LocalEventPublisher(),
        ImpactAnalysisWorkflow(repository, ValidInterpreter()),
        recovery=RecoveryWorkflow(repository),
    )
    app = create_app(settings, container=container)
    app.state.container = container

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Test 1: On-time state
        res_ontime = await client.post(
            "/api/simulator/evaluate",
            json={"delay_minutes": 0, "citizenship_iso2": "DE", "has_checked_bags": True},
        )
        assert res_ontime.status_code == 200
        data_ontime = res_ontime.json()
        assert data_ontime["is_connection_feasible"] is True
        assert data_ontime["effective_buffer_minutes"] == 55
        assert data_ontime["guardian"]["visa"]["status"] == "CLEAR"
        assert data_ontime["compensation"]["eligible"] is False

        # Test 2: Severe delay (+105 min)
        res_delayed = await client.post(
            "/api/simulator/evaluate",
            json={"delay_minutes": 105, "citizenship_iso2": "DE", "has_checked_bags": True},
        )
        assert res_delayed.status_code == 200
        data_delayed = res_delayed.json()
        assert data_delayed["is_connection_feasible"] is False
        assert data_delayed["effective_buffer_minutes"] == -50
        assert data_delayed["nodes"][1]["status"] == "SEVERED"
        assert data_delayed["compensation"]["eligible"] is True
        assert "Review required" in data_delayed["compensation"]["label"]


async def test_simulator_recover_endpoint() -> None:
    settings = Settings(pubsub_transport="local")
    repository = InMemoryIncidentRepository()
    await repository.seed_trip(build_demo_trip())

    container = AppContainer(
        settings,
        repository,
        LocalEventPublisher(),
        ImpactAnalysisWorkflow(repository, ValidInterpreter()),
        recovery=RecoveryWorkflow(repository),
    )
    app = create_app(settings, container=container)
    app.state.container = container

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post(
            "/api/simulator/recover",
            json={"delay_minutes": 105, "citizenship_iso2": "DE", "has_checked_bags": True},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "RECOVERED"
        assert len(data["audit_trail"]) >= 4
        assert "screening passed" in data["audit_trail"][0].lower()
