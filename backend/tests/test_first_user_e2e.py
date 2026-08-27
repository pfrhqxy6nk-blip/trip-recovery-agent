from __future__ import annotations

from app.config import Settings
from app.main import AppContainer, create_app
from app.models.enums import IncidentStatus
from app.models.telegram import TelegramMessageReceipt, TelegramView
from app.services.memory import InMemoryIncidentRepository, LocalEventPublisher
from app.services.onboarding import TelegramOnboardingService
from app.services.telegram_recovery import TelegramRecoveryService
from app.services.telegram_trips import TelegramTripService
from app.workflows.impact_analysis import ImpactAnalysisWorkflow
from app.workflows.recovery import RecoveryWorkflow
from httpx import ASGITransport, AsyncClient

from tests.helpers import ValidInterpreter
from tests.test_telegram_api import callback_update, message_update


class PilotTelegramGateway:
    def __init__(self) -> None:
        self.sent: list[TelegramView] = []
        self.edited: list[TelegramView] = []
        self.answered: list[str] = []

    async def send_message(self, *, chat_id: str, view: TelegramView) -> TelegramMessageReceipt:
        self.sent.append(view)
        return TelegramMessageReceipt(chat_id=chat_id, message_id=100 + len(self.sent))

    async def edit_message(
        self, *, chat_id: str, message_id: int, view: TelegramView
    ) -> TelegramMessageReceipt:
        self.edited.append(view)
        return TelegramMessageReceipt(chat_id=chat_id, message_id=message_id)

    async def answer_callback_query(
        self,
        *,
        callback_query_id: str,
        text: str | None = None,
        show_alert: bool = False,
    ) -> None:
        self.answered.append(callback_query_id)


async def test_first_pilot_user_onboards_adds_trip_and_recovers_via_telegram() -> None:
    settings = Settings(
            pubsub_transport="local",
            app_role="all",
            process_events_inline=True,
        telegram_webhook_secret="pilot-webhook-secret",
        enable_pilot_trip=True,
        enable_simulator=True,
        simulator_secret="pilot-simulator-secret",
    )
    repository = InMemoryIncidentRepository()
    publisher = LocalEventPublisher()
    impact = ImpactAnalysisWorkflow(repository, ValidInterpreter())
    onboarding = TelegramOnboardingService(repository)
    recovery = RecoveryWorkflow(repository)
    telegram_recovery = TelegramRecoveryService(repository, recovery)
    telegram_trips = TelegramTripService(repository, pilot_enabled=True)
    gateway = PilotTelegramGateway()
    container = AppContainer(
        settings=settings,
        repository=repository,
        publisher=publisher,
        workflow=impact,
        onboarding=onboarding,
        telegram_gateway=gateway,
        recovery=recovery,
        telegram_recovery=telegram_recovery,
        telegram_trips=telegram_trips,
    )
    app = create_app(settings, container=container)
    app.state.container = container
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    headers = {"X-Telegram-Bot-Api-Secret-Token": "pilot-webhook-secret"}
    try:
        await client.post("/telegram/webhook", json=message_update(1, "/start"), headers=headers)
        for update_id, callback in enumerate(
            [
                "onboard:setup",
                "onboard:calendar_auto",
                "onboard:service_auto",
                "onboard:reversible_auto",
                "onboard:spend_20",
                "onboard:boundary_continue",
                "onboard:activate",
            ],
            start=2,
        ):
            response = await client.post(
                "/telegram/webhook",
                json=callback_update(update_id, callback),
                headers=headers,
            )
            assert response.status_code == 200

        added = await client.post(
            "/telegram/webhook",
            json=callback_update(20, "trip:add_pilot"),
            headers=headers,
        )
        assert added.status_code == 200

        disruption = await client.post(
            "/simulate-disruption",
            headers={"X-Trip-Agent-Simulator-Secret": "pilot-simulator-secret"},
            json={
                "event_id": "pilot-delay-001",
                "trip_id": "pilot-trip:101",
                "type": "flight_delay",
                "flight": "LO351",
                "old_arrival": "2026-08-20T18:00:00Z",
                "new_arrival": "2026-08-20T19:45:00Z",
            },
        )
        assert disruption.status_code == 202
        incident_id = disruption.json()["processing"]["incident_id"]
        approval_view = gateway.sent[-1]
        callback_data = approval_view.button_rows[0][0].callback_data
        assert callback_data is not None
        assert callback_data.startswith("a:") and len(callback_data.encode()) <= 64

        approved = await client.post(
            "/telegram/webhook",
            json=callback_update(30, callback_data),
            headers=headers,
        )
        resume = next(
            record.command
            for record in repository.outbox.values()
            if record.command.type.value == "RESUME_AFTER_APPROVAL"
        )
        recovered_status = await recovery.process_command(
            command=resume,
            worker_id="pilot-command-worker",
            now=container.clock(),
        )
        recovered_view = await telegram_recovery.status_view(incident_id, recovered_status)
    finally:
        await client.aclose()

    incident = await repository.get_incident(incident_id)
    assert approved.status_code == 200
    assert approved.json()["text"].startswith("Approval recorded")
    assert incident is not None and incident.status == IncidentStatus.RECOVERED
    assert gateway.sent[-2].text.startswith("Trip change detected")
    assert "need your approval" in gateway.sent[-1].text
    assert recovered_view.text.startswith("Trip recovered")
    assert len(repository.effects) == 4
