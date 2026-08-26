from __future__ import annotations

from datetime import datetime

from app.models.enums import OnboardingStep
from app.models.gmail import GmailConnectionStatus
from app.models.telegram import TelegramButton, TelegramView
from app.services.gmail_oauth import GmailOAuthError, GmailOAuthService
from app.services.ports import IncidentRepository


class TelegramGmailError(ValueError):
    pass


class TelegramGmailService:
    """Minimal Gmail UI: connect/disconnect only; no mailbox browsing controls."""

    def __init__(
        self,
        repository: IncidentRepository,
        oauth: GmailOAuthService,
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
            raise TelegramGmailError("complete onboarding before connecting Gmail")
        if callback_data == "gmail:connect":
            try:
                authorization = await self._oauth.create_authorization(
                    telegram_user_id=telegram_user_id,
                    telegram_chat_id=telegram_chat_id,
                    redirect_uri=self._redirect_uri,
                    now=now,
                )
            except GmailOAuthError as exc:
                raise TelegramGmailError(str(exc)) from exc
            return TelegramView(
                text=(
                    "Connect Gmail securely. Trip Watch requests Gmail compose permission solely "
                    "to create a hotel-notice draft. This implementation has no send endpoint and "
                    "does not read your inbox."
                ),
                buttons=[TelegramButton(text="Connect Gmail", url=authorization.url)],
            )
        if callback_data == "gmail:disconnect":
            try:
                await self._oauth.disconnect(telegram_user_id=telegram_user_id, now=now)
            except GmailOAuthError as exc:
                raise TelegramGmailError(str(exc)) from exc
            return TelegramView(
                text="Gmail disconnected. Future hotel notices will remain in Telegram.",
                buttons=[TelegramButton(text="Connect Gmail", callback_data="gmail:connect")],
            )
        if callback_data != "gmail:menu":
            raise TelegramGmailError("unsupported Gmail connection callback")
        connection = await self._repository.get_gmail_connection(telegram_user_id)
        if connection is not None and connection.status == GmailConnectionStatus.CONNECTED:
            return TelegramView(
                text=(
                    "Gmail connected • Trip Watch can create a verified late-arrival draft "
                    "for a confirmed hotel contact. You review and send it yourself."
                ),
                buttons=[TelegramButton(text="Disconnect Gmail", callback_data="gmail:disconnect")],
            )
        return TelegramView(
            text=(
                "Gmail is not connected. Trip Watch can still protect your trip, but it will "
                "not create hotel-email drafts."
            ),
            buttons=[TelegramButton(text="Connect Gmail", callback_data="gmail:connect")],
        )
