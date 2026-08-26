from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.config import Settings
from app.main import AppContainer, create_app
from app.models.ai_connection import AiConnectionStatus
from app.services.ai_connections import AiConnectionService
from app.services.memory import InMemoryIncidentRepository, LocalEventPublisher
from app.workflows.impact_analysis import ImpactAnalysisWorkflow
from httpx import ASGITransport, AsyncClient

from tests.helpers import ValidInterpreter


class MemorySecretStore:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def put_user_secret(self, *, user_id: str, value: str) -> str:
        resource = f"projects/test/secrets/user-{len(self.values) + 1}/versions/1"
        self.values[resource] = value
        return resource

    async def delete_secret(self, *, resource_name: str) -> None:
        self.values.pop(resource_name, None)

    async def access_secret(self, *, resource_name: str) -> str:
        return self.values[resource_name]


class AcceptCredential:
    async def validate(self, api_key: str) -> bool:
        return api_key == "opaque-user-credential"


async def test_handoff_is_single_use_and_repository_never_stores_credential() -> None:
    now = datetime(2026, 8, 17, tzinfo=UTC)
    repository = InMemoryIncidentRepository()
    secrets = MemorySecretStore()
    service = AiConnectionService(repository, secrets, AcceptCredential())
    handoff = await service.create_handoff(telegram_user_id="101", telegram_chat_id="202", now=now)

    connected = await service.complete(
        token=handoff.token,
        telegram_user_id="101",
        telegram_chat_id="202",
        api_key="opaque-user-credential",
        now=now,
    )

    assert connected.status == AiConnectionStatus.CONNECTED
    assert connected.secret_resource_name in secrets.values
    assert "opaque-user-credential" not in repr(repository.ai_connections)
    assert "opaque-user-credential" not in repr(repository.ai_handoffs)
    replayed = await repository.consume_ai_handoff(
        state_hash=next(iter(repository.ai_handoffs)),
        telegram_user_id="101",
        telegram_chat_id="202",
        now=now,
    )
    assert replayed is None


async def test_expired_handoff_is_rejected_before_secret_storage() -> None:
    now = datetime(2026, 8, 17, tzinfo=UTC)
    repository = InMemoryIncidentRepository()
    secrets = MemorySecretStore()
    service = AiConnectionService(repository, secrets, AcceptCredential())
    handoff = await service.create_handoff(telegram_user_id="101", telegram_chat_id="202", now=now)

    try:
        await service.complete(
            token=handoff.token,
            telegram_user_id="101",
            telegram_chat_id="202",
            api_key="opaque-user-credential",
            now=now + timedelta(minutes=11),
        )
    except ValueError as exc:
        assert "credential" not in str(exc)
    else:
        raise AssertionError("expired handoff was accepted")
    assert secrets.values == {}


async def test_cross_user_handoff_is_rejected_without_consuming_it() -> None:
    now = datetime(2026, 8, 17, tzinfo=UTC)
    repository = InMemoryIncidentRepository()
    secrets = MemorySecretStore()
    service = AiConnectionService(repository, secrets, AcceptCredential())
    handoff = await service.create_handoff(telegram_user_id="101", telegram_chat_id="202", now=now)

    try:
        await service.complete(
            token=handoff.token,
            telegram_user_id="attacker",
            telegram_chat_id="202",
            api_key="opaque-user-credential",
            now=now,
        )
    except ValueError:
        pass
    else:
        raise AssertionError("cross-user handoff was accepted")
    legitimate = await service.complete(
        token=handoff.token,
        telegram_user_id="101",
        telegram_chat_id="202",
        api_key="opaque-user-credential",
        now=now,
    )
    assert legitimate.status == AiConnectionStatus.CONNECTED


async def test_cross_chat_handoff_is_rejected_and_disconnect_destroys_mapping() -> None:
    now = datetime(2026, 8, 17, tzinfo=UTC)
    repository = InMemoryIncidentRepository()
    secrets = MemorySecretStore()
    service = AiConnectionService(repository, secrets, AcceptCredential())
    handoff = await service.create_handoff(telegram_user_id="101", telegram_chat_id="202", now=now)
    try:
        await service.complete(
            token=handoff.token,
            telegram_user_id="101",
            telegram_chat_id="different-chat",
            api_key="opaque-user-credential",
            now=now,
        )
    except ValueError:
        pass
    else:
        raise AssertionError("cross-chat handoff was accepted")
    await service.complete(
        token=handoff.token,
        telegram_user_id="101",
        telegram_chat_id="202",
        api_key="opaque-user-credential",
        now=now,
    )

    disconnected = await service.disconnect(telegram_user_id="101", now=now)

    assert disconnected.status == AiConnectionStatus.DISCONNECTED
    assert disconnected.secret_resource_name is None
    assert secrets.values == {}


async def test_public_edge_returns_only_non_secret_connection_metadata() -> None:
    now = datetime(2026, 8, 17, tzinfo=UTC)
    repository = InMemoryIncidentRepository()
    secrets = MemorySecretStore()
    service = AiConnectionService(repository, secrets, AcceptCredential())
    handoff = await service.create_handoff(telegram_user_id="101", telegram_chat_id="202", now=now)
    settings = Settings(pubsub_transport="local")
    container = AppContainer(
        settings=settings,
        repository=repository,
        publisher=LocalEventPublisher(),
        workflow=ImpactAnalysisWorkflow(repository, ValidInterpreter()),
        ai_connections=service,
        clock=lambda: now,
    )
    app = create_app(settings, container=container)
    app.state.container = container
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    try:
        response = await client.post(
            "/connections/gemini/complete",
            json={
                "token": handoff.token,
                "telegram_user_id": "101",
                "telegram_chat_id": "202",
                "api_key": "opaque-user-credential",
            },
        )
    finally:
        await client.aclose()

    assert response.status_code == 200
    assert response.json()["status"] == "CONNECTED"
    assert "opaque-user-credential" not in response.text
