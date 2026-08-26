from __future__ import annotations

from datetime import UTC, datetime
from html import escape

from app.agents.judge_chat import VertexJudgeChat
from app.models.enums import OnboardingStep
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
        if self._matches(normalized, "погод", "weather", "отслеж", "what do you monitor"):
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
            )
            if answer:
                return TelegramView(
                    text=f"<b>Trip Watch · sourced answer</b>\n\n{escape(answer)}",
                    parse_mode="HTML",
                )
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
        for trip in trips[:5]:
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
        return TelegramView(text="Your protected trips\n" + "\n".join(lines) + suffix)

    @staticmethod
    def _coverage_view() -> TelegramView:
        return TelegramView(
            text=(
                "For each saved trip, I create focused watchpoints for:\n"
                "• flight status and airline notices\n"
                "• airport disruption, strikes and closures\n"
                "• route-relevant weather warnings\n"
                "• hotel check-in/closure notices\n"
                "• transfers, road closures and strikes\n"
                "• activities with changed hours or cancellation\n\n"
                "I preserve the source link. Public news is an alert to verify; only a "
                "validated, trip-specific fact can start recovery."
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
