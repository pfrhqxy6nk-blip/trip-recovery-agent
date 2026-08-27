from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation

from app.models.enums import OnboardingStep, PolicyMode
from app.models.money import Money
from app.models.policy import AutonomyPolicy
from app.models.telegram import TelegramButton, TelegramView, TravelerProfile
from app.services.ports import IncidentRepository


class OnboardingError(ValueError):
    pass


class TelegramOnboardingService:
    """Deterministic, resumable Telegram onboarding without a live Bot API dependency."""

    def __init__(self, repository: IncidentRepository, *, calendar_enabled: bool = False) -> None:
        self._repository = repository
        self._calendar_enabled = calendar_enabled

    async def start(
        self, *, telegram_user_id: str, telegram_chat_id: str, now: datetime
    ) -> TelegramView:
        traveler = await self._repository.get_traveler(telegram_user_id)
        if traveler is None:
            traveler = TravelerProfile(
                user_id=f"telegram:{telegram_user_id}",
                telegram_user_id=telegram_user_id,
                telegram_chat_id=telegram_chat_id,
                created_at=now,
                updated_at=now,
            )
            await self._repository.save_traveler(traveler)
        elif traveler.telegram_chat_id != telegram_chat_id:
            raise OnboardingError("Telegram account is already bound to a different chat")
        return self._view(traveler)

    async def callback(
        self,
        *,
        telegram_user_id: str,
        telegram_chat_id: str,
        callback_data: str,
        now: datetime,
    ) -> TelegramView:
        traveler = await self._repository.get_traveler(telegram_user_id)
        if traveler is None or traveler.telegram_chat_id != telegram_chat_id:
            raise OnboardingError("Telegram callback is not linked to this traveler")
        if not callback_data.startswith("onboard:"):
            raise OnboardingError("unsupported onboarding callback")
        action = callback_data.removeprefix("onboard:")
        if traveler.onboarding_step == OnboardingStep.SPENDING and action == "spend_custom":
            return TelegramView(
                text=(
                    "Send your per-disruption limit as /limit 35 (EUR). "
                    "Choose any amount from €1 to €500, with up to two decimal places."
                )
            )
        updated = traveler.model_copy(deep=True)
        updated.updated_at = now
        self._apply(updated, action)
        if action == "activate":
            policy = self.policy(updated)
            if not await self._repository.activate_traveler_policy(traveler=updated, policy=policy):
                raise OnboardingError("could not activate this policy version")
        else:
            await self._repository.save_traveler(updated)
        return self._view(updated)

    async def custom_spending_limit(
        self,
        *,
        telegram_user_id: str,
        telegram_chat_id: str,
        command: str,
        now: datetime,
    ) -> TelegramView:
        traveler = await self._repository.get_traveler(telegram_user_id)
        if (
            traveler is None
            or traveler.telegram_chat_id != telegram_chat_id
            or traveler.onboarding_step != OnboardingStep.SPENDING
        ):
            raise OnboardingError("a custom limit is not expected now")
        raw_amount = command.removeprefix("/limit").strip().replace(",", ".")
        try:
            amount = Decimal(raw_amount)
        except InvalidOperation as exc:
            raise OnboardingError("use /limit followed by an amount from 1 to 500") from exc
        if not amount.is_finite() or amount < 1 or amount > 500:
            raise OnboardingError("custom limit must be between €1 and €500")
        minor_units = amount * 100
        if minor_units != minor_units.to_integral_value():
            raise OnboardingError("custom limit may have at most two decimal places")
        updated = traveler.model_copy(deep=True)
        updated.automatic_spending_enabled = True
        updated.incident_spending_limit = Money(currency="EUR", minor_units=int(minor_units))
        updated.onboarding_step = OnboardingStep.BOUNDARY
        updated.updated_at = now
        await self._repository.save_traveler(updated)
        return self._view(updated)

    async def settings(self, *, telegram_user_id: str, telegram_chat_id: str) -> TelegramView:
        traveler = await self._repository.get_traveler(telegram_user_id)
        if traveler is None or traveler.telegram_chat_id != telegram_chat_id:
            raise OnboardingError("Telegram account is not active")
        if traveler.onboarding_step != OnboardingStep.COMPLETE:
            return self._view(traveler)
        return TelegramView(text=self._summary_text(traveler, active=True))

    def _apply(self, traveler: TravelerProfile, action: str) -> None:
        step = traveler.onboarding_step
        if action == "restart" and step == OnboardingStep.COMPLETE:
            traveler.onboarding_step = OnboardingStep.CALENDAR
            return
        if action == "setup" and step == OnboardingStep.COMPLETE:
            return
        if step == OnboardingStep.PROMISE and action == "setup":
            traveler.onboarding_step = OnboardingStep.CALENDAR
            return
        if step == OnboardingStep.CALENDAR and action in {"calendar_auto", "calendar_ask"}:
            traveler.calendar_mode = PolicyMode.AUTO if action.endswith("auto") else PolicyMode.ASK
            traveler.onboarding_step = OnboardingStep.SERVICE_MESSAGES
            return
        if step == OnboardingStep.SERVICE_MESSAGES and action in {"service_auto", "service_ask"}:
            traveler.service_message_mode = (
                PolicyMode.AUTO if action.endswith("auto") else PolicyMode.ASK
            )
            traveler.onboarding_step = OnboardingStep.REVERSIBLE_CHANGES
            return
        if step == OnboardingStep.REVERSIBLE_CHANGES and action in {
            "reversible_auto",
            "reversible_ask",
        }:
            traveler.reversible_change_mode = (
                PolicyMode.AUTO if action.endswith("auto") else PolicyMode.ASK
            )
            traveler.onboarding_step = OnboardingStep.SPENDING
            return
        if step == OnboardingStep.SPENDING and action in {
            "spend_none",
            "spend_20",
            "spend_50",
            "spend_100",
        }:
            traveler.automatic_spending_enabled = action != "spend_none"
            limits = {"spend_20": 2_000, "spend_50": 5_000, "spend_100": 10_000}
            traveler.incident_spending_limit = (
                None
                if action == "spend_none"
                else Money(currency="EUR", minor_units=limits[action])
            )
            traveler.onboarding_step = OnboardingStep.BOUNDARY
            return
        if step == OnboardingStep.BOUNDARY and action == "boundary_continue":
            traveler.onboarding_step = OnboardingStep.SUMMARY
            return
        if step == OnboardingStep.SUMMARY and action == "activate":
            traveler.active_policy_version = (traveler.active_policy_version or 0) + 1
            traveler.onboarding_step = OnboardingStep.COMPLETE
            return
        raise OnboardingError(f"callback {action!r} is not valid at step {step}")

    @staticmethod
    def policy(traveler: TravelerProfile) -> AutonomyPolicy:
        if traveler.onboarding_step != OnboardingStep.COMPLETE:
            raise OnboardingError("onboarding is incomplete")
        return AutonomyPolicy(
            policy_id=f"{traveler.user_id}:policy:{traveler.active_policy_version}",
            user_id=traveler.user_id,
            version=traveler.active_policy_version or 1,
            calendar_mode=traveler.calendar_mode,
            service_message_mode=traveler.service_message_mode,
            reversible_change_mode=traveler.reversible_change_mode,
            automatic_spending_enabled=traveler.automatic_spending_enabled,
            incident_spending_limit=traveler.incident_spending_limit,
            created_at=traveler.created_at,
            updated_at=traveler.updated_at,
        )

    def _view(self, traveler: TravelerProfile) -> TelegramView:
        step = traveler.onboarding_step
        if step == OnboardingStep.PROMISE:
            return TelegramView(
                text=(
                    "<b>Trip Agent</b>\n"
                    "<i>Your trip keeps working when plans stop working.</i>\n\n"
                    "I stay quiet in the background, watch connected trip facts, repair "
                    "safe consequences, and interrupt you only at a real decision boundary.\n\n"
                    "Choose how you want to begin. Both paths start with a short safety setup; "
                    "after that, just write to me in plain English."
                ),
                parse_mode="HTML",
                button_rows=[
                    [
                        TelegramButton(
                            text="Start my trip",
                            callback_data="onboard:setup",
                        )
                    ],
                    [TelegramButton(text="Plan a trip", callback_data="onboard:setup")],
                ],
            )
        if step == OnboardingStep.CALENDAR:
            return self._choice(
                "When trip times change, may I update your calendar?",
                ("Update automatically", "onboard:calendar_auto"),
                ("Ask first", "onboard:calendar_ask"),
            )
        if step == OnboardingStep.SERVICE_MESSAGES:
            return self._choice(
                "May I send practical trip messages, such as a late-arrival notice?",
                ("Message automatically", "onboard:service_auto"),
                ("Ask first", "onboard:service_ask"),
            )
        if step == OnboardingStep.REVERSIBLE_CHANGES:
            return self._choice(
                "May I automatically make free, fully reversible changes?",
                ("Change automatically", "onboard:reversible_auto"),
                ("Ask first", "onboard:reversible_ask"),
            )
        if step == OnboardingStep.SPENDING:
            return TelegramView(
                text="May I spend money on recovery without asking first?",
                buttons=[
                    TelegramButton(
                        text="No automatic spending", callback_data="onboard:spend_none"
                    ),
                    TelegramButton(text="Up to €20", callback_data="onboard:spend_20"),
                    TelegramButton(text="Up to €50", callback_data="onboard:spend_50"),
                    TelegramButton(text="Up to €100", callback_data="onboard:spend_100"),
                    TelegramButton(text="Custom amount", callback_data="onboard:spend_custom"),
                ],
            )
        if step == OnboardingStep.BOUNDARY:
            return TelegramView(
                text=(
                    "I will always ask before irreversible, penalty-bearing, ambiguous, "
                    "major itinerary, or over-limit changes."
                ),
                buttons=[
                    TelegramButton(text="Continue", callback_data="onboard:boundary_continue")
                ],
            )
        if step == OnboardingStep.SUMMARY:
            return TelegramView(
                text=self._summary_text(traveler, active=False),
                buttons=[TelegramButton(text="Activate agent", callback_data="onboard:activate")],
            )
        return TelegramView(
            text=(
                "Your recovery settings are active. Add a trip and I will contact you "
                "when a meaningful change needs attention. You can also ask me to build "
                "a sourced planning shortlist before you book.\n\n"
                "Send a booking PDF, screenshot, Apple Wallet pass or email, or write "
                "what you want to plan in plain English. Use /settings any time to review "
                "your policy."
            )
        )

    @staticmethod
    def _choice(text: str, first: tuple[str, str], second: tuple[str, str]) -> TelegramView:
        return TelegramView(
            text=text,
            buttons=[
                TelegramButton(text=first[0], callback_data=first[1]),
                TelegramButton(text=second[0], callback_data=second[1]),
            ],
        )

    @staticmethod
    def _summary_text(traveler: TravelerProfile, *, active: bool) -> str:
        spend = (
            "disabled"
            if traveler.incident_spending_limit is None
            else f"up to €{traveler.incident_spending_limit.minor_units // 100} per disruption"
        )
        heading = "Your agent is active" if active else "Your recovery settings"
        return (
            f"{heading}\n\n"
            "Notifications: always on\n"
            f"Calendar: {traveler.calendar_mode.value.lower()}\n"
            f"Services: {traveler.service_message_mode.value.lower()}\n"
            f"Free reversible changes: {traveler.reversible_change_mode.value.lower()}\n"
            f"Automatic recovery spending: {spend}\n"
            "Risky or major changes: always ask"
        )
