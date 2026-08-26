from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol
from urllib.parse import urlencode

import httpx

from app.models.calendar import (
    CalendarConnection,
    CalendarConnectionStatus,
    CalendarOAuthState,
)
from app.services.ports import IncidentRepository, SecretStore

CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.events"


class CalendarOAuthError(ValueError):
    pass


class CalendarOAuthClient(Protocol):
    async def exchange_code(
        self, *, code: str, code_verifier: str, redirect_uri: str
    ) -> dict[str, Any]: ...

    async def revoke_refresh_token(self, *, refresh_token: str) -> None: ...


class HttpGoogleCalendarOAuthClient:
    """Google token exchange; client secret is supplied by the server only."""

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        client: httpx.AsyncClient | None = None,
        token_endpoint: str = "https://oauth2.googleapis.com/token",
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._client = client or httpx.AsyncClient()
        self._token_endpoint = token_endpoint

    async def exchange_code(
        self, *, code: str, code_verifier: str, redirect_uri: str
    ) -> dict[str, Any]:
        response = await self._client.post(
            self._token_endpoint,
            data={
                "code": code,
                "code_verifier": code_verifier,
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
            timeout=httpx.Timeout(10.0),
        )
        if response.status_code in {400, 401}:
            raise CalendarOAuthError("Google rejected the calendar authorization code")
        if response.status_code >= 500:
            raise CalendarOAuthError("Google calendar authorization is temporarily unavailable")
        if response.status_code >= 400:
            raise CalendarOAuthError("Google calendar authorization failed")
        try:
            payload = response.json()
        except ValueError as exc:
            raise CalendarOAuthError("Google returned an invalid calendar token response") from exc
        if not isinstance(payload, dict):
            raise CalendarOAuthError("Google returned an invalid calendar token response")
        return payload

    async def revoke_refresh_token(self, *, refresh_token: str) -> None:
        response = await self._client.post(
            "https://oauth2.googleapis.com/revoke",
            params={"token": refresh_token},
            timeout=httpx.Timeout(10.0),
        )
        if response.status_code not in {200, 400}:
            raise CalendarOAuthError("Google calendar revoke is temporarily unavailable")


@dataclass(frozen=True)
class CalendarAuthorization:
    url: str
    state: str
    expires_at: datetime


class CalendarOAuthService:
    """PKCE OAuth lifecycle with single-use, identity-bound state.

    The service deliberately accepts an injected OAuth client. Production can
    provide a Google client; tests use a deterministic fake and never hold real
    refresh tokens in memory longer than the exchange call.
    """

    def __init__(
        self,
        repository: IncidentRepository,
        secret_store: SecretStore,
        client: CalendarOAuthClient,
        *,
        client_id: str,
        pkce_signing_key: str,
        authorization_endpoint: str = "https://accounts.google.com/o/oauth2/v2/auth",
    ) -> None:
        if not client_id or len(pkce_signing_key.encode("utf-8")) < 32:
            raise ValueError("client_id and a 32+ byte PKCE signing key are required")
        self._repository = repository
        self._secret_store = secret_store
        self._client = client
        self._client_id = client_id
        self._pkce_signing_key = pkce_signing_key.encode("utf-8")
        self._authorization_endpoint = authorization_endpoint

    async def create_authorization(
        self,
        *,
        telegram_user_id: str,
        telegram_chat_id: str,
        redirect_uri: str,
        now: datetime,
        expires_in: timedelta = timedelta(minutes=10),
    ) -> CalendarAuthorization:
        if not redirect_uri.startswith("https://"):
            raise CalendarOAuthError("calendar redirect URI must use HTTPS")
        state = secrets.token_urlsafe(32)
        verifier = _pkce_verifier(state, self._pkce_signing_key)
        expires_at = now + expires_in
        stored = await self._repository.store_calendar_oauth_state(
            CalendarOAuthState(
                state_hash=_hash(state),
                telegram_user_id=telegram_user_id,
                telegram_chat_id=telegram_chat_id,
                redirect_uri=redirect_uri,
                scopes=[CALENDAR_SCOPE],
                code_verifier_hash=_hash(verifier),
                expires_at=expires_at,
                created_at=now,
            )
        )
        if not stored:
            raise CalendarOAuthError("could not persist calendar OAuth state")
        query = urlencode(
            {
                "client_id": self._client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": CALENDAR_SCOPE,
                "access_type": "offline",
                "prompt": "consent",
                "state": state,
                "code_challenge": _pkce_challenge(verifier),
                "code_challenge_method": "S256",
            }
        )
        return CalendarAuthorization(
            url=f"{self._authorization_endpoint}?{query}",
            state=state,
            expires_at=expires_at,
        )

    async def complete(
        self,
        *,
        code: str,
        state: str,
        code_verifier: str | None = None,
        telegram_user_id: str,
        telegram_chat_id: str,
        redirect_uri: str,
        now: datetime,
    ) -> CalendarConnection:
        if not code or not state:
            raise CalendarOAuthError("calendar OAuth callback is incomplete")
        verifier = code_verifier or _pkce_verifier(state, self._pkce_signing_key)
        stored = await self._repository.consume_calendar_oauth_state(
            state_hash=_hash(state),
            telegram_user_id=telegram_user_id,
            telegram_chat_id=telegram_chat_id,
            redirect_uri=redirect_uri,
            code_verifier_hash=_hash(verifier),
            now=now,
        )
        if stored is None:
            raise CalendarOAuthError("calendar OAuth state is expired, used, or invalid")
        token_payload = await self._client.exchange_code(
            code=code, code_verifier=verifier, redirect_uri=redirect_uri
        )
        refresh_token = token_payload.get("refresh_token")
        if not isinstance(refresh_token, str) or not refresh_token:
            raise CalendarOAuthError("Google did not return a refresh token")
        resource_name = await self._secret_store.put_user_secret(
            user_id=f"calendar:{telegram_user_id}", value=refresh_token
        )
        previous = await self._repository.get_calendar_connection(telegram_user_id)
        created_at = previous.created_at if previous is not None else now
        connection = CalendarConnection(
            telegram_user_id=telegram_user_id,
            secret_resource_name=resource_name,
            scopes=[CALENDAR_SCOPE],
            status=CalendarConnectionStatus.CONNECTED,
            created_at=created_at,
            updated_at=now,
        )
        await self._repository.save_calendar_connection(connection)
        return connection

    async def complete_callback(
        self, *, code: str, state: str, now: datetime
    ) -> CalendarConnection:
        """Complete a browser callback using only the server-side state binding."""

        stored = await self._repository.get_calendar_oauth_state(_hash(state))
        if stored is None:
            raise CalendarOAuthError("calendar OAuth state is expired, used, or invalid")
        return await self.complete(
            code=code,
            state=state,
            telegram_user_id=stored.telegram_user_id,
            telegram_chat_id=stored.telegram_chat_id,
            redirect_uri=stored.redirect_uri,
            now=now,
        )

    async def disconnect(self, *, telegram_user_id: str, now: datetime) -> CalendarConnection:
        connection = await self._repository.get_calendar_connection(telegram_user_id)
        if connection is None:
            raise CalendarOAuthError("calendar connection does not exist")
        if connection.secret_resource_name is not None:
            try:
                refresh_token = await self._secret_store.access_secret(
                    resource_name=connection.secret_resource_name
                )
                await self._client.revoke_refresh_token(refresh_token=refresh_token)
            except AttributeError as exc:
                raise CalendarOAuthError("calendar OAuth client cannot revoke tokens") from exc
            except Exception as exc:
                raise CalendarOAuthError(
                    "Google calendar revoke failed; connection retained"
                ) from exc
            await self._secret_store.delete_secret(resource_name=connection.secret_resource_name)
        disconnected = connection.model_copy(deep=True)
        disconnected.status = CalendarConnectionStatus.DISCONNECTED
        disconnected.secret_resource_name = None
        disconnected.updated_at = now
        disconnected.disconnected_at = now
        await self._repository.save_calendar_connection(disconnected)
        return disconnected


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _pkce_verifier(state: str, signing_key: bytes) -> str:
    digest = hmac.new(signing_key, state.encode("ascii"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _pkce_challenge(verifier: str) -> str:
    return (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
        .rstrip(b"=")
        .decode("ascii")
    )
