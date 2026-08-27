from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Protocol

import httpx

from app.models.calendar import CalendarConnectionStatus
from app.models.enums import ActionCategory
from app.models.recovery import PlannedAction
from app.services.action_executor import ActionProvider, ProviderActionError
from app.services.ports import IncidentRepository, SecretStore


class CalendarTokenSource(Protocol):
    async def access_token(self) -> str | None: ...


class CalendarRefreshClient(Protocol):
    async def refresh_access_token(
        self, *, refresh_token: str, client_id: str, client_secret: str
    ) -> str | None: ...


class HttpGoogleRefreshClient:
    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient()

    async def refresh_access_token(
        self, *, refresh_token: str, client_id: str, client_secret: str
    ) -> str | None:
        response = await self._client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id,
                "client_secret": client_secret,
            },
            timeout=httpx.Timeout(10.0),
        )
        if response.status_code in {400, 401}:
            raise ProviderActionError(error_code="calendar_refresh_rejected", retryable=False)
        if response.status_code >= 500:
            raise ProviderActionError(error_code="calendar_token_unavailable", retryable=True)
        if response.status_code >= 400:
            raise ProviderActionError(error_code="calendar_token_rejected", retryable=False)
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderActionError(
                error_code="calendar_invalid_token_response", retryable=True
            ) from exc
        token = payload.get("access_token") if isinstance(payload, dict) else None
        return token if isinstance(token, str) and token else None


class GoogleRefreshTokenSource:
    """Resolve a short-lived access token without exposing refresh tokens to actions."""

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
        connection = await self._repository.get_calendar_connection(self._telegram_user_id)
        if (
            connection is None
            or connection.status != CalendarConnectionStatus.CONNECTED
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
        except ProviderActionError:
            raise
        except Exception as exc:
            raise ProviderActionError(
                error_code="calendar_token_unavailable", retryable=True
            ) from exc


class CalendarApi(Protocol):
    async def get_event(
        self, *, calendar_id: str, event_id: str, access_token: str
    ) -> dict[str, Any] | None: ...

    async def patch_event(
        self,
        *,
        calendar_id: str,
        event_id: str,
        body: dict[str, Any],
        access_token: str,
    ) -> dict[str, Any]: ...

    async def insert_event(
        self, *, calendar_id: str, body: dict[str, Any], access_token: str
    ) -> dict[str, Any]: ...

    async def find_event_by_effect(
        self, *, calendar_id: str, effect_key: str, access_token: str
    ) -> dict[str, Any] | None: ...


class HttpGoogleCalendarApi:
    """Small raw HTTP adapter so the worker needs no broad Google client dependency."""

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient()

    async def get_event(
        self, *, calendar_id: str, event_id: str, access_token: str
    ) -> dict[str, Any] | None:
        response = await self._client.get(
            f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events/{event_id}",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=httpx.Timeout(10.0),
        )
        if response.status_code == 404:
            return None
        if response.status_code in {401, 403}:
            raise ProviderActionError(error_code="calendar_auth_failed", retryable=False)
        if response.status_code >= 500:
            raise ProviderActionError(error_code="calendar_upstream_unavailable", retryable=True)
        if response.status_code >= 400:
            raise ProviderActionError(error_code="calendar_event_read_rejected", retryable=False)
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderActionError(
                error_code="calendar_invalid_event_response", retryable=True
            ) from exc
        return payload if isinstance(payload, dict) else None

    async def patch_event(
        self,
        *,
        calendar_id: str,
        event_id: str,
        body: dict[str, Any],
        access_token: str,
    ) -> dict[str, Any]:
        response = await self._client.patch(
            f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events/{event_id}",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"sendUpdates": "none"},
            json=body,
            timeout=httpx.Timeout(10.0),
        )
        if response.status_code in {401, 403}:
            raise ProviderActionError(error_code="calendar_auth_failed", retryable=False)
        if response.status_code >= 500:
            raise ProviderActionError(error_code="calendar_upstream_unavailable", retryable=True)
        if response.status_code >= 400:
            raise ProviderActionError(error_code="calendar_event_update_rejected", retryable=False)
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderActionError(
                error_code="calendar_invalid_event_response", retryable=True
            ) from exc
        if not isinstance(payload, dict):
            raise ProviderActionError(error_code="calendar_invalid_response", retryable=False)
        return payload

    async def insert_event(
        self, *, calendar_id: str, body: dict[str, Any], access_token: str
    ) -> dict[str, Any]:
        response = await self._client.post(
            f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"sendUpdates": "none"},
            json=body,
            timeout=httpx.Timeout(10.0),
        )
        if response.status_code in {401, 403}:
            raise ProviderActionError(error_code="calendar_auth_failed", retryable=False)
        if response.status_code >= 500:
            raise ProviderActionError(error_code="calendar_upstream_unavailable", retryable=True)
        if response.status_code >= 400:
            raise ProviderActionError(error_code="calendar_event_create_rejected", retryable=False)
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderActionError(
                error_code="calendar_invalid_event_response", retryable=True
            ) from exc
        if not isinstance(payload, dict):
            raise ProviderActionError(error_code="calendar_invalid_response", retryable=False)
        return payload

    async def find_event_by_effect(
        self, *, calendar_id: str, effect_key: str, access_token: str
    ) -> dict[str, Any] | None:
        response = await self._client.get(
            f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"privateExtendedProperty": f"tripAgentEffectKey={effect_key}"},
            timeout=httpx.Timeout(10.0),
        )
        if response.status_code in {401, 403}:
            raise ProviderActionError(error_code="calendar_auth_failed", retryable=False)
        if response.status_code >= 500:
            raise ProviderActionError(error_code="calendar_upstream_unavailable", retryable=True)
        if response.status_code >= 400:
            raise ProviderActionError(error_code="calendar_event_search_rejected", retryable=False)
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderActionError(
                error_code="calendar_invalid_event_response", retryable=True
            ) from exc
        items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            return None
        return next((item for item in items if isinstance(item, dict)), None)


class NotConnectedCalendarProvider:
    """Explicit safe stop: never claim a calendar action was completed."""

    async def apply(self, action: PlannedAction) -> str:
        raise ProviderActionError(error_code="calendar_not_connected", retryable=False)

    async def verify(self, action: PlannedAction) -> bool:
        return False


class GoogleCalendarActionProvider:
    """Idempotent Calendar action provider with reread verification."""

    def __init__(
        self,
        *,
        token_source: CalendarTokenSource,
        api: CalendarApi,
        calendar_id: str = "primary",
    ) -> None:
        self._token_source = token_source
        self._api = api
        self._calendar_id = calendar_id

    async def apply(self, action: PlannedAction) -> str:
        self._require_calendar(action)
        token = await self._token_source.access_token()
        if not token:
            raise ProviderActionError(error_code="calendar_not_connected", retryable=False)
        synthetic_target = action.target_external_id.startswith("calendar:")
        if synthetic_target:
            current = await self._api.find_event_by_effect(
                calendar_id=self._calendar_id,
                effect_key=action.effect_key,
                access_token=token,
            )
            if current is None:
                created = await self._api.insert_event(
                    calendar_id=self._calendar_id,
                    body=self._new_event(action),
                    access_token=token,
                )
                reference = created.get("id")
                if not isinstance(reference, str) or not reference:
                    raise ProviderActionError(
                        error_code="calendar_invalid_create_response", retryable=False
                    )
                return reference
        else:
            current = await self._api.get_event(
                calendar_id=self._calendar_id,
                event_id=action.target_external_id,
                access_token=token,
            )
        if current is None:
            raise ProviderActionError(error_code="calendar_event_not_found", retryable=False)
        body = self._desired_patch(action, current)
        event_id = action.target_external_id
        if synthetic_target:
            candidate_id = current.get("id")
            if isinstance(candidate_id, str) and candidate_id:
                event_id = candidate_id
        updated = await self._api.patch_event(
            calendar_id=self._calendar_id,
            event_id=event_id,
            body=body,
            access_token=token,
        )
        reference = updated.get("id")
        if not isinstance(reference, str) or not reference:
            reference = action.target_external_id
        return reference

    async def verify(self, action: PlannedAction) -> bool:
        self._require_calendar(action)
        token = await self._token_source.access_token()
        if not token:
            return False
        event_id = action.provider_reference or action.target_external_id
        if action.target_external_id.startswith("calendar:") and not action.provider_reference:
            current = await self._api.find_event_by_effect(
                calendar_id=self._calendar_id,
                effect_key=action.effect_key,
                access_token=token,
            )
        else:
            current = await self._api.get_event(
                calendar_id=self._calendar_id,
                event_id=event_id,
                access_token=token,
            )
        if current is None:
            return False
        private = current.get("extendedProperties", {}).get("private", {})
        if not isinstance(private, dict) or private.get("tripAgentEffectKey") != action.effect_key:
            return False
        expected = _as_datetime(action.desired_state.get("arrival_at"))
        if expected is None:
            return True
        start = current.get("start", {})
        actual = start.get("dateTime") if isinstance(start, dict) else None
        actual_at = _as_datetime(actual)
        return actual_at is not None and actual_at == expected

    @staticmethod
    def _new_event(action: PlannedAction) -> dict[str, Any]:
        arrival = _as_datetime(action.desired_state.get("arrival_at"))
        if arrival is None:
            raise ProviderActionError(error_code="calendar_missing_arrival", retryable=False)
        end = arrival + timedelta(hours=1)
        return {
            "summary": "Trip Watch itinerary update",
            "description": "Created and verified by Trip Watch.",
            "start": {"dateTime": arrival.isoformat(), "timeZone": "UTC"},
            "end": {"dateTime": end.isoformat(), "timeZone": "UTC"},
            "extendedProperties": {"private": {"tripAgentEffectKey": action.effect_key}},
        }

    @staticmethod
    def _require_calendar(action: PlannedAction) -> None:
        if action.category != ActionCategory.CALENDAR:
            raise ProviderActionError(error_code="calendar_wrong_action_category", retryable=False)

    @staticmethod
    def _desired_patch(action: PlannedAction, current: dict[str, Any]) -> dict[str, Any]:
        private = current.get("extendedProperties", {}).get("private", {})
        if not isinstance(private, dict):
            private = {}
        body: dict[str, Any] = {
            "extendedProperties": {"private": {**private, "tripAgentEffectKey": action.effect_key}}
        }
        arrival = _as_datetime(action.desired_state.get("arrival_at"))
        if arrival is not None:
            start = current.get("start", {})
            end = current.get("end", {})
            if isinstance(start, dict) and "date" not in start:
                body["start"] = {**start, "dateTime": arrival.isoformat()}
            if isinstance(end, dict) and "date" not in end:
                duration = _duration_between(start, end)
                body["end"] = {
                    **end,
                    "dateTime": (arrival + duration).isoformat(),
                }
        return body


def _duration_between(start: dict[str, Any], end: dict[str, Any]) -> Any:
    """Preserve event duration where both timestamps are parseable; otherwise one hour."""

    from datetime import timedelta

    try:
        start_at = datetime.fromisoformat(str(start.get("dateTime")))
        end_at = datetime.fromisoformat(str(end.get("dateTime")))
        duration = end_at - start_at
        return duration if duration.total_seconds() > 0 else timedelta(hours=1)
    except (TypeError, ValueError):
        return timedelta(hours=1)


def _as_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class HybridActionProvider:
    """Route Calendar to a connected provider and keep the demo provider elsewhere."""

    def __init__(
        self,
        *,
        fallback: ActionProvider,
        calendar: ActionProvider | None = None,
        connection_status: CalendarConnectionStatus = CalendarConnectionStatus.DISCONNECTED,
    ) -> None:
        self._fallback = fallback
        self._calendar = calendar
        self._connection_status = connection_status

    async def apply(self, action: PlannedAction) -> str:
        if action.category == ActionCategory.CALENDAR:
            if (
                self._calendar is None
                or self._connection_status != CalendarConnectionStatus.CONNECTED
            ):
                return await NotConnectedCalendarProvider().apply(action)
            return await self._calendar.apply(action)
        return await self._fallback.apply(action)

    async def verify(self, action: PlannedAction) -> bool:
        if action.category == ActionCategory.CALENDAR:
            if (
                self._calendar is None
                or self._connection_status != CalendarConnectionStatus.CONNECTED
            ):
                return False
            return await self._calendar.verify(action)
        return await self._fallback.verify(action)


class TravelerCalendarActionProvider:
    """Per-traveler provider used by the live feature flag.

    The refresh token is resolved only when a Calendar action is executed. A
    disconnected traveler therefore gets an explicit terminal stop, while all
    non-Calendar actions keep using the deterministic provider used by the
    rest of the recovery plan.
    """

    def __init__(
        self,
        *,
        repository: IncidentRepository,
        secret_store: SecretStore,
        telegram_user_id: str,
        client_id: str,
        client_secret: str,
        calendar_api: CalendarApi,
        calendar_id: str = "primary",
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        from app.providers.demo import PersistentDemoProvider

        fallback = PersistentDemoProvider(repository)
        token_source = GoogleRefreshTokenSource(
            repository=repository,
            secret_store=secret_store,
            telegram_user_id=telegram_user_id,
            client_id=client_id,
            client_secret=client_secret,
            refresh_client=HttpGoogleRefreshClient(client=http_client),
        )
        self._provider = HybridActionProvider(
            fallback=fallback,
            calendar=GoogleCalendarActionProvider(
                token_source=token_source,
                api=calendar_api,
                calendar_id=calendar_id,
            ),
            connection_status=CalendarConnectionStatus.CONNECTED,
        )

    async def apply(self, action: PlannedAction) -> str:
        return await self._provider.apply(action)

    async def verify(self, action: PlannedAction) -> bool:
        return await self._provider.verify(action)
