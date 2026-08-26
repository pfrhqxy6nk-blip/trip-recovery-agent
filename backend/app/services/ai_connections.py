from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.models.ai_connection import AiConnection, AiConnectionHandoff, AiConnectionStatus
from app.services.approval_tokens import callback_token_hash
from app.services.ports import GeminiKeyValidator, IncidentRepository, SecretStore


class AiConnectionError(ValueError):
    pass


@dataclass(frozen=True)
class ConnectionHandoff:
    token: str
    expires_at: datetime


class AiConnectionService:
    def __init__(
        self,
        repository: IncidentRepository,
        secret_store: SecretStore,
        validator: GeminiKeyValidator,
    ) -> None:
        self._repository = repository
        self._secret_store = secret_store
        self._validator = validator

    async def create_handoff(
        self, *, telegram_user_id: str, telegram_chat_id: str, now: datetime
    ) -> ConnectionHandoff:
        token = secrets.token_urlsafe(32)
        expires_at = now + timedelta(minutes=10)
        stored = await self._repository.store_ai_handoff(
            AiConnectionHandoff(
                state_hash=callback_token_hash(token),
                telegram_user_id=telegram_user_id,
                telegram_chat_id=telegram_chat_id,
                expires_at=expires_at,
                created_at=now,
            )
        )
        if not stored:
            raise AiConnectionError("could not create a unique connection handoff")
        return ConnectionHandoff(token=token, expires_at=expires_at)

    async def complete(
        self,
        *,
        token: str,
        telegram_user_id: str,
        telegram_chat_id: str,
        api_key: str,
        now: datetime,
    ) -> AiConnection:
        handoff = await self._repository.consume_ai_handoff(
            state_hash=callback_token_hash(token),
            telegram_user_id=telegram_user_id,
            telegram_chat_id=telegram_chat_id,
            now=now,
        )
        if handoff is None:
            raise AiConnectionError("connection link is expired, used, or invalid")
        created_at = now
        previous = await self._repository.get_ai_connection(handoff.telegram_user_id)
        if previous is not None:
            created_at = previous.created_at
        if not await self._validator.validate(api_key):
            invalid = AiConnection(
                telegram_user_id=handoff.telegram_user_id,
                status=AiConnectionStatus.INVALID,
                created_at=created_at,
            )
            await self._repository.save_ai_connection(invalid)
            return invalid
        resource_name = await self._secret_store.put_user_secret(
            user_id=handoff.telegram_user_id, value=api_key
        )
        fingerprint = hashlib.sha256(api_key.encode("utf-8")).hexdigest()[-8:]
        connected = AiConnection(
            telegram_user_id=handoff.telegram_user_id,
            secret_resource_name=resource_name,
            key_fingerprint=f"…{fingerprint}",
            status=AiConnectionStatus.CONNECTED,
            created_at=created_at,
            validated_at=now,
        )
        await self._repository.save_ai_connection(connected)
        return connected

    async def disconnect(self, *, telegram_user_id: str, now: datetime) -> AiConnection:
        connection = await self._repository.get_ai_connection(telegram_user_id)
        if connection is None:
            raise AiConnectionError("Gemini connection does not exist")
        if connection.secret_resource_name is not None:
            await self._secret_store.delete_secret(resource_name=connection.secret_resource_name)
        disconnected = connection.model_copy(deep=True)
        disconnected.status = AiConnectionStatus.DISCONNECTED
        disconnected.secret_resource_name = None
        disconnected.disconnected_at = now
        await self._repository.save_ai_connection(disconnected)
        return disconnected
