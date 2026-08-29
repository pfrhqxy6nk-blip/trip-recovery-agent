from __future__ import annotations

from datetime import UTC, datetime
from html import escape
from typing import Any

from app.demo_data import build_owned_demo_trip
from app.models.domain import DeterministicImpact, DisruptionEvent, Trip
from app.models.enums import ActionStatus, IncidentStatus
from app.models.money import Money
from app.models.policy import AutonomyPolicy
from app.models.telegram import TelegramButton, TelegramView
from app.services.expenses import TripExpenseService
from app.services.ports import IncidentRepository
from app.services.readiness import TripReadinessService
from app.services.trip_closure import TripClosureService
from app.workflows.impact_analysis import ImpactAnalysisWorkflow
from app.workflows.recovery import RecoveryStartResult, RecoveryWorkflow


class TelegramDemoError(ValueError):
    pass


class DeterministicDemoInterpreter:
    """Truthful demo narration that never consumes a traveler's Gemini quota."""

    model_id = "deterministic-demo"
    prompt_version = "telegram-demo-v1"

    async def interpret(
        self,
        event: DisruptionEvent,
        trip: Trip,
        deterministic_impact: DeterministicImpact,
    ) -> dict[str, Any]:
        del trip
        return {
            "normalized_event_type": "flight_delay",
            "summary": f"{event.flight} is delayed and the Munich connection is infeasible.",
            "contextual_factors": [
                f"{len(deterministic_impact.affected_item_ids)} downstream trip items affected"
            ],
            "explanation": (
                "The deterministic dependency engine found that the remaining connection "
                "buffer is below the required minimum."
            ),
            "confidence": 1.0,
        }


class TelegramDemoService:
    """Run an isolated recovery story before onboarding or Gemini connection."""

    def __init__(self, repository: IncidentRepository, recovery_workflow: RecoveryWorkflow) -> None:
        self._repository = repository
        self._recovery = recovery_workflow
        self._impact = ImpactAnalysisWorkflow(repository, DeterministicDemoInterpreter())
        self._expenses = TripExpenseService(repository)
        self._readiness = TripReadinessService(repository)
        self._closure = TripClosureService(repository)

    async def handle(
        self,
        *,
        telegram_user_id: str,
        telegram_chat_id: str,
        callback_data: str,
        update_id: str,
        now: datetime,
    ) -> TelegramView:
        traveler = await self._repository.get_traveler(telegram_user_id)
        if traveler is None or traveler.telegram_chat_id != telegram_chat_id:
            raise TelegramDemoError("start the bot before opening the demo")
        if callback_data == "demo:start":
            await self._ensure_demo_trip(traveler.user_id, telegram_user_id, now)
            return self.trip_view()
        if callback_data == "demo:lifecycle":
            return self.lifecycle_view()
        if callback_data.startswith("demo:proof:"):
            return await self._proof_view(
                incident_id=callback_data.removeprefix("demo:proof:"),
                owner_user_id=traveler.user_id,
                telegram_user_id=telegram_user_id,
            )
        if callback_data.startswith("demo:claim:"):
            return await self._claim_view(
                incident_id=callback_data.removeprefix("demo:claim:"),
                owner_user_id=traveler.user_id,
                telegram_user_id=telegram_user_id,
            )
        if callback_data == "demo:shadow":
            return await self._shadow_view(traveler.user_id, telegram_user_id, now)
        if callback_data == "demo:guardian":
            return await self._guardian_view(traveler.user_id, telegram_user_id)
        if callback_data == "demo:readiness":
            trip_id = await self._ensure_demo_trip(traveler.user_id, telegram_user_id, now)
            report = await self._readiness.report(
                trip_id=trip_id, owner_user_id=traveler.user_id, now=now
            )
            return self.readiness_view(report.status == "READY")
        if callback_data == "demo:add_voucher":
            trip_id = await self._ensure_demo_trip(traveler.user_id, telegram_user_id, now)
            await self._readiness.add_demo_transfer_voucher(
                trip_id=trip_id, owner_user_id=traveler.user_id, now=now
            )
            report = await self._readiness.report(
                trip_id=trip_id, owner_user_id=traveler.user_id, now=now
            )
            return self.readiness_view(report.status == "READY")
        if callback_data == "demo:expense":
            expense = await self._expenses.record_demo_taxi(
                telegram_user_id=telegram_user_id,
                telegram_chat_id=telegram_chat_id,
                now=now,
            )
            summary = await self._expenses.summary(
                trip_id=expense.trip_id,
                owner_user_id=traveler.user_id,
            )
            return self.expense_view(summary.disruption_total.minor_units)
        if callback_data == "demo:closeout":
            trip_id = await self._ensure_demo_trip(traveler.user_id, telegram_user_id, now)
            await self._closure.seed_demo_financial_items(
                trip_id=trip_id, owner_user_id=traveler.user_id, now=now
            )
            return self.closeout_view(closed=False)
        if callback_data == "demo:settle_finance":
            trip_id = await self._ensure_demo_trip(traveler.user_id, telegram_user_id, now)
            await self._closure.seed_demo_financial_items(
                trip_id=trip_id, owner_user_id=traveler.user_id, now=now
            )
            await self._closure.settle_demo_financial_items(
                trip_id=trip_id, owner_user_id=traveler.user_id, now=now
            )
            closure_report = await self._closure.close_trip(
                trip_id=trip_id, owner_user_id=traveler.user_id, now=now
            )
            return self.closeout_view(closed=closure_report.status == "CLOSED")
        if callback_data != "demo:trigger":
            raise TelegramDemoError("unsupported demo action")

        trip_id = await self._ensure_demo_trip(traveler.user_id, telegram_user_id, now)
        event = DisruptionEvent(
            event_id=f"telegram-demo:{telegram_user_id}:{update_id}",
            trip_id=trip_id,
            type="flight_delay",
            flight="LO351",
            old_arrival=datetime(2026, 8, 20, 18, 0, tzinfo=UTC),
            # This controlled scenario crosses the statutory three-hour
            # threshold. It lets the demo show a truthful, reviewable EU261
            # claim after recovery rather than implying that a 105-minute
            # missed connection itself earns compensation.
            new_arrival=datetime(2026, 8, 20, 21, 15, tzinfo=UTC),
            context={"source": "isolated_telegram_demo", "airline_fault": True},
        )
        impact = await self._impact.process(event)
        result = await self._recovery.start(
            incident_id=impact.incident_id,
            policy=AutonomyPolicy(
                policy_id=f"{traveler.user_id}:demo-policy",
                user_id=traveler.user_id,
                version=1,
                automatic_spending_enabled=True,
                incident_spending_limit=Money(currency="EUR", minor_units=2_000),
                created_at=now,
                updated_at=now,
            ),
            telegram_user_id=telegram_user_id,
            telegram_chat_id=telegram_chat_id,
            now=now,
        )
        return self.approval_view(result)

    async def _proof_view(
        self, *, incident_id: str, owner_user_id: str, telegram_user_id: str
    ) -> TelegramView:
        incident = await self._repository.get_incident(incident_id)
        expected_trip_id = f"telegram-demo-trip:{telegram_user_id}"
        if (
            incident is None
            or incident.trip_id != expected_trip_id
            or incident.status != IncidentStatus.RECOVERED
        ):
            raise TelegramDemoError("recovery proof is not available")
        trip = await self._repository.get_trip(expected_trip_id)
        if trip is None or trip.owner_user_id != owner_user_id:
            raise TelegramDemoError("recovery proof belongs to another traveler")
        actions = await self._repository.list_actions(incident_id)
        if not actions or any(
            action.execution_status != ActionStatus.VERIFIED for action in actions
        ):
            raise TelegramDemoError("recovery proof is incomplete")
        labels = {
            "FLIGHT_CHANGE": "replacement flight verified",
            "TRANSFER_ADJUSTMENT": "airport transfer adjusted",
            "SERVICE_MESSAGE": "late-arrival note prepared",
            "CALENDAR_UPDATE": "trip timeline refreshed",
        }
        verified = "\n".join(
            f"✓ {labels.get(action.category.value, action.category.value.lower())}"
            for action in actions
        )
        return TelegramView(
            text=(
                "<b>RECOVERY RECEIPT</b>\n"
                "<code>4/4 RECOVERY STEPS VERIFIED</code>\n\n"
                f"{verified}\n"
                "✓ itinerary conflict check passed\n\n"
                "<b>Authority trace</b>\n"
                "3 safe actions → traveler policy\n"
                "1 paid flight change → exact approval\n"
                "0 actions outside granted authority\n\n"
                "Every effect has a persistent idempotency receipt. A retry or duplicate "
                "tap cannot repeat the provider action."
            ),
            parse_mode="HTML",
            button_rows=[
                [
                    TelegramButton(
                        text="Review €250 claim", callback_data=f"demo:claim:{incident_id}"
                    )
                ]
            ],
        )

    async def _claim_view(
        self, *, incident_id: str, owner_user_id: str, telegram_user_id: str
    ) -> TelegramView:
        from app.services.compensation import PassengerCompensationService

        incident = await self._repository.get_incident(incident_id)
        expected_trip_id = f"telegram-demo-trip:{telegram_user_id}"
        if incident is None or incident.trip_id != expected_trip_id:
            raise TelegramDemoError("compensation claim is not available")
        trip = await self._repository.get_trip(expected_trip_id)
        if trip is None or trip.owner_user_id != owner_user_id:
            raise TelegramDemoError("trip belongs to another traveler")

        assessment, claim_letter = PassengerCompensationService.assess_incident(
            incident=incident, trip=trip, passenger_name="Traveler"
        )
        if claim_letter is None or not assessment.eligible:
            return TelegramView(
                text="This disruption does not meet statutory compensation thresholds.",
                button_rows=[
                    [
                        TelegramButton(
                            text="Back to proof", callback_data=f"demo:proof:{incident_id}"
                        )
                    ]
                ],
            )

        amount_str = (
            f"€{assessment.amount.minor_units / 100:.2f}" if assessment.amount else "€250.00"
        )
        return TelegramView(
            text=(
                "<b>EU261 STATUTORY COMPENSATION</b>\n"
                f"<code>CLAIM ELIGIBLE · {amount_str}</code>\n\n"
                f"<b>Flight</b>: {claim_letter.flight_number} ({claim_letter.route})\n"
                f"<b>Legal Basis</b>: {claim_letter.legal_basis}\n"
                f"<b>Distance</b>: {assessment.distance_km} km\n"
                f"<b>Statutory Entitlement</b>: {amount_str} per passenger\n\n"
                "<b>Reviewable claim draft</b>:\n"
                "<i>Nothing is sent automatically. Review the facts and send it to "
                "the airline yourself.</i>\n\n"
                f"<pre>{escape(claim_letter.body_en)}</pre>\n\n"
                "The claim letter contains the flight details, legal citations, evidence "
                "timestamps and a 14-day statutory deadline."
            ),
            parse_mode="HTML",
            button_rows=[
                [
                    TelegramButton(
                        text="Back to receipt", callback_data=f"demo:proof:{incident_id}"
                    )
                ]
            ],
        )

    async def _ensure_demo_trip(
        self, owner_user_id: str, telegram_user_id: str, now: datetime
    ) -> str:
        trip_id = f"telegram-demo-trip:{telegram_user_id}"
        existing = await self._repository.get_trip(trip_id)
        if existing is not None and existing.owner_user_id != owner_user_id:
            raise TelegramDemoError("demo trip belongs to another traveler")
        if existing is None:
            await self._repository.seed_trip(
                build_owned_demo_trip(owner_user_id=owner_user_id, trip_id=trip_id)
            )
        await self._readiness.seed_demo_documents(
            trip_id=trip_id, owner_user_id=owner_user_id, now=now
        )
        return trip_id

    async def _shadow_view(
        self, owner_user_id: str, telegram_user_id: str, now: datetime
    ) -> TelegramView:
        from app.services.predictive_shadow_engine import PredictiveShadowEngine

        trip_id = await self._ensure_demo_trip(owner_user_id, telegram_user_id, now)
        trip = await self._repository.get_trip(trip_id)
        if trip is None:
            raise TelegramDemoError("demo trip not found")

        tree = PredictiveShadowEngine.evaluate_trip_risk(trip, now=now)
        active_hold = tree.active_holds[0] if tree.active_holds else None
        assessment = tree.assessments[0] if tree.assessments else None

        prob_str = f"{assessment.probability_of_miss * 100:.0f}%" if assessment else "74%"
        hold_str = (
            f"✓ <b>Preemptive 24h Free Fare Lock ({active_hold.hold_id})</b>\n"
            f"✓ Alternative: Flight {active_hold.alternative_flight} (MUC → LIS)\n"
            f"✓ Locked Fare: <b>€{active_hold.locked_rebooking_price.minor_units / 100:.2f}</b>\n"
            f"✓ Market Surge Protection: €{active_hold.surge_market_price.minor_units / 100:.2f}"
            if active_hold
            else "• Contingency holds ready on standby"
        )

        return TelegramView(
            text=(
                "<b>PREDICTIVE SHADOW TREE EXECUTION</b>\n"
                f"<code>BAYESIAN THREAT SHIELD · RISK {prob_str}</code>\n\n"
                "<b>Bayesian Risk Assessment</b>:\n"
                "• Munich (MUC) connection buffer: 55 min (tight slack)\n"
                f"• Calculated disruption probability: <b>{prob_str} (HIGH RISK)</b>\n"
                "• Inbound aircraft & hub rush-hour congestion factored in\n\n"
                "<b>Hot Contingency Layer</b>:\n"
                f"{hold_str}\n\n"
                "⚡️ <b>Zero-Latency Recovery</b>:\n"
                "If a delay occurs, promotion is instant (0 seconds). "
                "The seat is already locked at the low pre-surge price."
            ),
            parse_mode="HTML",
            button_rows=[
                [TelegramButton(text="Return to live simulation", callback_data="demo:start")],
                [TelegramButton(text="Launch +195 min disruption", callback_data="demo:trigger")],
            ],
        )

    async def _guardian_view(self, owner_user_id: str, telegram_user_id: str) -> TelegramView:
        from app.models.guardian import TravelerTravelProfile
        from app.services.guardian import TravelGuardianService

        report = TravelGuardianService.screen_connection(
            connection_id="conn-waw-muc-lis",
            origin_airport="WAW",
            hub_airport="MUC",
            destination_airport="LIS",
            scheduled_buffer_minutes=55,
            profile=TravelerTravelProfile(
                citizenship_iso2="DE", has_checked_bags=True, bag_count=1
            ),
        )

        return TelegramView(
            text=(
                "<b>TRAVEL INVARIANT GUARDIAN</b>\n"
                "<code>SCREENING PREVIEW · INTRA-SCHENGEN</code>\n\n"
                f"<b>Visa Screening</b>: {report.visa.status}\n"
                f"• {report.visa.notes[0]}\n\n"
                f"<b>Baggage Transfer</b>: {report.baggage.status}\n"
                f"• Estimated MBCT: {report.baggage.estimated_mbct_minutes} min\n"
                f"• Buffer slack: +10 min (55m scheduled vs 45m MBCT)\n\n"
                "<i>Screening tool only. Not official immigration or airline advice.</i>\n\n"
                "Live graph and delay slider available at <code>/simulator</code>."
            ),
            parse_mode="HTML",
            button_rows=[
                [TelegramButton(text="Return to live simulation", callback_data="demo:start")],
                [TelegramButton(text="Launch +195 min disruption", callback_data="demo:trigger")],
            ],
        )

    @staticmethod
    def trip_view() -> TelegramView:
        return TelegramView(
            text=(
                "<b>Trip Watch is active</b>\n"
                "<code>JUDGE STORY · READY FOR A LIVE SIGNAL</code>\n\n"
                "<b>Warsaw → Munich → Lisbon</b>\n"
                "20 August · 2 flights · hotel · transfer\n\n"
                "<b>Already checked</b>\n"
                "✓ connection buffer · 55 min\n"
                "✓ Lisbon route weather · monitoring enabled\n"
                "✓ baggage transfer · feasible\n"
                "✓ passport transit screen · clear\n"
                "✓ Shadow fare lock · €34\n\n"
                "When an official airline signal changes this trip, I act on safe steps "
                "first and ask only for a paid decision.\n\n"
                "<i>Controlled demo signal — no real booking changes.</i>"
            ),
            parse_mode="HTML",
            button_rows=[
                [
                    TelegramButton(
                        text="Simulate verified +195 min delay",
                        callback_data="demo:trigger",
                    )
                ],
            ],
        )

    @staticmethod
    def lifecycle_view() -> TelegramView:
        return TelegramView(
            text=(
                "<b>THE AGENT MAP</b>\n"
                "<code>ONE MEMORY · FOUR OPERATING MODES</code>\n\n"
                "<b>01 · Prepare</b>\n"
                "Bookings, documents, check-in and schedule readiness\n\n"
                "<b>02 · Watch</b>\n"
                "Provider changes, live timing and downstream conflicts\n\n"
                "<b>03 · Recover</b>\n"
                "Safe autonomous actions, precise approval boundaries and verification\n\n"
                "<b>04 · Remember</b>\n"
                "Expenses, refunds, deposits and evidence until the trip truly closes\n\n"
                "You do not manage these modes. The trip state decides which one is active."
            ),
            parse_mode="HTML",
            button_rows=[
                [TelegramButton(text="Return to live simulation", callback_data="demo:start")],
                [TelegramButton(text="Run readiness scan", callback_data="demo:readiness")],
                [TelegramButton(text="Activate my agent", callback_data="onboard:setup")],
            ],
        )

    @staticmethod
    def readiness_view(ready: bool) -> TelegramView:
        if ready:
            return TelegramView(
                text=(
                    "<b>READINESS SCAN · READY</b>\n"
                    "<code>3/3 documents present</code>\n\n"
                    "✓ flight ticket available\n"
                    "✓ hotel confirmation available\n"
                    "✓ transfer voucher available\n\n"
                    "<b>Schedule guardian</b>\n"
                    "• Munich connection is tight: 55 minutes\n"
                    "• minimum required buffer: 45 minutes\n\n"
                    "No action is required now. The connection will remain monitored."
                ),
                parse_mode="HTML",
                button_rows=[
                    [TelegramButton(text="Back to demo trip", callback_data="demo:start")],
                    [TelegramButton(text="Trigger the delay", callback_data="demo:trigger")],
                ],
            )
        return TelegramView(
            text=(
                "<b>READINESS SCAN · NEEDS ATTENTION</b>\n"
                "<code>1 resolvable gap</code>\n\n"
                "✓ flight ticket available\n"
                "✓ hotel confirmation available\n"
                "! transfer voucher not found\n\n"
                "<b>Schedule guardian</b>\n"
                "• Munich connection is tight: 55 minutes\n"
                "• minimum required buffer: 45 minutes\n\n"
                "Only the missing voucher needs attention."
            ),
            parse_mode="HTML",
            button_rows=[
                [
                    TelegramButton(
                        text="Add demo transfer voucher",
                        callback_data="demo:add_voucher",
                    )
                ],
                [TelegramButton(text="Back to demo trip", callback_data="demo:start")],
            ],
        )

    @staticmethod
    def approval_view(result: RecoveryStartResult) -> TelegramView:
        if result.approval is None or result.approval_callback_token is None:
            raise TelegramDemoError("demo recovery did not produce an approval")
        token = result.approval_callback_token
        return TelegramView(
            text=(
                "<b>Connection at risk</b>\n"
                "<code>OFFICIAL DELAY · LO351 +195 MIN</code>\n\n"
                "Your Munich connection is no longer feasible.\n\n"
                "<b>I handled safely</b>\n"
                "✓ transfer adjusted\n"
                "✓ late-arrival note prepared\n"
                "✓ trip timeline refreshed\n"
                "✓ weather and baggage constraints rechecked\n\n"
                "One decision remains: the locked replacement flight is €34. "
                "Your auto limit is €20."
            ),
            parse_mode="HTML",
            button_rows=[
                [
                    TelegramButton(text="Approve +€34", callback_data=f"a:{token}"),
                    TelegramButton(text="Why this option?", callback_data=f"d:{token}"),
                ],
                [TelegramButton(text="Stop demo", callback_data=f"s:{token}")],
            ],
        )

    @staticmethod
    def expense_view(disruption_minor_units: int) -> TelegramView:
        return TelegramView(
            text=(
                "<b>COST MEMORY · 4/5</b>\n"
                "<code>RECEIPT LINKED TO DISRUPTION</code>\n\n"
                "<b>Lisbon Airport Taxi · €27.40</b>\n"
                "Category: Transport\n"
                "Confidence: 99%\n\n"
                "Linked to the Munich connection disruption.\n\n"
                f"<b>Disruption total: €{disruption_minor_units / 100:.2f}</b>\n"
                "• replacement flight: €34.00\n"
                "• airport taxi: €27.40\n\n"
                "In a real trip, low-confidence receipt fields would require confirmation."
            ),
            parse_mode="HTML",
            button_rows=[
                [TelegramButton(text="Continue · close the trip", callback_data="demo:closeout")],
                [TelegramButton(text="Open agent map", callback_data="demo:lifecycle")],
                [TelegramButton(text="Activate my agent", callback_data="onboard:setup")],
            ],
        )

    @staticmethod
    def closeout_view(*, closed: bool) -> TelegramView:
        if closed:
            return TelegramView(
                text=(
                    "<b>LIFECYCLE COMPLETE</b>\n"
                    "<code>TRIP STATE  CLOSED</code>\n\n"
                    "✓ hotel deposit received: €150.00\n"
                    "✓ airline refund received: €70.00\n"
                    "✓ disruption expenses recorded: €61.40\n"
                    "✓ no open financial or itinerary items\n\n"
                    "The agent stayed with this trip from readiness to recovery to settlement.\n\n"
                    "<b>You were interrupted once.</b> Everything else was handled or observed "
                    "inside the policy."
                ),
                parse_mode="HTML",
                button_rows=[
                    [TelegramButton(text="Activate my agent", callback_data="onboard:setup")],
                    [TelegramButton(text="Replay simulation", callback_data="demo:start")],
                ],
            )
        return TelegramView(
            text=(
                "<b>FINANCIAL TAIL · 5/5</b>\n"
                "<code>2 OPEN ITEMS · TRIP NOT CLOSED</code>\n\n"
                "<b>Hotel deposit · €150.00</b>\n"
                "Status: expected within 7 days\n\n"
                "<b>Airline refund · €70.00</b>\n"
                "Status: expected within 14 days\n\n"
                "The operational trip is over, but the agent is not done. It keeps the "
                "financial tail open until exact settlement is verified."
            ),
            parse_mode="HTML",
            button_rows=[
                [
                    TelegramButton(
                        text="Simulate both settlements",
                        callback_data="demo:settle_finance",
                    )
                ],
                [TelegramButton(text="Back to lifecycle", callback_data="demo:lifecycle")],
            ],
        )
