from __future__ import annotations

from datetime import UTC, datetime
from html import escape

from app.models.domain import Incident
from app.models.enums import ActionStatus, ApprovalStatus, IncidentStatus
from app.models.recovery import ApprovalRequest, PlannedAction, RecoveryPlan
from app.models.telegram import TelegramButton, TelegramView
from app.services.approval_tokens import callback_token_hash
from app.services.expenses import TripExpenseService
from app.services.ports import IncidentRepository
from app.workflows.recovery import RecoveryStartResult, RecoveryWorkflow


class RecoveryInteractionError(ValueError):
    pass


class TelegramRecoveryService:
    """Render and handle authority-bound recovery interactions for Telegram."""

    def __init__(
        self,
        repository: IncidentRepository,
        recovery_workflow: RecoveryWorkflow,
        expense_service: TripExpenseService | None = None,
    ) -> None:
        self._repository = repository
        self._workflow = recovery_workflow
        self._expenses = expense_service or TripExpenseService(repository)

    @staticmethod
    def awareness_view(plan: RecoveryPlan) -> TelegramView:
        arrival = plan.selected_option.arrival_at.strftime("%H:%M %Z").strip()
        return TelegramView(
            text=(
                "Trip change detected: Warsaw → Lisbon\n\n"
                "LO351 is now 105 minutes late. Your Munich connection is no longer "
                "feasible.\n\n"
                f"I found a recovery option arriving at {arrival}. I will now handle "
                "the changes allowed by your settings."
            )
        )

    async def approval_view(self, result: RecoveryStartResult) -> TelegramView:
        if result.approval is None or result.approval_callback_token is None:
            raise RecoveryInteractionError("approval callback is not available")
        approval = result.approval
        token = result.approval_callback_token
        amount = approval.maximum_authorized
        actions = await self._repository.list_actions(approval.incident_id)
        verified = [
            action for action in actions if action.execution_status == ActionStatus.VERIFIED
        ]
        pending = [
            action
            for action in actions
            if action.execution_status
            not in {ActionStatus.VERIFIED, ActionStatus.SKIPPED, ActionStatus.SUPERSEDED}
        ]
        handled = "\n".join(
            f"✓ {self._action_label(action.category.value)} — verified" for action in verified
        )
        waiting = "\n".join(
            f"{self._status_marker(action.execution_status)} "
            f"{self._action_label(action.category.value)} — "
            f"{self._approval_status_label(action, approval)}"
            for action in pending
        )
        return TelegramView(
            text=(
                "Your Munich connection is no longer feasible.\n\n"
                f"Already handled:\n{handled or '• No action verified yet'}\n\n"
                f"Still pending:\n{waiting or '• Nothing pending'}\n\n"
                f"Flight recovery: €{amount.minor_units / 100:.2f}\n"
                "Your automatic spending limit is lower, so I need your approval."
            ),
            button_rows=[
                [
                    TelegramButton(text="Approve recovery", callback_data=f"a:{token}"),
                    TelegramButton(text="Show details", callback_data=f"d:{token}"),
                ],
                [TelegramButton(text="Find another option", callback_data=f"f:{token}")],
                [TelegramButton(text="Stop recovery", callback_data=f"c:{token}")],
            ],
        )

    async def handle_callback(
        self,
        *,
        callback_data: str,
        telegram_user_id: str,
        telegram_chat_id: str,
        update_id: str,
        now: datetime,
    ) -> TelegramView:
        prefix, separator, token = callback_data.partition(":")
        if separator != ":" or prefix not in {"a", "d", "c", "s", "f", "r"} or not token:
            raise RecoveryInteractionError("unsupported recovery callback")
        approval = await self._repository.get_approval_by_callback_token_hash(
            callback_token_hash(token)
        )
        if approval is None:
            raise RecoveryInteractionError("recovery approval is stale or unknown")
        self._assert_owner(approval, telegram_user_id, telegram_chat_id)
        incident = await self._repository.get_incident(approval.incident_id)
        is_demo = incident is not None and incident.external_event_id.startswith("telegram-demo:")
        if prefix == "d":
            return await self._details_view(approval, token, now)
        if prefix == "c":
            if approval.status != ApprovalStatus.PENDING or approval.expires_at <= now:
                return await self.status_view(approval.incident_id, IncidentStatus.WAITING_APPROVAL)
            return self._stop_confirmation_view(token)
        if approval.expires_at <= now and prefix in {"a", "s"}:
            return TelegramView(
                text="This recovery quote expired. I need to find and verify a new option."
            )

        if prefix == "s":
            status = await self._workflow.decline(
                approval_id=approval.approval_id,
                callback_token_hash=approval.callback_token_hash,
                telegram_user_id=telegram_user_id,
                telegram_chat_id=telegram_chat_id,
                update_id=update_id,
                now=now,
            )
        elif prefix in {"f", "r"}:
            status = await self._workflow.request_replan(
                approval_id=approval.approval_id,
                callback_token_hash=approval.callback_token_hash,
                telegram_user_id=telegram_user_id,
                telegram_chat_id=telegram_chat_id,
                update_id=update_id,
                now=now,
                resume_cancelled=prefix == "r",
            )
        else:
            status = await self._workflow.approve(
                approval_id=approval.approval_id,
                callback_token_hash=approval.callback_token_hash,
                telegram_user_id=telegram_user_id,
                telegram_chat_id=telegram_chat_id,
                update_id=update_id,
                now=now,
                process_inline=is_demo,
            )
            consumed = await self._repository.get_approval(approval.approval_id)
            if (
                not is_demo
                and consumed is not None
                and consumed.status == ApprovalStatus.APPROVED
                and status != IncidentStatus.RECOVERED
            ):
                return TelegramView(
                    text=(
                        "Approval recorded. Recovery is queued from persistent state. "
                        "I’ll update this message after every provider result is verified."
                    )
                )
        view = await self.status_view(
            approval.incident_id,
            status,
            telegram_user_id=telegram_user_id,
            telegram_chat_id=telegram_chat_id,
        )
        if prefix == "s" and status == IncidentStatus.CANCELLED:
            view.button_rows = [
                [TelegramButton(text="Resume recovery", callback_data=f"r:{token}")]
            ]
        return view

    async def _details_view(
        self, approval: ApprovalRequest, callback_token: str, now: datetime
    ) -> TelegramView:
        actions = await self._repository.list_actions(approval.incident_id)
        verified = sum(action.execution_status == ActionStatus.VERIFIED for action in actions)
        state_lines = "\n".join(
            f"{self._status_marker(action.execution_status)} "
            f"{self._action_label(action.category.value)} — "
            f"{self._status_label(action.execution_status)}"
            for action in actions
        )
        amount = approval.maximum_authorized
        incident = await self._repository.get_incident(approval.incident_id)
        is_demo = incident is not None and incident.external_event_id.startswith("telegram-demo:")
        controls_enabled = approval.status == ApprovalStatus.PENDING and approval.expires_at > now
        controls = (
            self._decision_controls(
                callback_token=callback_token,
                amount_minor_units=amount.minor_units,
                demo=is_demo,
            )
            if controls_enabled
            else []
        )
        if is_demo:
            return TelegramView(
                text=(
                    "<b>DECISION TRACE</b>\n"
                    "<code>AUTHORITY BOUND TO ONE OPTION</code>\n\n"
                    "<b>Why this recovery</b>\n"
                    "• keeps the original route through Munich\n"
                    "• arrives Lisbon at 23:15 · 2h10 later\n"
                    "• exact incremental cost: €34\n"
                    "• downstream safe actions already verified: "
                    f"{verified}\n\n"
                    f"<b>Current action state</b>\n{state_lines}\n\n"
                    "<b>What approval permits</b>\n"
                    f"{len(approval.approved_action_ids)} replacement flight change\n"
                    f"Maximum charge · €{amount.minor_units / 100:.2f}\n"
                    f"Quote valid until · {approval.expires_at.strftime('%H:%M UTC')}\n\n"
                    "A different route, higher price, expired quote or new disruption "
                    "automatically cancels this authority."
                ),
                parse_mode="HTML",
                button_rows=controls,
            )
        return TelegramView(
            text=(
                "WHY THIS OPTION?\n\n"
                "It keeps the original route through Munich and arrives in Lisbon at 23:15.\n\n"
                f"Safe actions already verified: {verified}\n"
                f"Approval scope: {len(approval.approved_action_ids)} flight change\n"
                f"Maximum charge: €{amount.minor_units / 100:.2f}\n"
                f"Quote valid until: {approval.expires_at.strftime('%H:%M UTC')}\n\n"
                f"CURRENT ACTION STATE\n{state_lines}\n\n"
                "The approval is limited to this exact option and amount."
            ),
            button_rows=controls,
        )

    @staticmethod
    def _decision_controls(
        *, callback_token: str, amount_minor_units: int, demo: bool
    ) -> list[list[TelegramButton]]:
        return [
            [
                TelegramButton(
                    text=f"Approve exact +€{amount_minor_units / 100:.0f}",
                    callback_data=f"a:{callback_token}",
                )
            ],
            [TelegramButton(text="Find another option", callback_data=f"f:{callback_token}")],
            [
                TelegramButton(
                    text="Stop simulation" if demo else "Stop recovery",
                    callback_data=f"c:{callback_token}",
                )
            ],
        ]

    @staticmethod
    def _stop_confirmation_view(callback_token: str) -> TelegramView:
        return TelegramView(
            text=(
                "Stop this recovery?\n\n"
                "Verified safe updates will remain recorded. The pending flight change will "
                "not be made, and the trip may remain at risk."
            ),
            button_rows=[
                [TelegramButton(text="Yes, stop recovery", callback_data=f"s:{callback_token}")],
                [TelegramButton(text="Keep recovering", callback_data=f"d:{callback_token}")],
            ],
        )

    async def status_view(
        self,
        incident_id: str,
        status: IncidentStatus,
        *,
        telegram_user_id: str | None = None,
        telegram_chat_id: str | None = None,
    ) -> TelegramView:
        incident = await self._repository.get_incident(incident_id)
        is_demo = incident is not None and incident.external_event_id.startswith("telegram-demo:")
        if status == IncidentStatus.RECOVERED:
            await self._expenses.record_recovery_expenses(
                incident_id=incident_id,
                now=incident.updated_at if incident is not None else datetime.now(UTC),
            )
            actions = await self._repository.list_actions(incident_id)
            verified = [
                action.category.value.lower().replace("_", " ")
                for action in actions
                if action.execution_status == ActionStatus.VERIFIED
            ]
            checklist = "\n".join(f"✓ {label}" for label in verified)
            view = TelegramView(
                text=(
                    f"{'DEMO COMPLETE · ' if is_demo else ''}Trip recovered.\n\n"
                    f"{checklist}\n"
                    "✓ no unresolved itinerary conflicts\n\n"
                    "New arrival: 23:15\n"
                    "Recovery completed with one traveler decision."
                )
            )
            if is_demo:
                view.button_rows = [
                    [
                        TelegramButton(
                            text="Continue · cost memory",
                            callback_data="demo:expense",
                        )
                    ],
                    [
                        TelegramButton(
                            text="Open action proof",
                            callback_data=f"demo:proof:{incident_id}",
                        )
                    ],
                    [TelegramButton(text="Open agent map", callback_data="demo:lifecycle")],
                    [TelegramButton(text="Activate my agent", callback_data="onboard:setup")],
                ]
                view.text = (
                    "<b>RECOVERY VERIFIED · 3/5</b>\n"
                    "<code>TRIP GRAPH  COHERENT</code>\n\n"
                    f"{checklist}\n"
                    "✓ no unresolved itinerary conflicts\n\n"
                    "<b>New arrival · 23:15</b>\n\n"
                    "Observed 1 disruption · repaired 4 dependencies · "
                    "interrupted you 1 time.\n\n"
                    "Every provider effect was reread and verified before this message."
                )
                view.parse_mode = "HTML"
            elif (
                incident is not None
                and telegram_user_id is not None
                and telegram_chat_id is not None
            ):
                claim_button = await self._compensation_button(
                    incident=incident,
                    telegram_user_id=telegram_user_id,
                    telegram_chat_id=telegram_chat_id,
                )
                if claim_button is not None:
                    view.button_rows = [[claim_button]]
            return view
        if status == IncidentStatus.NEEDS_ATTENTION:
            return TelegramView(
                text=(
                    "Recovery needs attention. I could not verify every required change, "
                    "so I have not marked the trip recovered."
                )
            )
        if status == IncidentStatus.WAITING_APPROVAL:
            return TelegramView(
                text=(
                    "I could not apply this approval because the recovery is no longer "
                    "current. I will verify the latest trip state before asking again."
                )
            )
        if status == IncidentStatus.CANCELLED:
            view = TelegramView(
                text=(
                    "Recovery stopped. I did not make the pending flight change. "
                    "Previously verified safe updates remain recorded."
                )
            )
            if is_demo:
                view.button_rows = [
                    [TelegramButton(text="Replay demo", callback_data="demo:start")]
                ]
            return view
        if status in {IncidentStatus.PLANNING, IncidentStatus.NOTIFYING}:
            return TelegramView(
                text=(
                    "I’m checking the latest trip state and building a fresh recovery option. "
                    "I will ask again only if the new plan needs your decision."
                )
            )
        return TelegramView(text="Recovery approval received. Work is continuing safely.")

    async def claim_view(
        self,
        *,
        incident_id: str,
        telegram_user_id: str,
        telegram_chat_id: str,
    ) -> TelegramView:
        """Return an owner-bound, review-only compensation draft for a real incident."""

        from app.services.compensation import PassengerCompensationService

        incident = await self._repository.get_incident(incident_id)
        if incident is None or incident.status != IncidentStatus.RECOVERED:
            raise RecoveryInteractionError("compensation claim is not available")
        trip = await self._repository.get_trip(incident.trip_id)
        traveler = await self._repository.get_traveler(telegram_user_id)
        if (
            trip is None
            or trip.owner_user_id != f"telegram:{telegram_user_id}"
            or traveler is None
            or traveler.telegram_chat_id != telegram_chat_id
        ):
            raise RecoveryInteractionError("compensation claim belongs to another traveler")

        assessment, claim_letter = PassengerCompensationService.assess_incident(
            incident=incident, trip=trip, passenger_name="Traveler"
        )
        if claim_letter is None or not assessment.eligible:
            return TelegramView(
                text=(
                    "Compensation is not claim-ready yet. I kept this incident for review "
                    "because official cause, timing or jurisdiction evidence is incomplete."
                )
            )

        amount = assessment.amount
        amount_text = (
            f"{amount.currency} {amount.minor_units / 100:.2f}" if amount else "not determined"
        )
        sources = (
            "\n".join(f"• {escape(link)}" for link in claim_letter.source_links)
            or "• No external source link was attached"
        )
        timestamps = (
            "\n".join(
                f"• {escape(timestamp.isoformat())}"
                for timestamp in claim_letter.evidence_timestamps
            )
            or "• No source timestamp was attached"
        )
        body = escape(claim_letter.body_en)
        return TelegramView(
            text=(
                "<b>COMPENSATION CLAIM · REVIEW</b>\n"
                f"<code>{escape(claim_letter.jurisdiction.value)} · "
                f"{escape(amount_text)}</code>\n\n"
                f"<b>Flight</b>: {escape(claim_letter.flight_number)} "
                f"({escape(claim_letter.route)})\n"
                f"<b>Legal basis</b>: {escape(claim_letter.legal_basis)}\n\n"
                "<b>Evidence timestamps</b>\n"
                f"{timestamps}\n\n"
                "<b>Sources</b>\n"
                f"{sources}\n\n"
                "<b>Reviewable claim draft</b>\n"
                "<i>Nothing is sent automatically. Review the facts and send it to "
                "the airline yourself.</i>\n\n"
                f"<pre>{body}</pre>"
            ),
            parse_mode="HTML",
            button_rows=[[TelegramButton(text="Back to trip menu", callback_data="trip:menu")]],
        )

    async def _compensation_button(
        self,
        *,
        incident: Incident,
        telegram_user_id: str,
        telegram_chat_id: str,
    ) -> TelegramButton | None:
        """Build a claim control only after re-checking the Telegram owner binding."""

        from app.services.compensation import PassengerCompensationService

        trip = await self._repository.get_trip(incident.trip_id)
        traveler = await self._repository.get_traveler(telegram_user_id)
        if (
            trip is None
            or trip.owner_user_id != f"telegram:{telegram_user_id}"
            or traveler is None
            or traveler.telegram_chat_id != telegram_chat_id
        ):
            return None
        assessment, claim_letter = PassengerCompensationService.assess_incident(
            incident=incident, trip=trip, passenger_name="Traveler"
        )
        if claim_letter is None or not assessment.eligible:
            return None
        amount = assessment.amount
        amount_text = (
            f"{amount.currency} {amount.minor_units / 100:.0f}" if amount else "review claim"
        )
        return TelegramButton(
            text=f"Review compensation · {amount_text}",
            callback_data=f"claim:{incident.incident_id}",
        )

    @staticmethod
    def _action_label(category: str) -> str:
        return category.lower().replace("_", " ")

    @staticmethod
    def _status_marker(status: ActionStatus) -> str:
        if status == ActionStatus.VERIFIED:
            return "✓"
        if status in {ActionStatus.SKIPPED, ActionStatus.SUPERSEDED}:
            return "—"
        if status in {
            ActionStatus.FAILED,
            ActionStatus.FAILED_TERMINAL,
            ActionStatus.VERIFICATION_FAILED,
        }:
            return "!"
        return "•"

    @staticmethod
    def _status_label(status: ActionStatus) -> str:
        labels = {
            ActionStatus.VERIFIED: "verified",
            ActionStatus.SKIPPED: "skipped",
            ActionStatus.SUPERSEDED: "superseded",
            ActionStatus.FAILED: "unresolved",
            ActionStatus.FAILED_TERMINAL: "unresolved",
            ActionStatus.VERIFICATION_FAILED: "verification failed",
            ActionStatus.PENDING: "pending",
            ActionStatus.BLOCKED: "waiting for approval",
        }
        return labels.get(status, status.value.lower().replace("_", " "))

    @classmethod
    def _approval_status_label(cls, action: PlannedAction, approval: ApprovalRequest) -> str:
        if action.action_id in approval.approved_action_ids and action.execution_status in {
            ActionStatus.PENDING,
            ActionStatus.BLOCKED,
        }:
            return "pending approval"
        return cls._status_label(action.execution_status)

    @staticmethod
    def _assert_owner(
        approval: ApprovalRequest, telegram_user_id: str, telegram_chat_id: str
    ) -> None:
        if (
            approval.telegram_user_id != telegram_user_id
            or approval.telegram_chat_id != telegram_chat_id
        ):
            raise RecoveryInteractionError("recovery approval belongs to another traveler")
