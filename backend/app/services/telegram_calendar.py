from __future__ import annotations

from datetime import datetime

from app.models.calendar import CalendarConnectionStatus
from app.models.enums import OnboardingStep
from app.models.telegram import TelegramButton, TelegramView
from app.services.calendar_oauth import CalendarOAuthError, CalendarOAuthService
from app.services.ports import IncidentRepository


class TelegramCalendarError(ValueError):
    pass


class TelegramCalendarService:
    def __init__(
        self,
        repository: IncidentRepository,
        oauth: CalendarOAuthService,
        *,
        redirect_uri: str,
    ) -> None:
        self._repository = repository
        self._oauth = oauth
        self._redirect_uri = redirect_uri

    async def handle(
        self,
        *,
        telegram_user_id: str,
        telegram_chat_id: str,
        callback_data: str,
        now: datetime,
    ) -> TelegramView:
        traveler = await self._repository.get_traveler(telegram_user_id)
        if (
            traveler is None
            or traveler.telegram_chat_id != telegram_chat_id
            or traveler.onboarding_step != OnboardingStep.COMPLETE
        ):
            raise TelegramCalendarError("complete onboarding before connecting Calendar")
        if callback_data == "calendar:connect":
            try:
                authorization = await self._oauth.create_authorization(
                    telegram_user_id=telegram_user_id,
                    telegram_chat_id=telegram_chat_id,
                    redirect_uri=self._redirect_uri,
                    now=now,
                )
            except CalendarOAuthError as exc:
                raise TelegramCalendarError(str(exc)) from exc
            return TelegramView(
                text=(
                    "Connect Google Calendar securely. Trip Watch requests only permission "
                    "to update events and never receives your Google password."
                ),
                buttons=[TelegramButton(text="Connect Google Calendar", url=authorization.url)],
            )
        if callback_data == "calendar:disconnect":
            try:
                await self._oauth.disconnect(telegram_user_id=telegram_user_id, now=now)
            except CalendarOAuthError as exc:
                raise TelegramCalendarError(str(exc)) from exc
            return TelegramView(
                text=(
                    "Google Calendar disconnected. Future recovery plans will pause before "
                    "calendar changes."
                ),
                buttons=[TelegramButton(text="Connect Calendar", callback_data="calendar:connect")],
            )
        if callback_data != "calendar:menu":
            raise TelegramCalendarError("unsupported Calendar connection callback")
        connection = await self._repository.get_calendar_connection(telegram_user_id)
        if connection is not None and connection.status == CalendarConnectionStatus.CONNECTED:
            return TelegramView(
                text="Google Calendar connected • updates are verified after every change.",
                buttons=[
                    TelegramButton(text="Disconnect Calendar", callback_data="calendar:disconnect")
                ],
            )
        return TelegramView(
            text=(
                "Google Calendar is not connected. The agent can still watch your trip, "
                "but it will pause before calendar updates."
            ),
            buttons=[TelegramButton(text="Connect Calendar", callback_data="calendar:connect")],
        )
