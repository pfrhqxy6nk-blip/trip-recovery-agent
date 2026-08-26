from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from app.demo_data import build_demo_trip
from app.models.calendar import CalendarConnection, CalendarConnectionStatus
from app.models.enums import ActionCategory, ActionStatus
from app.providers.calendar import (
    GoogleCalendarActionProvider,
    GoogleRefreshTokenSource,
    HybridActionProvider,
)
from app.providers.demo import PersistentDemoProvider
from app.services.action_executor import ActionExecutor
from app.services.calendar_oauth import CALENDAR_SCOPE, CalendarOAuthService
from app.services.memory import InMemoryIncidentRepository
from app.services.recovery_planner import CanonicalRecoveryPlanner

from tests.helpers import ValidInterpreter, disruption_event
from tests.test_action_executor import build_plan


class FakeSecretStore:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.deleted: list[str] = []

    async def put_user_secret(self, *, user_id: str, value: str) -> str:
        name = f"projects/test/secrets/{user_id}/versions/1"
        self.values[name] = value
        return name

    async def delete_secret(self, *, resource_name: str) -> None:
        self.deleted.append(resource_name)
        self.values.pop(resource_name, None)

    async def access_secret(self, *, resource_name: str) -> str:
        return self.values[resource_name]


class FakeOAuthClient:
    def __init__(self) -> None:
        self.revoked: list[str] = []

    async def exchange_code(
        self, *, code: str, code_verifier: str, redirect_uri: str
    ) -> dict[str, object]:
        assert code == "auth-code"
        assert code_verifier
        assert redirect_uri == "https://example.test/oauth/callback"
        return {"refresh_token": "refresh-token-secret"}

    async def revoke_refresh_token(self, *, refresh_token: str) -> None:
        self.revoked.append(refresh_token)


class FakeCalendarApi:
    def __init__(self, event: Mapping[str, Any]) -> None:
        self.events: dict[str, dict[str, Any]] = {"calendar-event-1": dict(deepcopy(event))}
        self.patches: list[dict[str, Any]] = []

    async def get_event(
        self, *, calendar_id: str, event_id: str, access_token: str
    ) -> dict[str, Any] | None:
        assert calendar_id == "primary"
        assert access_token == "access-token"
        event = self.events.get(event_id)
        return deepcopy(event) if event is not None else None

    async def patch_event(
        self,
        *,
        calendar_id: str,
        event_id: str,
        body: dict[str, Any],
        access_token: str,
    ) -> dict[str, Any]:
        assert calendar_id == "primary"
        assert access_token == "access-token"
        self.patches.append(deepcopy(body))
        self.events[event_id] = {**self.events[event_id], **deepcopy(body)}
        return deepcopy(self.events[event_id])

    async def insert_event(
        self,
        *,
        calendar_id: str,
        body: dict[str, Any],
        access_token: str,
    ) -> dict[str, Any]:
        assert calendar_id == "primary"
        assert access_token == "access-token"
        event_id = f"created-{len(self.events) + 1}"
        self.events[event_id] = {"id": event_id, **deepcopy(body)}
        return deepcopy(self.events[event_id])

    async def find_event_by_effect(
        self, *, calendar_id: str, effect_key: str, access_token: str
    ) -> dict[str, Any] | None:
        assert calendar_id == "primary"
        assert access_token == "access-token"
        for event in self.events.values():
            private = event.get("extendedProperties", {}).get("private", {})
            if isinstance(private, dict) and private.get("tripAgentEffectKey") == effect_key:
                return deepcopy(event)
        return None


class FakeTokenSource:
    async def access_token(self) -> str | None:
        return "access-token"


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


async def test_calendar_oauth_uses_pkce_and_single_use_identity_bound_state() -> None:
    repository = InMemoryIncidentRepository()
    secrets = FakeSecretStore()
    oauth_client = FakeOAuthClient()
    service = CalendarOAuthService(
        repository,
        secrets,
        oauth_client,
        client_id="client-id",
        pkce_signing_key="k" * 32,
    )
    now = datetime(2026, 8, 23, tzinfo=UTC)
    authorization = await service.create_authorization(
        telegram_user_id="user-1",
        telegram_chat_id="chat-1",
        redirect_uri="https://example.test/oauth/callback",
        now=now,
    )
    params = parse_qs(urlparse(authorization.url).query)
    assert params["state"] == [authorization.state]
    assert params["scope"] == [CALENDAR_SCOPE]
    assert params["code_challenge_method"] == ["S256"]
    assert params["code_challenge"][0]

    connection = await service.complete(
        code="auth-code",
        state=authorization.state,
        telegram_user_id="user-1",
        telegram_chat_id="chat-1",
        redirect_uri="https://example.test/oauth/callback",
        now=now + timedelta(seconds=2),
    )
    assert connection.secret_resource_name is not None
    assert secrets.values[connection.secret_resource_name] == "refresh-token-secret"
    assert await repository.get_calendar_connection("user-1") == connection

    disconnected = await service.disconnect(
        telegram_user_id="user-1", now=now + timedelta(seconds=4)
    )
    assert disconnected.status == CalendarConnectionStatus.DISCONNECTED
    assert oauth_client.revoked == ["refresh-token-secret"]
    assert secrets.deleted == [connection.secret_resource_name]

    with pytest.raises(ValueError, match="expired, used, or invalid"):
        await service.complete(
            code="auth-code",
            state=authorization.state,
            telegram_user_id="user-1",
            telegram_chat_id="chat-1",
            redirect_uri="https://example.test/oauth/callback",
            now=now + timedelta(seconds=3),
        )


async def test_calendar_provider_marks_effect_and_verifies_reread() -> None:
    incident, policy, now = build_plan()
    plan = CanonicalRecoveryPlanner().create_plan(incident=incident, policy=policy, now=now)
    action = next(action for action in plan.actions if action.category == ActionCategory.CALENDAR)
    action.target_external_id = "calendar-event-1"
    event = {
        "id": "calendar-event-1",
        "start": {"dateTime": "2026-08-23T10:00:00+00:00", "timeZone": "UTC"},
        "end": {"dateTime": "2026-08-23T11:00:00+00:00", "timeZone": "UTC"},
        "extendedProperties": {"private": {}},
    }
    api = FakeCalendarApi(event)
    provider = GoogleCalendarActionProvider(token_source=FakeTokenSource(), api=api)
    reference = await provider.apply(action)
    assert reference == "calendar-event-1"
    assert (
        api.patches[0]["extendedProperties"]["private"]["tripAgentEffectKey"] == action.effect_key
    )
    assert await provider.verify(action) is True


async def test_calendar_provider_creates_synthetic_trip_event_idempotently() -> None:
    incident, policy, now = build_plan()
    plan = CanonicalRecoveryPlanner().create_plan(incident=incident, policy=policy, now=now)
    action = next(action for action in plan.actions if action.category == ActionCategory.CALENDAR)
    action.target_external_id = "calendar:trip-1"
    api = FakeCalendarApi({"id": "calendar-event-1", "start": {}, "end": {}})
    provider = GoogleCalendarActionProvider(token_source=FakeTokenSource(), api=api)

    first = await provider.apply(action)
    action.provider_reference = first
    second = await provider.apply(action)

    assert first == second
    assert len(api.events) == 2
    assert await provider.verify(action) is True


async def test_refresh_source_reads_only_secret_manager_reference() -> None:
    repository = InMemoryIncidentRepository()
    secrets = FakeSecretStore()
    resource = await secrets.put_user_secret(
        user_id="calendar:user-1", value="refresh-token-secret"
    )
    now = datetime(2026, 8, 23, tzinfo=UTC)
    connection = CalendarConnection(
        telegram_user_id="user-1",
        secret_resource_name=resource,
        scopes=[CALENDAR_SCOPE],
        status=CalendarConnectionStatus.CONNECTED,
        created_at=now,
        updated_at=now,
    )
    await repository.save_calendar_connection(connection)
    source = GoogleRefreshTokenSource(
        repository=repository,
        secret_store=secrets,
        telegram_user_id="user-1",
        client_id="client-id",
        client_secret="client-secret",
        refresh_client=FakeRefreshClient(),
    )
    assert await source.access_token() == "access-token"


async def test_disconnected_calendar_is_terminal_and_never_claims_verified() -> None:
    incident, policy, now = build_plan()
    plan = CanonicalRecoveryPlanner().create_plan(incident=incident, policy=policy, now=now)
    action = next(action for action in plan.actions if action.category == ActionCategory.CALENDAR)
    repository = InMemoryIncidentRepository()
    assert await repository.put_action(action)
    provider = HybridActionProvider(fallback=PersistentDemoProvider(repository))
    executor = ActionExecutor(repository, provider)
    result = await executor.execute(action=action, worker_id="test", now=now)
    assert result is not None
    assert result.execution_status == ActionStatus.FAILED_TERMINAL
    assert result.attempt_count == 1
    assert result.provider_reference is None


async def test_recovery_selects_provider_for_the_traveler_context() -> None:
    from app.workflows.impact_analysis import ImpactAnalysisWorkflow
    from app.workflows.recovery import RecoveryWorkflow

    now = datetime(2026, 8, 23, tzinfo=UTC)
    repository = InMemoryIncidentRepository()
    await repository.seed_trip(build_demo_trip())
    outcome = await ImpactAnalysisWorkflow(repository, ValidInterpreter()).process(
        disruption_event(event_id="provider-selection-001")
    )
    selected: list[str] = []

    def provider_factory(user_id: str) -> PersistentDemoProvider:
        selected.append(user_id)
        return PersistentDemoProvider(repository)

    result = await RecoveryWorkflow(repository, provider_factory=provider_factory).start(
        incident_id=outcome.incident_id,
        policy=build_plan()[1],
        telegram_user_id="telegram-user-1",
        telegram_chat_id="telegram-chat-1",
        now=now,
    )
    assert result.incident_status.value == "WAITING_APPROVAL"
    assert selected == ["telegram-user-1"]
