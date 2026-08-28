from __future__ import annotations

import re
from datetime import UTC, datetime
from html import escape

from app.agents.judge_chat import VertexJudgeChat
from app.models.enums import OnboardingStep, TripStatus
from app.models.monitoring import MonitoringCoverage
from app.models.telegram import TelegramButton, TelegramView
from app.services.ports import IncidentRepository
from app.services.telegram_planning import TelegramPlanningService


class TelegramConversationService:
    """Safe natural-language front door for the Telegram agent.

    Text can navigate, explain and show the traveler's own state, but it never
    grants authority or executes a recovery action. Consequential work still
    starts only from validated monitoring facts and policy/approval workflows.
    """

    def __init__(
        self,
        repository: IncidentRepository,
        judge_chat: VertexJudgeChat | None = None,
        planning: TelegramPlanningService | None = None,
    ) -> None:
        self._repository = repository
        self._judge_chat = judge_chat
        self._planning = planning

    async def handle(
        self,
        *,
        telegram_user_id: str,
        telegram_chat_id: str,
        text: str,
        now: datetime | None = None,
    ) -> TelegramView:
        traveler = await self._repository.get_traveler(telegram_user_id)
        if traveler is None or traveler.telegram_chat_id != telegram_chat_id:
            return TelegramView(
                text="Start with /start and I’ll set up your private trip recovery agent."
            )
        if traveler.onboarding_step != OnboardingStep.COMPLETE:
            return TelegramView(
                text="Finish your recovery settings first, then I can protect a trip for you."
            )

        normalized = " ".join(text.casefold().split())
        if self.looks_like_urgent_trip_issue(normalized):
            return self._urgent_help_view(normalized)
        # A planning draft can be waiting for one missing detail (currently the
        # departure city). Continue that state before interpreting the message as
        # a generic question; otherwise a reply such as "Kyiv" falls through to
        # the judge chat and the user never receives the three options.
        if self._planning is not None:
            pending_draft = await self._repository.get_trip_draft(telegram_user_id)
            if (
                pending_draft is not None
                and pending_draft.telegram_chat_id == telegram_chat_id
                and pending_draft.planning_context is not None
                and pending_draft.planning_context.origin is None
            ):
                return await self._planning.handle_message(
                    telegram_user_id=telegram_user_id,
                    telegram_chat_id=telegram_chat_id,
                    text=text,
                    now=now or datetime.now(UTC),
                )
        if self._matches(
            normalized, "погод", "weather", "отслеж", "what do you monitor", "coverage"
        ):
            return self._coverage_view()
        if self._matches(
            normalized, "статус", "мои поезд", "my trip", "trip status", "что с поезд"
        ):
            return await self._status_view(traveler.user_id)
        if self._matches(normalized, "добав", "add trip", "новая поезд", "new trip"):
            return TelegramView(
                text=(
                    "Send the itinerary I should protect: a PDF ticket, booking email, "
                    "screenshot or Apple Wallet pass."
                )
            )
        if self._matches(normalized, "сплан", "plan trip", "plan a trip", "маршрут", "бюджет"):
            if self._planning is not None:
                return await self._planning.handle_message(
                    telegram_user_id=telegram_user_id,
                    telegram_chat_id=telegram_chat_id,
                    text=text,
                    now=now or datetime.now(UTC),
                )
            return TelegramView(text="Trip planning is temporarily unavailable. Try again shortly.")
        if self._matches(normalized, "рекомендац", "travel ideas", "идеи поезд"):
            if self._planning is not None:
                return await self._planning.handle(
                    telegram_user_id=telegram_user_id,
                    telegram_chat_id=telegram_chat_id,
                    callback_data="plan:preferences",
                    now=now or datetime.now(UTC),
                )
        if self._matches(normalized, "gemini", "ключ", "key", "подключ"):
            return TelegramView(
                text="Connect your own Gemini key for personal Search Watch monitoring.",
                buttons=[TelegramButton(text="Connect Gemini", callback_data="ai:menu")],
            )
        if self._matches(normalized, "calendar", "календар"):
            return TelegramView(
                text=(
                    "Use /settings to connect Google Calendar and review the actions I may update."
                ),
            )
        if self._matches(normalized, "gmail", "email", "почт"):
            return TelegramView(
                text=(
                    "Use /settings to connect Gmail. I request draft-only permission: "
                    "I can prepare "
                    "a hotel late-arrival email, but I cannot read your inbox or send it for you."
                ),
            )
        if self._matches(normalized, "настрой", "settings", "лимит", "limit"):
            return TelegramView(
                text=(
                    "Your authority settings decide what I may do automatically. "
                    "Use /settings to review them."
                )
            )
        if self._matches(
            normalized,
            "компенсац",
            "compensation",
            "eu261",
            "uk261",
            "претензи",
            "claim",
            "возврат денег",
            "flight delay compensation",
        ):
            return self._compensation_view()
        if self._judge_chat is not None:
            answer = await self._judge_chat.answer(
                text=text,
                now=now or datetime.now(UTC),
                telegram_user_id=telegram_user_id,
                trip_context=await self._gemini_trip_context(traveler.user_id),
            )
            if answer:
                return TelegramView(text=escape(answer), parse_mode="HTML")
        return TelegramView(
            text=(
                "I’m your trip recovery agent. You can write naturally — for example: “what "
                "do you monitor?”, “my trip status”, “plan Lisbon”, “weather”, “add a trip”, "
                "or “connect Gemini”.\n\n"
                "I never execute a booking, payment, or major itinerary change from a chat "
                "message. Those actions require a validated disruption and your standing policy."
            )
        )

    async def handle_callback(
        self, *, telegram_user_id: str, telegram_chat_id: str, callback_data: str
    ) -> TelegramView:
        traveler = await self._repository.get_traveler(telegram_user_id)
        if (
            traveler is None
            or traveler.telegram_chat_id != telegram_chat_id
            or traveler.onboarding_step != OnboardingStep.COMPLETE
        ):
            return TelegramView(text="Finish onboarding before using your trip agent.")
        if callback_data == "chat:status":
            return await self._status_view(traveler.user_id)
        if callback_data == "chat:coverage":
            return self._coverage_view()
        if callback_data == "chat:plan" and self._planning is not None:
            return await self._planning.handle(
                telegram_user_id=telegram_user_id,
                telegram_chat_id=telegram_chat_id,
                callback_data="plan:start",
                now=datetime.now(UTC),
            )
        return TelegramView(text="That conversation action is no longer available.")

    async def _status_view(self, owner_user_id: str) -> TelegramView:
        trips = await self._repository.list_trips_for_owner(owner_user_id)
        if not trips:
            return TelegramView(
                text=(
                    "You have no protected trip yet. Add one and I’ll create its focused "
                    "watchpoints. Send the booking here when you are ready."
                )
            )
        lines = []
        planned_count = 0
        for trip in trips[:5]:
            if trip.status == TripStatus.PLANNED:
                planned_count += 1
                lines.append(f"• Saved plan: {trip.origin} → {trip.destination} · not booked yet")
                continue
            watchpoints = await self._repository.list_watchpoints(trip.trip_id)
            subscriptions = await self._repository.list_monitoring_subscriptions(trip.trip_id)
            degraded = sum(1 for watchpoint in watchpoints if watchpoint.last_error_at is not None)
            degraded += sum(
                1
                for subscription in subscriptions
                if subscription.coverage == MonitoringCoverage.MONITORING_DEGRADED
            )
            health = f" · {degraded} checks need attention" if degraded else " · monitoring healthy"
            lines.append(
                f"• {trip.origin} → {trip.destination} · {len(trip.items)} itinerary item(s) · "
                f"{len(watchpoints)} watchpoints{health}"
            )
        suffix = "\n• More trips are protected." if len(trips) > 5 else ""
        heading = "Your trips"
        if planned_count and planned_count == len(trips):
            heading = "Your saved plans"
        return TelegramView(text=heading + "\n" + "\n".join(lines) + suffix)

    async def _gemini_trip_context(self, owner_user_id: str) -> str:
        """Give Gemini only the compact owned context it needs to sound helpful.

        The model never receives source documents, PNRs, contact data, or a policy
        token.  Its job is explanation and routing; deterministic workflows retain
        all authority to change an itinerary.
        """

        trips = await self._repository.list_trips_for_owner(owner_user_id)
        if not trips:
            return "No saved or protected trip."
        lines: list[str] = []
        for trip in trips[:3]:
            if trip.status == TripStatus.PLANNED:
                lines.append(
                    f"Saved estimate only: {trip.origin} to {trip.destination}; not booked."
                )
                continue
            watchpoints = await self._repository.list_watchpoints(trip.trip_id)
            degraded = sum(point.last_error_at is not None for point in watchpoints)
            coverage = "monitoring healthy" if not degraded else f"{degraded} checks need attention"
            lines.append(
                f"Protected: {trip.origin} to {trip.destination}; "
                f"{len(trip.items)} itinerary items; {len(watchpoints)} watchpoints; {coverage}."
            )
        return "\n".join(lines)

    @staticmethod
    def _coverage_view() -> TelegramView:
        return TelegramView(
            text=(
                "I watch the few things that can break a trip:\n"
                "• flight status and airline notices\n"
                "• airport disruption, strikes and closures\n"
                "• route-relevant weather warnings\n"
                "• hotel check-in/closure notices\n"
                "• transfers, road closures and strikes\n"
                "• activities with changed hours or cancellation\n\n"
                "I keep the source for every signal. A headline never changes your plan on "
                "its own: I first verify that it affects your exact itinerary."
            )
        )

    @staticmethod
    def looks_like_urgent_trip_issue(text: str) -> bool:
        """Recognise a traveler asking for help mid-trip before ticket intake.

        A forwarded airline alert often includes a flight number and an IATA route,
        so it can otherwise look like a new itinerary.  In an urgent moment the
        useful first response is to collect the change, not to silently overwrite
        the protected trip.
        """

        normalized = " ".join(text.casefold().split())
        if (
            ("flight" in normalized and any(word in normalized for word in ("delay", "cancel")))
            or ("рейс" in normalized and any(word in normalized for word in ("задерж", "отмен")))
            or (
                "connection" in normalized
                and any(word in normalized for word in ("miss", "infeasible", "not make"))
            )
            or (
                "пересад" in normalized
                and any(word in normalized for word in ("опоздал", "не успева", "сорвал"))
            )
        ):
            return True
        if re.search(
            r"\b(?:connection|transfer)\b.*\b\d{1,2}\s*(?:min|minutes)\b",
            normalized,
        ):
            return True
        return any(
            phrase in normalized
            for phrase in (
                "flight delayed",
                "flight cancelled",
                "flight canceled",
                "missed my connection",
                "missed connection",
                "my connection is",
                "gate changed",
                "gate change",
                "transfer hasn't",
                "transfer has not",
                "hotel won't",
                "hotel will not",
                "lost my bag",
                "lost baggage",
                "my bag is",
                "рейс задерж",
                "рейс отмен",
                "опоздал на пересад",
                "не успеваю на пересад",
                "гейт измен",
                "трансфер не",
                "отель не",
                "багаж не",
                "потерял багаж",
            )
        )

    @staticmethod
    def _urgent_help_view(normalized: str) -> TelegramView:
        if re.search(r"\b(?:connection|transfer)\b.*\b\d{1,2}\s*(?:min|minutes)\b", normalized):
            focus = (
                "Head toward your departure gate now and stay airside. If the gate or terminal "
                "has changed, ask the airline desk while you move — do not make a rushed "
                "new booking. "
                "Send your boarding pass and delay notice so I can compare the revised timing, "
                "baggage risk and next safe option."
            )
        elif any(word in normalized for word in ("bag", "багаж")):
            focus = (
                "Keep your baggage receipt and send a photo of the airline or airport notice. "
                "I will assess the connection risk and keep the baggage issue linked to your trip."
            )
        elif any(word in normalized for word in ("hotel", "отель", "transfer", "трансфер")):
            focus = (
                "Send the provider message or a screenshot with the new time. I will check the "
                "downstream impact and prepare the safest next step."
            )
        else:
            focus = (
                "Send the airline or airport message, boarding pass, or a screenshot. If you know "
                "the new time, include it too."
            )
        return TelegramView(
            text=(
                "I’m with you. Don’t make a rushed booking yet.\n\n"
                f"{focus}\n\n"
                "I will verify the change against the protected itinerary and only ask you when "
                "money or a real choice is involved. Keep receipts for any essential expense."
            )
        )

    @staticmethod
    def _compensation_view() -> TelegramView:
        return TelegramView(
            text=(
                "<b>AIR PASSENGER COMPENSATION (EU261 / UK261)</b>\n\n"
                "If your flight is delayed by <b>3+ hours</b> or cancelled without 14 days notice, "
                "you may be entitled to cash compensation:\n"
                "• Flights ≤ 1,500 km: <b>€250 / £220</b>\n"
                "• Flights 1,500–3,500 km: <b>€400 / £350</b>\n"
                "• Flights > 3,500 km: <b>€600 / £520</b>\n\n"
                "When a disruption occurs on your protected trip, I automatically calculate "
                "your statutory entitlement and draft a reviewable Letter of Claim with the "
                "evidence and source links attached. You approve it before sending."
            ),
            parse_mode="HTML",
        )

    @staticmethod
    def _matches(text: str, *needles: str) -> bool:
        return any(needle in text for needle in needles)
