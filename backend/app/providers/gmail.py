from __future__ import annotations

import base64
from email.message import EmailMessage
from typing import Any, Protocol

import httpx

from app.models.enums import ActionCategory
from app.models.gmail import GmailConnectionStatus
from app.models.recovery import PlannedAction
from app.providers.calendar import (
    CalendarApi,
    CalendarRefreshClient,
    GoogleCalendarActionProvider,
    GoogleRefreshTokenSource,
    HttpGoogleRefreshClient,
)
from app.providers.guarded_demo import JudgeOnlyDemoProvider
from app.services.action_executor import ProviderActionError
from app.services.ports import IncidentRepository, SecretStore


class GmailTokenSource(Protocol):
    async def access_token(self) -> str | None: ...


class GoogleGmailRefreshTokenSource:
    """Resolve a short-lived token only for an approved Gmail draft action."""

    def __init__(
        self,
        *,
        repository: IncidentRepository,
        secret_store: SecretStore,
        telegram_user_id: str,
        client_id: str,
        client_secret: str,
        refresh_client: CalendarRefreshClient | None = None,
    ) -> None:
        self._repository = repository
        self._secret_store = secret_store
        self._telegram_user_id = telegram_user_id
        self._client_id = client_id
        self._client_secret = client_secret
        self._refresh_client = refresh_client or HttpGoogleRefreshClient()

    async def access_token(self) -> str | None:
        connection = await self._repository.get_gmail_connection(self._telegram_user_id)
        if (
            connection is None
            or connection.status != GmailConnectionStatus.CONNECTED
            or connection.secret_resource_name is None
        ):
            return None
        try:
            refresh_token = await self._secret_store.access_secret(
                resource_name=connection.secret_resource_name
            )
            return await self._refresh_client.refresh_access_token(
                refresh_token=refresh_token,
                client_id=self._client_id,
                client_secret=self._client_secret,
            )
        except ProviderActionError as exc:
            code = exc.error_code.replace("calendar", "gmail")
            raise ProviderActionError(error_code=code, retryable=exc.retryable) from exc
        except Exception as exc:
            raise ProviderActionError(error_code="gmail_token_unavailable", retryable=True) from exc


class GmailDraftApi(Protocol):
    async def find_message_by_effect(
        self, *, effect_key: str, access_token: str
    ) -> dict[str, Any] | None: ...

    async def create_draft(self, *, raw_message: str, access_token: str) -> dict[str, Any]: ...

    async def get_message(self, *, message_id: str, access_token: str) -> dict[str, Any] | None: ...


class HttpGoogleGmailDraftApi:
    """Raw Gmail draft adapter.

    It deliberately has no `send` method. Every mutation produces a draft; the
    traveler must open Gmail and send it themselves.
    """

    _BASE = "https://gmail.googleapis.com/gmail/v1/users/me"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient()

    async def find_message_by_effect(
        self, *, effect_key: str, access_token: str
    ) -> dict[str, Any] | None:
        message_id = _message_id(effect_key)
        response = await self._client.get(
            f"{self._BASE}/messages",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"q": f"rfc822msgid:{message_id}", "maxResults": 1},
            timeout=httpx.Timeout(10.0),
        )
        self._raise_for_status(response, operation="search")
        payload = self._json(response, operation="search")
        messages = payload.get("messages")
        if not isinstance(messages, list) or not messages:
            return None
        first = messages[0]
        if not isinstance(first, dict):
            return None
        message = first.get("id")
        if not isinstance(message, str) or not message:
            return None
        return await self.get_message(message_id=message, access_token=access_token)

    async def create_draft(self, *, raw_message: str, access_token: str) -> dict[str, Any]:
        response = await self._client.post(
            f"{self._BASE}/drafts",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"message": {"raw": raw_message}},
            timeout=httpx.Timeout(10.0),
        )
        self._raise_for_status(response, operation="create")
        return self._json(response, operation="create")

    async def get_message(self, *, message_id: str, access_token: str) -> dict[str, Any] | None:
        response = await self._client.get(
            f"{self._BASE}/messages/{message_id}",
            headers={"Authorization": f"Bearer {access_token}"},
            params=[
                ("format", "metadata"),
                ("metadataHeaders", "Message-ID"),
                ("metadataHeaders", "X-Trip-Agent-Effect-Key"),
            ],
            timeout=httpx.Timeout(10.0),
        )
        if response.status_code == 404:
            return None
        self._raise_for_status(response, operation="read")
        return self._json(response, operation="read")

    @staticmethod
    def _json(response: httpx.Response, *, operation: str) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderActionError(
                error_code=f"gmail_invalid_{operation}_response", retryable=True
            ) from exc
        if not isinstance(payload, dict):
            raise ProviderActionError(
                error_code=f"gmail_invalid_{operation}_response", retryable=False
            )
        return payload

    @staticmethod
    def _raise_for_status(response: httpx.Response, *, operation: str) -> None:
        if response.status_code in {401, 403}:
            raise ProviderActionError(error_code="gmail_auth_failed", retryable=False)
        if response.status_code >= 500:
            raise ProviderActionError(error_code="gmail_upstream_unavailable", retryable=True)
        if response.status_code >= 400:
            raise ProviderActionError(
                error_code=f"gmail_draft_{operation}_rejected", retryable=False
            )


class NotConnectedGmailProvider:
    async def apply(self, action: PlannedAction) -> str:
        del action
        raise ProviderActionError(error_code="gmail_not_connected", retryable=False)

    async def verify(self, action: PlannedAction) -> bool:
        del action
        return False


class GoogleGmailDraftProvider:
    """Idempotently create and reread a hotel late-arrival email draft."""

    def __init__(self, *, token_source: GmailTokenSource, api: GmailDraftApi) -> None:
        self._token_source = token_source
        self._api = api

    async def apply(self, action: PlannedAction) -> str:
        self._require_service_message(action)
        token = await self._token_source.access_token()
        if not token:
            raise ProviderActionError(error_code="gmail_not_connected", retryable=False)
        existing = await self._api.find_message_by_effect(
            effect_key=action.effect_key, access_token=token
        )
        if existing is not None:
            reference = existing.get("id")
            if isinstance(reference, str) and reference:
                return reference
        recipient = _recipient(action)
        raw = _draft_message(action, recipient)
        created = await self._api.create_draft(raw_message=raw, access_token=token)
        message = created.get("message")
        reference = message.get("id") if isinstance(message, dict) else None
        if not isinstance(reference, str) or not reference:
            raise ProviderActionError(error_code="gmail_invalid_create_response", retryable=False)
        return reference

    async def verify(self, action: PlannedAction) -> bool:
        self._require_service_message(action)
        token = await self._token_source.access_token()
        if not token:
            return False
        message_id = action.provider_reference
        message = (
            await self._api.get_message(message_id=message_id, access_token=token)
            if message_id
            else await self._api.find_message_by_effect(
                effect_key=action.effect_key, access_token=token
            )
        )
        if message is None:
            return False
        labels = message.get("labelIds")
        headers = message.get("payload", {}).get("headers", [])
        if not isinstance(labels, list) or "DRAFT" not in labels or not isinstance(headers, list):
            return False
        return any(
            isinstance(header, dict)
            and header.get("name") == "X-Trip-Agent-Effect-Key"
            and header.get("value") == action.effect_key
            for header in headers
        )

    @staticmethod
    def _require_service_message(action: PlannedAction) -> None:
        if action.category != ActionCategory.SERVICE_MESSAGE:
            raise ProviderActionError(error_code="gmail_action_category_invalid", retryable=False)


def _recipient(action: PlannedAction) -> str:
    candidate = action.desired_state.get("contact_email")
    if not isinstance(candidate, str) or "@" not in candidate or len(candidate) > 254:
        raise ProviderActionError(error_code="hotel_contact_missing", retryable=False)
    return candidate


def _message_id(effect_key: str) -> str:
    return f"<trip-agent-{effect_key[:40]}@tripagent.local>"


def _draft_message(action: PlannedAction, recipient: str) -> str:
    hotel = action.desired_state.get("hotel_name")
    hotel_name = hotel if isinstance(hotel, str) and hotel else "your hotel"
    arrival = action.desired_state.get("expected_arrival_at")
    arrival_text = arrival if isinstance(arrival, str) and arrival else "the updated arrival time"
    reference = action.desired_state.get("booking_reference")
    reference_text = ""
    if isinstance(reference, str) and reference:
        reference_text = f" Booking reference: {reference}."
    message = EmailMessage()
    message["To"] = recipient
    message["Subject"] = f"Late arrival notice — {hotel_name}"
    message["Message-ID"] = _message_id(action.effect_key)
    message["X-Trip-Agent-Effect-Key"] = action.effect_key
    message.set_content(
        f"Hello {hotel_name},\n\n"
        f"My arrival has changed because of a travel disruption. I now expect to arrive at "
        f"{arrival_text}. Please keep my reservation active and confirm late check-in is possible."
        f"{reference_text}\n\nThank you."
    )
    return base64.urlsafe_b64encode(message.as_bytes()).decode("ascii").rstrip("=")


class TravelerGoogleActionProvider:
    """Route live Google actions per traveler; never fall back to fake user effects.

    Calendar changes and Gmail drafts each reread the provider after a write. All
    other recovery actions use the judge-only provider, which refuses to claim
    success for a non-demo incident until its real adapter exists.
    """

    def __init__(
        self,
        *,
        repository: IncidentRepository,
        secret_store: SecretStore,
        telegram_user_id: str,
        calendar_client_id: str | None = None,
        calendar_client_secret: str | None = None,
        calendar_api: CalendarApi | None = None,
        calendar_id: str = "primary",
        gmail_client_id: str | None = None,
        gmail_client_secret: str | None = None,
        gmail_api: GmailDraftApi | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._fallback = JudgeOnlyDemoProvider(repository)
        self._calendar: GoogleCalendarActionProvider | None = None
        self._gmail: GoogleGmailDraftProvider | None = None
        if calendar_client_id and calendar_client_secret and calendar_api is not None:
            self._calendar = GoogleCalendarActionProvider(
                token_source=GoogleRefreshTokenSource(
                    repository=repository,
                    secret_store=secret_store,
                    telegram_user_id=telegram_user_id,
                    client_id=calendar_client_id,
                    client_secret=calendar_client_secret,
                    refresh_client=HttpGoogleRefreshClient(client=http_client),
                ),
                api=calendar_api,
                calendar_id=calendar_id,
            )
        if gmail_client_id and gmail_client_secret and gmail_api is not None:
            self._gmail = GoogleGmailDraftProvider(
                token_source=GoogleGmailRefreshTokenSource(
                    repository=repository,
                    secret_store=secret_store,
                    telegram_user_id=telegram_user_id,
                    client_id=gmail_client_id,
                    client_secret=gmail_client_secret,
                    refresh_client=HttpGoogleRefreshClient(client=http_client),
                ),
                api=gmail_api,
            )

    async def apply(self, action: PlannedAction) -> str:
        if action.category == ActionCategory.CALENDAR:
            if self._calendar is None:
                return await self._calendar_not_connected(action)
            return await self._calendar.apply(action)
        if action.category == ActionCategory.SERVICE_MESSAGE:
            if self._gmail is None:
                return await NotConnectedGmailProvider().apply(action)
            return await self._gmail.apply(action)
        return await self._fallback.apply(action)

    async def verify(self, action: PlannedAction) -> bool:
        if action.category == ActionCategory.CALENDAR:
            return self._calendar is not None and await self._calendar.verify(action)
        if action.category == ActionCategory.SERVICE_MESSAGE:
            return self._gmail is not None and await self._gmail.verify(action)
        return await self._fallback.verify(action)

    @staticmethod
    async def _calendar_not_connected(action: PlannedAction) -> str:
        del action
        raise ProviderActionError(error_code="calendar_not_connected", retryable=False)
