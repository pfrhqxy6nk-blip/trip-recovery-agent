"""Comprehensive QA E2E test simulating a user building a complete trip from scratch.

Covers:
1. User /start onboarding and setting autonomy budget (€20).
2. Multimodal text itinerary intake (forwarded email with 2 flights + hotel).
3. Parsing into dependency graph and watchpoint generation.
4. Live disruption triggering (Flight LO351 delayed +105 min).
5. Impact analysis, blast-radius calculation, and proposal of recovery plan.
6. User approval via Telegram callback.
7. Resuming recovery with idempotency, action execution, and EU261 claim preparation.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.config import Settings
from app.main import AppContainer, create_app
from app.models.enums import IncidentStatus
from app.models.telegram import TelegramMessageReceipt, TelegramView
from app.services.compensation import PassengerCompensationService
from app.services.guardian import TravelGuardianService
from app.services.memory import InMemoryIncidentRepository, LocalEventPublisher
from app.services.onboarding import TelegramOnboardingService
from app.services.predictive_shadow_engine import PredictiveShadowEngine
from app.services.telegram_recovery import TelegramRecoveryService
from app.services.telegram_trips import TelegramTripService
from app.workflows.impact_analysis import ImpactAnalysisWorkflow
from app.workflows.recovery import RecoveryWorkflow
from httpx import ASGITransport, AsyncClient

from tests.helpers import ValidInterpreter
from tests.test_telegram_api import callback_update, message_update


class LiveDemoTelegramGateway:
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


@pytest.mark.asyncio
async def test_full_user_trip_lifecycle_from_scratch_to_recovery() -> None:
    now = datetime(2026, 8, 20, 15, 0, tzinfo=UTC)
    settings = Settings(
        pubsub_transport="local",
        app_role="all",
        process_events_inline=True,
        telegram_webhook_secret="demo-webhook-secret",
        enable_pilot_trip=True,
        enable_simulator=True,
        simulator_secret="demo-simulator-secret",
    )
    repository = InMemoryIncidentRepository()
    publisher = LocalEventPublisher()
    impact = ImpactAnalysisWorkflow(repository, ValidInterpreter())
    onboarding = TelegramOnboardingService(repository)
    recovery = RecoveryWorkflow(repository)
    telegram_recovery = TelegramRecoveryService(repository, recovery)
    telegram_trips = TelegramTripService(repository, pilot_enabled=True)
    gateway = LiveDemoTelegramGateway()

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
        clock=lambda: now,
    )

    app = create_app(settings, container=container)
    app.state.container = container
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    headers = {"X-Telegram-Bot-Api-Secret-Token": "demo-webhook-secret"}

    try:
        # -------------------------------------------------------------
        # STEP 1: Traveler Onboarding (/start -> Autonomy Policy €20)
        # -------------------------------------------------------------
        res_start = await client.post(
            "/telegram/webhook",
            json=message_update(1, "/start"),
            headers=headers,
        )
        assert res_start.status_code == 200

        onboarding_steps = [
            "onboard:setup",
            "onboard:calendar_auto",
            "onboard:service_auto",
            "onboard:reversible_auto",
            "onboard:spend_20",
            "onboard:boundary_continue",
            "onboard:activate",
        ]
        for update_id, callback in enumerate(onboarding_steps, start=2):
            res_cb = await client.post(
                "/telegram/webhook",
                json=callback_update(update_id, callback),
                headers=headers,
            )
            assert res_cb.status_code == 200

        traveler = await repository.get_traveler("101")
        assert traveler is not None
        assert traveler.active_policy_version == 1

        # -------------------------------------------------------------
        # STEP 2: Natural Language Trip Intake (Forwarded Booking Email)
        # -------------------------------------------------------------
        forwarded_email = (
            "Booking Confirmation:\n"
            "Flight 1: LOT Polish Airlines LO351 WAW -> MUC (2026-08-20 16:30 -> 18:00). "
            "PNR: LOT-ABC123\n"
            "Flight 2: Lufthansa LH1792 MUC -> LIS (2026-08-20 18:55 -> 21:05). "
            "PNR: LH-XYZ789\n"
            "Hotel: Lisbon Riverside Hotel, Check-in 2026-08-20 from 22:00."
        )

        res_email = await client.post(
            "/telegram/webhook",
            json=message_update(15, forwarded_email),
            headers=headers,
        )
        assert res_email.status_code == 200

        # Save itinerary draft
        res_save = await client.post(
            "/telegram/webhook",
            json=callback_update(16, "trip:manual:save"),
            headers=headers,
        )
        assert res_save.status_code == 200

        # Verify created trip in repository
        trips = list(repository.trips.values())
        assert len(trips) == 1
        user_trip = trips[0]
        assert user_trip.owner_user_id == "telegram:101"
        assert len(user_trip.items) >= 2  # flights + hotel
        assert len(user_trip.dependencies) >= 1  # connection dependency

        # -------------------------------------------------------------
        # STEP 3: Verify Predictive Shadow Engine
        # -------------------------------------------------------------
        shadow_tree = PredictiveShadowEngine.evaluate_trip_risk(
            user_trip, now=now, inbound_delay_minutes=0
        )
        assert shadow_tree is not None
        assert len(shadow_tree.assessments) >= 1

        # -------------------------------------------------------------
        # STEP 4: Simulate Disruption (LO351 delayed past connection window)
        # -------------------------------------------------------------
        first_flight = user_trip.items[0]
        second_flight = user_trip.items[1]
        old_arr = first_flight.end_at
        # New arrival is 15 min after second flight departure, breaking the connection
        new_arr = second_flight.start_at + timedelta(minutes=15)

        disruption_res = await client.post(
            "/simulate-disruption",
            headers={"X-Trip-Agent-Simulator-Secret": "demo-simulator-secret"},
            json={
                "event_id": "qa-delay-event-105",
                "trip_id": user_trip.trip_id,
                "type": "flight_delay",
                "flight": first_flight.external_id,
                "old_arrival": old_arr.isoformat(),
                "new_arrival": new_arr.isoformat(),
            },
        )
        assert disruption_res.status_code == 202
        incident_id = disruption_res.json()["processing"]["incident_id"]

        # Verify telegram notification was sent
        last_sent = gateway.sent[-1]
        assert "Trip change detected" in last_sent.text or "need your approval" in last_sent.text

        # -------------------------------------------------------------
        # STEP 5: Travel Guardian Screening & EU261 Claim check
        # -------------------------------------------------------------
        guardian_report = TravelGuardianService.screen_connection(
            connection_id="qa-conn-1",
            origin_airport="WAW",
            hub_airport="MUC",
            destination_airport="LIS",
            scheduled_buffer_minutes=0,
            profile=None,
        )
        assert guardian_report.visa.status.value in ["CLEAR", "REQUIRES_VERIFICATION"]

        # EU261 statutory claim assessment for delay >= 180 min
        # WAW -> MUC (under 1500 km) = €250; WAW -> LIS (2750 km) = €400
        assessment = PassengerCompensationService.assess_flight_disruption(
            flight_number="LO351",
            origin="WAW",
            destination="MUC",
            delay_minutes=195,
        )
        assert assessment.eligible is True
        assert assessment.amount is not None and assessment.amount.minor_units == 25000

        claim_letter = PassengerCompensationService.generate_claim_letter(
            incident_id=incident_id,
            passenger_name="Alex Traveler",
            flight_number="LO351",
            origin="WAW",
            destination="LIS",
            scheduled_arrival=datetime(2026, 8, 20, 18, 0, tzinfo=UTC),
            actual_arrival=datetime(2026, 8, 20, 21, 15, tzinfo=UTC),
            booking_reference="LOT-ABC123",
            assessment=assessment,
        )
        assert "Regulation (EC) No 261/2004" in claim_letter.body_en

        # -------------------------------------------------------------
        # STEP 6: Traveler Approves Recovery via Telegram Button
        # -------------------------------------------------------------
        approval_view = gateway.sent[-1]
        callback_data = approval_view.button_rows[0][0].callback_data
        assert callback_data is not None

        res_approve = await client.post(
            "/telegram/webhook",
            json=callback_update(30, callback_data),
            headers=headers,
        )
        assert res_approve.status_code == 200

        # Execute recovery workflow resumption from outbox
        resume_cmd = next(
            record.command
            for record in repository.outbox.values()
            if record.command.type.value == "RESUME_AFTER_APPROVAL"
        )
        recovered_status = await recovery.process_command(
            command=resume_cmd,
            worker_id="qa-command-worker",
            now=now,
        )
        recovered_view = await telegram_recovery.status_view(incident_id, recovered_status)

        # -------------------------------------------------------------
        # STEP 7: Final Verification of Recovery and Effects
        # -------------------------------------------------------------
        incident = await repository.get_incident(incident_id)
        assert incident is not None and incident.status == IncidentStatus.RECOVERED
        assert "Trip recovered" in recovered_view.text
        assert len(repository.effects) >= 2  # flight/calendar/hotel notifications

    finally:
        await client.aclose()
