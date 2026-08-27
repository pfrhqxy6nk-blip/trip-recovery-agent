from __future__ import annotations

import base64
from datetime import UTC, datetime

from app.config import Settings
from app.demo_data import build_owned_demo_trip
from app.main import AppContainer, create_app
from app.models.enums import IncidentStatus, OnboardingStep, PolicyMode
from app.models.money import Money
from app.models.policy import AutonomyPolicy
from app.models.telegram import TelegramMessageReceipt, TelegramView, TravelerProfile
from app.models.watch import GroundedTravelSignal, SourceTrust, TripWatchpoint
from app.services.memory import InMemoryIncidentRepository, LocalEventPublisher
from app.services.onboarding import TelegramOnboardingService
from app.services.outbox import DurableOutboxDispatcher
from app.services.telegram_recovery import TelegramRecoveryService
from app.services.telegram_trips import TelegramTripService
from app.services.trip_watch_workflow import TripWatchWorkflow
from app.workflows.impact_analysis import ImpactAnalysisWorkflow
from app.workflows.recovery import RecoveryWorkflow
from httpx import ASGITransport, AsyncClient

from tests.helpers import ValidInterpreter
from tests.test_telegram_api import callback_update


class AutonomousDelayGrounder:
    async def observe(self, watchpoint: TripWatchpoint) -> GroundedTravelSignal:
        return GroundedTravelSignal(
            watchpoint_id=watchpoint.watchpoint_id,
            summary="The airline confirms LO351 now arrives 1h45 later.",
            source_url="https://airline.example/status/LO351",
            source_title="LOT flight status",
            trust=SourceTrust.OFFICIAL,
            observed_at=datetime(2026, 8, 20, 12, tzinfo=UTC),
            affects_trip=True,
            suggested_event_type="FLIGHT_ARRIVAL_DELAY",
            observed_flight="LO351",
            old_arrival=datetime(2026, 8, 20, 18, tzinfo=UTC),
            new_arrival=datetime(2026, 8, 20, 19, 45, tzinfo=UTC),
        )


class RecordingTelegramGateway:
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


async def test_watch_tick_to_pubsub_to_telegram_resume_is_autonomous() -> None:
    """Prove the runtime chain, not only its isolated workflow units."""

    now = datetime(2026, 8, 20, 12, tzinfo=UTC)
    owner = "telegram:101"
    trip = build_owned_demo_trip(owner_user_id=owner, trip_id="autonomous-trip-001")
    repository = InMemoryIncidentRepository()
    await repository.seed_trip(trip)
    await repository.put_watchpoint(
        TripWatchpoint(
            watchpoint_id="watch:autonomous-trip-001:flight-lo351:flight_status",
            trip_id=trip.trip_id,
            item_id="flight-lo351",
            kind="FLIGHT_STATUS",
            query="LO351 flight status",
            trusted_domains=["airline.example"],
            due_at=now,
        )
    )
    traveler = TravelerProfile(
        user_id=owner,
        telegram_user_id="101",
        telegram_chat_id="202",
        onboarding_step=OnboardingStep.COMPLETE,
        calendar_mode=PolicyMode.AUTO,
        service_message_mode=PolicyMode.AUTO,
        reversible_change_mode=PolicyMode.AUTO,
        automatic_spending_enabled=True,
        incident_spending_limit=Money(currency="EUR", minor_units=2_000),
        active_policy_version=None,
        created_at=now,
        updated_at=now,
    )
    policy = AutonomyPolicy(
        policy_id=f"{owner}:policy:1",
        user_id=owner,
        version=1,
        automatic_spending_enabled=True,
        incident_spending_limit=Money(currency="EUR", minor_units=2_000),
        created_at=now,
        updated_at=now,
    )
    await repository.save_traveler(traveler)
    activated_traveler = traveler.model_copy(update={"active_policy_version": 1})
    assert await repository.activate_traveler_policy(traveler=activated_traveler, policy=policy)

    publisher = LocalEventPublisher()
    gateway = RecordingTelegramGateway()
    settings = Settings(
        pubsub_transport="local",
        app_role="all",
        process_events_inline=False,
        telegram_webhook_secret="autonomous-webhook-secret",
        gemini_model_id="gemini-test-model",
    )
    recovery = RecoveryWorkflow(repository)
    container = AppContainer(
        settings=settings,
        repository=repository,
        publisher=publisher,
        workflow=ImpactAnalysisWorkflow(repository, ValidInterpreter()),
        onboarding=TelegramOnboardingService(repository),
        telegram_gateway=gateway,
        recovery=recovery,
        telegram_recovery=TelegramRecoveryService(repository, recovery),
        telegram_trips=TelegramTripService(repository, pilot_enabled=False),
        trip_watch=TripWatchWorkflow(
            repository,
            AutonomousDelayGrounder(),
            publisher,
            clock=lambda: now,
        ),
        clock=lambda: now,
        command_publisher=publisher,
        outbox_dispatcher=DurableOutboxDispatcher(repository, publisher),
    )
    app = create_app(settings, container=container)
    envelope_headers = {"X-Telegram-Bot-Api-Secret-Token": "autonomous-webhook-secret"}

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://worker"
        ) as client:
            tick = await client.post("/internal/watch/tick")
            assert tick.status_code == 200
            assert tick.json()["published_events"] == 1
            assert len(publisher.events) == 1

            event = publisher.events[0]
            encoded = base64.b64encode(event.model_dump_json().encode()).decode()
            consumed = await client.post(
                "/internal/pubsub/disruptions",
                json={"message": {"data": encoded, "messageId": "watch-pubsub-001"}},
            )
            assert consumed.status_code == 200
            assert consumed.json()["processed"] is True
            # The worker sent awareness and a bounded approval request without a
            # human having to start a chat command or refresh a dashboard.
            assert any("Trip change detected" in view.text for view in gateway.sent)
            approval_view = next(view for view in gateway.sent if view.button_rows)
            callback = approval_view.button_rows[0][0].callback_data
            assert callback is not None and callback.startswith("a:")

            approved = await client.post(
                "/telegram/webhook",
                json=callback_update(900, callback),
                headers=envelope_headers,
            )
            assert approved.status_code == 200
            assert len(publisher.commands) == 1
            command = publisher.commands[0]
            command_data = base64.b64encode(command.model_dump_json().encode()).decode()
            resumed = await client.post(
                "/internal/pubsub/commands",
                json={"message": {"data": command_data, "messageId": "command-pubsub-001"}},
            )
            assert resumed.status_code == 200
            final_status = IncidentStatus(resumed.json()["status"])

    incident_id = consumed.json()["incident_id"]
    incident = await repository.get_incident(incident_id)
    assert approved.json()["text"].startswith("Approval recorded")
    assert final_status == IncidentStatus.RECOVERED
    assert incident is not None and incident.status == IncidentStatus.RECOVERED
    assert any("Trip recovered" in view.text for view in gateway.sent)
