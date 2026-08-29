from __future__ import annotations

from app.config import Settings
from app.main import AppContainer, create_app
from app.models.enums import IncidentStatus, OnboardingStep
from app.services.memory import InMemoryIncidentRepository, LocalEventPublisher
from app.services.onboarding import TelegramOnboardingService
from app.services.telegram_demo import TelegramDemoService
from app.services.telegram_recovery import TelegramRecoveryService
from app.workflows.impact_analysis import ImpactAnalysisWorkflow
from app.workflows.recovery import RecoveryWorkflow
from httpx import ASGITransport, AsyncClient

from tests.helpers import ValidInterpreter
from tests.test_telegram_api import callback_update, message_update


async def test_demo_runs_before_onboarding_without_gemini_and_recovers() -> None:
    settings = Settings(
        pubsub_transport="local",
        app_role="all",
        telegram_webhook_secret="demo-webhook-secret",
    )
    repository = InMemoryIncidentRepository()
    publisher = LocalEventPublisher()
    recovery = RecoveryWorkflow(repository)
    container = AppContainer(
        settings=settings,
        repository=repository,
        publisher=publisher,
        workflow=ImpactAnalysisWorkflow(repository, ValidInterpreter()),
        onboarding=TelegramOnboardingService(repository),
        recovery=recovery,
        telegram_recovery=TelegramRecoveryService(repository, recovery),
        telegram_demo=TelegramDemoService(repository, recovery),
    )
    app = create_app(settings, container=container)
    app.state.container = container
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    headers = {"X-Telegram-Bot-Api-Secret-Token": "demo-webhook-secret"}
    try:
        start = await client.post(
            "/telegram/webhook", json=message_update(1, "/start"), headers=headers
        )
        trip = await client.post(
            "/telegram/webhook",
            json=callback_update(2, "demo:start"),
            headers=headers,
        )
        recovery_card = await client.post(
            "/telegram/webhook",
            json=callback_update(3, "demo:trigger"),
            headers=headers,
        )

        approve_callback = recovery_card.json()["button_rows"][0][0]["callback_data"]
        details = await client.post(
            "/telegram/webhook",
            json=callback_update(4, recovery_card.json()["button_rows"][0][1]["callback_data"]),
            headers=headers,
        )
        approved = await client.post(
            "/telegram/webhook",
            json=callback_update(5, approve_callback),
            headers=headers,
        )
        proof = await client.post(
            "/telegram/webhook",
            json=callback_update(12, approved.json()["button_rows"][0][0]["callback_data"]),
            headers=headers,
        )
        expense = await client.post(
            "/telegram/webhook",
            json=callback_update(6, "demo:expense"),
            headers=headers,
        )
        duplicate_expense = await client.post(
            "/telegram/webhook",
            json=callback_update(7, "demo:expense"),
            headers=headers,
        )
        readiness = await client.post(
            "/telegram/webhook",
            json=callback_update(8, "demo:readiness"),
            headers=headers,
        )
        ready = await client.post(
            "/telegram/webhook",
            json=callback_update(9, "demo:add_voucher"),
            headers=headers,
        )
        closeout = await client.post(
            "/telegram/webhook",
            json=callback_update(10, "demo:closeout"),
            headers=headers,
        )
        closed = await client.post(
            "/telegram/webhook",
            json=callback_update(11, "demo:settle_finance"),
            headers=headers,
        )
    finally:
        await client.aclose()

    traveler = await repository.get_traveler("101")
    incident = next(iter(repository.incidents.values()))
    assert start.status_code == 200
    assert start.json()["button_rows"][0][0]["callback_data"] == "onboard:setup"
    assert "demo" not in start.json()["text"].lower()
    assert "AGENT STATE  WATCHING" in trip.json()["text"]
    assert "IMPACT RESOLVED" in recovery_card.json()["text"]
    assert "Approve +€34" == recovery_card.json()["button_rows"][0][0]["text"]
    assert details.json()["button_rows"][0][0]["callback_data"] == approve_callback
    assert approved.status_code == 200
    assert "RECOVERY VERIFIED" in approved.json()["text"]
    assert approved.json()["parse_mode"] == "HTML"
    assert len(approved.json()["button_rows"]) == 1
    assert approved.json()["button_rows"][0][0]["callback_data"].startswith("demo:proof:")
    assert "4/4 RECOVERY STEPS VERIFIED" in proof.json()["text"]
    assert "€61.40" in expense.json()["text"]
    assert "€61.40" in duplicate_expense.json()["text"]
    assert "NEEDS ATTENTION" in readiness.json()["text"]
    assert "transfer voucher not found" in readiness.json()["text"]
    assert "READINESS SCAN · READY" in ready.json()["text"]
    assert "2 OPEN ITEMS" in closeout.json()["text"]
    assert "LIFECYCLE COMPLETE" in closed.json()["text"]
    assert traveler is not None and traveler.onboarding_step == OnboardingStep.PROMISE
    assert incident.gemini_model_id == "deterministic-demo"
    assert incident.status == IncidentStatus.RECOVERED
    assert len(repository.effects) == 4
    assert len(repository.expenses) == 2
    assert sum(item.amount.minor_units for item in repository.expenses.values()) == 6_140
    assert all("telegram-demo-trip:101" in key for key in repository.demo_provider_state)
    assert len(repository.trip_documents) == 3
    assert len(repository.financial_items) == 2
    assert all(item.status == "SETTLED" for item in repository.financial_items.values())
    demo_trip = await repository.get_trip("telegram-demo-trip:101")
    assert demo_trip is not None and demo_trip.status.value == "CLOSED"


async def test_demo_rejects_an_unbound_telegram_user() -> None:
    settings = Settings(
        pubsub_transport="local",
        app_role="all",
        telegram_webhook_secret="demo-webhook-secret",
    )
    repository = InMemoryIncidentRepository()
    recovery = RecoveryWorkflow(repository)
    container = AppContainer(
        settings=settings,
        repository=repository,
        publisher=LocalEventPublisher(),
        workflow=ImpactAnalysisWorkflow(repository, ValidInterpreter()),
        onboarding=TelegramOnboardingService(repository),
        recovery=recovery,
        telegram_recovery=TelegramRecoveryService(repository, recovery),
        telegram_demo=TelegramDemoService(repository, recovery),
    )
    app = create_app(settings, container=container)
    app.state.container = container
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    headers = {"X-Telegram-Bot-Api-Secret-Token": "demo-webhook-secret"}
    try:
        response = await client.post(
            "/telegram/webhook",
            json=callback_update(1, "demo:start", user_id=999),
            headers=headers,
        )
    finally:
        await client.aclose()

    assert response.status_code == 400
    assert repository.incidents == {}
