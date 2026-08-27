from __future__ import annotations

import base64
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from email.parser import BytesParser
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from app.models.enums import ActionCategory
from app.models.gmail import GmailConnectionStatus
from app.models.money import Money
from app.models.recovery import PlannedAction
from app.providers.gmail import GoogleGmailDraftProvider, GoogleGmailRefreshTokenSource
from app.services.action_executor import ProviderActionError
from app.services.gmail_oauth import GMAIL_DRAFT_SCOPE, GmailOAuthService
from app.services.memory import InMemoryIncidentRepository

from tests.test_calendar_contracts import FakeOAuthClient, FakeSecretStore


class FakeRefreshClient:
    async def refresh_access_token(
        self, *, refresh_token: str, client_id: str, client_secret: str
    ) -> str | None:
        assert (refresh_token, client_id, client_secret) == (
            "refresh-token-secret",
            "client-id",
            "client-secret",
        )
        return "access-token"


class FakeTokenSource:
    async def access_token(self) -> str | None:
        return "access-token"


class FakeGmailDraftApi:
    def __init__(self) -> None:
        self.messages: dict[str, dict[str, Any]] = {}
        self.created: list[str] = []

    async def find_message_by_effect(
        self, *, effect_key: str, access_token: str
    ) -> dict[str, Any] | None:
        assert access_token == "access-token"
        for message in self.messages.values():
            headers = message["payload"]["headers"]
            if any(
                header["name"] == "X-Trip-Agent-Effect-Key" and header["value"] == effect_key
                for header in headers
            ):
                return deepcopy(message)
        return None

    async def create_draft(self, *, raw_message: str, access_token: str) -> dict[str, Any]:
        assert access_token == "access-token"
        assert raw_message
        message_id = f"draft-message-{len(self.messages) + 1}"
        raw_bytes = base64.urlsafe_b64decode(raw_message + "=" * (-len(raw_message) % 4))
        email = BytesParser().parsebytes(raw_bytes)
        self.created.append(raw_message)
        self.messages[message_id] = {
            "id": message_id,
            "labelIds": ["DRAFT"],
            "payload": {
                "headers": [
                    {
                        "name": "X-Trip-Agent-Effect-Key",
                        "value": email["X-Trip-Agent-Effect-Key"],
                    },
                    {"name": "To", "value": email["To"]},
                ]
            },
        }
        return {"id": f"draft-{len(self.messages)}", "message": {"id": message_id}}

    async def get_message(self, *, message_id: str, access_token: str) -> dict[str, Any] | None:
        assert access_token == "access-token"
        return deepcopy(self.messages.get(message_id))


async def test_gmail_oauth_is_compose_only_and_identity_bound() -> None:
    repository = InMemoryIncidentRepository()
    secrets = FakeSecretStore()
    oauth_client = FakeOAuthClient()
    service = GmailOAuthService(
        repository,
        secrets,
        oauth_client,
        client_id="client-id",
        pkce_signing_key="k" * 32,
    )
    now = datetime(2026, 8, 24, tzinfo=UTC)
    authorization = await service.create_authorization(
        telegram_user_id="user-1",
        telegram_chat_id="chat-1",
        redirect_uri="https://example.test/oauth/callback",
        now=now,
    )
    params = parse_qs(urlparse(authorization.url).query)
    assert params["scope"] == [GMAIL_DRAFT_SCOPE]
    assert params["code_challenge_method"] == ["S256"]

    connection = await service.complete(
        code="auth-code",
        state=authorization.state,
        telegram_user_id="user-1",
        telegram_chat_id="chat-1",
        redirect_uri="https://example.test/oauth/callback",
        now=now + timedelta(seconds=1),
    )
    assert connection.status == GmailConnectionStatus.CONNECTED
    assert connection.secret_resource_name is not None
    assert secrets.values[connection.secret_resource_name] == "refresh-token-secret"

    disconnected = await service.disconnect(
        telegram_user_id="user-1", now=now + timedelta(seconds=2)
    )
    assert disconnected.status == GmailConnectionStatus.DISCONNECTED
    assert oauth_client.revoked == ["refresh-token-secret"]


async def test_gmail_refresh_token_is_secret_manager_backed() -> None:
    repository = InMemoryIncidentRepository()
    secrets = FakeSecretStore()
    service = GmailOAuthService(
        repository,
        secrets,
        FakeOAuthClient(),
        client_id="client-id",
        pkce_signing_key="k" * 32,
    )
    now = datetime(2026, 8, 24, tzinfo=UTC)
    authorization = await service.create_authorization(
        telegram_user_id="user-1",
        telegram_chat_id="chat-1",
        redirect_uri="https://example.test/oauth/callback",
        now=now,
    )
    await service.complete(
        code="auth-code",
        state=authorization.state,
        telegram_user_id="user-1",
        telegram_chat_id="chat-1",
        redirect_uri="https://example.test/oauth/callback",
        now=now,
    )
    source = GoogleGmailRefreshTokenSource(
        repository=repository,
        secret_store=secrets,
        telegram_user_id="user-1",
        client_id="client-id",
        client_secret="client-secret",
        refresh_client=FakeRefreshClient(),
    )
    assert await source.access_token() == "access-token"


async def test_gmail_provider_creates_a_verifiable_late_arrival_draft_once() -> None:
    action = _hotel_action(contact_email="latecheckin@example-hotel.test")
    api = FakeGmailDraftApi()
    provider = GoogleGmailDraftProvider(token_source=FakeTokenSource(), api=api)

    first = await provider.apply(action)
    action.provider_reference = first
    second = await provider.apply(action)

    assert first == second
    assert len(api.created) == 1
    assert await provider.verify(action) is True


async def test_gmail_provider_refuses_to_create_a_draft_without_a_hotel_email() -> None:
    action = _hotel_action(contact_email=None)
    provider = GoogleGmailDraftProvider(token_source=FakeTokenSource(), api=FakeGmailDraftApi())

    with pytest.raises(ProviderActionError, match="hotel_contact_missing"):
        await provider.apply(action)


def _hotel_action(*, contact_email: str | None) -> PlannedAction:
    return PlannedAction(
        action_id="incident-1:v1:hotel",
        incident_id="incident-1",
        plan_version=1,
        category=ActionCategory.SERVICE_MESSAGE,
        provider="gmail",
        target_external_id="booking-1",
        desired_state={
            "hotel_name": "Hotel Aurora",
            "expected_arrival_at": "2026-09-08T23:10:00+01:00",
            "booking_reference": "BOOKING-1",
            "contact_email": contact_email,
        },
        cost=Money(currency="EUR", minor_units=0),
        effect_key="e" * 64,
    )
