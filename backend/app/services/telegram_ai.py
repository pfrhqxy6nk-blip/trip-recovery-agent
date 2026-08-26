from __future__ import annotations

from datetime import datetime
from urllib.parse import urlencode

from app.models.ai_connection import AiConnectionStatus
from app.models.enums import OnboardingStep
from app.models.telegram import TelegramButton, TelegramView
from app.services.ai_connections import AiConnectionError, AiConnectionService
from app.services.ports import IncidentRepository


class TelegramAiConnectionError(ValueError):
    pass


class TelegramAiConnectionService:
    AI_STUDIO_URL = "https://aistudio.google.com/app/apikey"

    def __init__(
        self,
        repository: IncidentRepository,
        connections: AiConnectionService,
        connection_base_url: str,
    ) -> None:
        self._repository = repository
        self._connections = connections
        self._connection_base_url = connection_base_url.rstrip("/")

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
            raise TelegramAiConnectionError("complete onboarding before connecting Gemini")
        if callback_data == "ai:connect":
            handoff = await self._connections.create_handoff(
                telegram_user_id=telegram_user_id,
                telegram_chat_id=telegram_chat_id,
                now=now,
            )
            query = urlencode(
                {
                    "token": handoff.token,
                    "telegram_user_id": telegram_user_id,
                    "telegram_chat_id": telegram_chat_id,
                }
            )
            return TelegramView(
                text=(
                    "Connect your Gemini account securely. Create a key in Google AI Studio, "
                    "then paste it only on the one-time HTTPS page. Never send it in Telegram."
                ),
                button_rows=[
                    [TelegramButton(text="Get a Gemini API key", url=self.AI_STUDIO_URL)],
                    [
                        TelegramButton(
                            text="Connect securely",
                            # The handoff stays in the URL fragment so proxies/access logs
                            # never receive it during the page GET.
                            url=f"{self._connection_base_url}#{query}",
                        )
                    ],
                ],
            )
        if callback_data == "ai:disconnect":
            try:
                await self._connections.disconnect(telegram_user_id=telegram_user_id, now=now)
            except AiConnectionError as exc:
                raise TelegramAiConnectionError(str(exc)) from exc
            return TelegramView(
                text=(
                    "Gemini disconnected from this agent. You can also revoke the original "
                    "key in Google AI Studio."
                ),
                buttons=[TelegramButton(text="Connect again", callback_data="ai:connect")],
            )
        if callback_data != "ai:menu":
            raise TelegramAiConnectionError("unsupported Gemini connection callback")
        connection = await self._repository.get_ai_connection(telegram_user_id)
        if connection is not None and connection.status == AiConnectionStatus.CONNECTED:
            return TelegramView(
                text=f"Gemini connected • {connection.key_fingerprint}",
                buttons=[TelegramButton(text="Disconnect Gemini", callback_data="ai:disconnect")],
            )
        return TelegramView(
            text=(
                "Connect your own Gemini API key so model usage follows your Google quota "
                "and billing configuration."
            ),
            buttons=[TelegramButton(text="Connect Gemini", callback_data="ai:connect")],
        )
