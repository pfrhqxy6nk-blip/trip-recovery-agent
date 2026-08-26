from types import SimpleNamespace

import pytest
from app.agents import google_search_watch
from app.agents.google_search_watch import (
    GeminiGoogleSearchWatch,
    JudgeGoogleSearchWatch,
    PerTravelerGoogleSearchWatch,
    WatchProviderError,
)
from app.models.ai_connection import AiConnection, AiConnectionStatus
from app.models.domain import Trip
from app.models.watch import TripWatchpoint, WatchpointKind
from app.services.memory import InMemoryIncidentRepository


def test_grounding_urls_accepts_only_vertex_grounding_metadata() -> None:
    response = SimpleNamespace(
        candidates=[
            SimpleNamespace(
                grounding_metadata=SimpleNamespace(
                    grounding_chunks=[
                        SimpleNamespace(web=SimpleNamespace(uri="https://airport.example/notice")),
                        SimpleNamespace(web=SimpleNamespace(uri="http://not-secure.example")),
                    ]
                )
            )
        ]
    )

    assert GeminiGoogleSearchWatch._grounding_urls(response) == {"https://airport.example/notice"}


def _watchpoint() -> TripWatchpoint:
    return TripWatchpoint(
        watchpoint_id="watch-1",
        trip_id="trip-1",
        kind=WatchpointKind.FLIGHT_STATUS,
        query="LO351 flight status",
        trusted_domains=["airport.example"],
        due_at="2026-08-17T10:00:00Z",
    )


def test_affected_response_without_grounded_citation_degrades_instead_of_looking_healthy() -> None:
    response = SimpleNamespace(
        text='{"affects_trip": true, "source_url": "https://airport.example/notice"}',
        candidates=[],
    )

    with pytest.raises(WatchProviderError, match="INVALID_GROUNDED_RESPONSE"):
        GeminiGoogleSearchWatch._parse_response(response, _watchpoint())


def test_non_object_grounded_response_is_bounded_as_invalid() -> None:
    response = SimpleNamespace(text="[]", candidates=[])

    with pytest.raises(WatchProviderError, match="INVALID_GROUNDED_RESPONSE"):
        GeminiGoogleSearchWatch._parse_response(response, _watchpoint())


class SecretStore:
    async def put_user_secret(self, *, user_id: str, value: str) -> str:
        raise AssertionError("not used")

    async def delete_secret(self, *, resource_name: str) -> None:
        raise AssertionError("not used")

    async def access_secret(self, *, resource_name: str) -> str:
        assert resource_name == "projects/test/secrets/user/versions/1"
        return "user-owned-key"


async def test_trip_watch_uses_only_the_trip_owners_gemini_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = InMemoryIncidentRepository()
    await repository.seed_trip(
        Trip(
            trip_id="trip-1",
            owner_user_id="telegram:101",
            origin="WAW",
            destination="MUC",
            starts_at="2026-08-20T10:00:00Z",
            ends_at="2026-08-20T12:00:00Z",
        )
    )
    await repository.save_ai_connection(
        AiConnection(
            telegram_user_id="101",
            status=AiConnectionStatus.CONNECTED,
            secret_resource_name="projects/test/secrets/user/versions/1",
            created_at="2026-08-17T10:00:00Z",
        )
    )
    used_keys: list[str | None] = []

    class FakeGeminiWatch:
        def __init__(self, **kwargs: object) -> None:
            api_key = kwargs.get("api_key")
            used_keys.append(api_key if isinstance(api_key, str) else None)

        async def observe(self, watchpoint: TripWatchpoint) -> None:
            return None

    monkeypatch.setattr(google_search_watch, "GeminiGoogleSearchWatch", FakeGeminiWatch)
    watch = PerTravelerGoogleSearchWatch(
        repository=repository,
        secret_store=SecretStore(),
        project="project",
        location="global",
        model="gemini-test",
    )
    await watch.observe(
        TripWatchpoint(
            watchpoint_id="watch-1",
            trip_id="trip-1",
            kind=WatchpointKind.FLIGHT_STATUS,
            query="LO351 flight status",
            due_at="2026-08-17T10:00:00Z",
        )
    )

    assert used_keys == ["user-owned-key"]


async def test_trip_watch_marks_a_telegram_trip_without_connected_gemini() -> None:
    repository = InMemoryIncidentRepository()
    await repository.seed_trip(
        Trip(
            trip_id="trip-1",
            owner_user_id="telegram:101",
            origin="WAW",
            destination="MUC",
            starts_at="2026-08-20T10:00:00Z",
            ends_at="2026-08-20T12:00:00Z",
        )
    )
    watch = PerTravelerGoogleSearchWatch(
        repository=repository,
        secret_store=SecretStore(),
        project="project",
        location="global",
        model="gemini-test",
    )

    with pytest.raises(WatchProviderError, match="AI_CONNECTION_REQUIRED"):
        await watch.observe(
            TripWatchpoint(
                watchpoint_id="watch-1",
                trip_id="trip-1",
                kind=WatchpointKind.FLIGHT_STATUS,
                query="LO351 flight status",
                due_at="2026-08-17T10:00:00Z",
            )
        )


async def test_trip_watch_rejects_unowned_trip_instead_of_reporting_healthy() -> None:
    repository = InMemoryIncidentRepository()
    await repository.seed_trip(
        Trip(
            trip_id="trip-1",
            origin="WAW",
            destination="MUC",
            starts_at="2026-08-20T10:00:00Z",
            ends_at="2026-08-20T12:00:00Z",
        )
    )
    watch = PerTravelerGoogleSearchWatch(
        repository=repository,
        secret_store=SecretStore(),
        project="project",
        location="global",
        model="gemini-test",
    )

    with pytest.raises(WatchProviderError, match="TRIP_OWNER_NOT_BOUND"):
        await watch.observe(
            TripWatchpoint(
                watchpoint_id="watch-1",
                trip_id="trip-1",
                kind=WatchpointKind.FLIGHT_STATUS,
                query="LO351 flight status",
                due_at="2026-08-17T10:00:00Z",
            )
        )


async def test_trip_watch_surfaces_sanitized_provider_failure_for_degraded_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = InMemoryIncidentRepository()
    await repository.seed_trip(
        Trip(
            trip_id="trip-1",
            owner_user_id="telegram:101",
            origin="WAW",
            destination="MUC",
            starts_at="2026-08-20T10:00:00Z",
            ends_at="2026-08-20T12:00:00Z",
        )
    )
    await repository.save_ai_connection(
        AiConnection(
            telegram_user_id="101",
            status=AiConnectionStatus.CONNECTED,
            secret_resource_name="projects/test/secrets/user/versions/1",
            created_at="2026-08-17T10:00:00Z",
        )
    )

    class FailingGeminiWatch:
        def __init__(self, **kwargs: object) -> None:
            pass

        async def observe(self, watchpoint: TripWatchpoint) -> None:
            raise RuntimeError("secret-looking provider detail")

    monkeypatch.setattr(google_search_watch, "GeminiGoogleSearchWatch", FailingGeminiWatch)
    watch = PerTravelerGoogleSearchWatch(
        repository=repository,
        secret_store=SecretStore(),
        project="project",
        location="global",
        model="gemini-test",
    )

    with pytest.raises(WatchProviderError, match="SEARCH_PROVIDER_ERROR") as failure:
        await watch.observe(
            TripWatchpoint(
                watchpoint_id="watch-1",
                trip_id="trip-1",
                kind=WatchpointKind.FLIGHT_STATUS,
                query="LO351 flight status",
                due_at="2026-08-17T10:00:00Z",
            )
        )
    assert "secret-looking" not in str(failure.value)


async def test_judge_quota_exhaustion_is_not_reported_as_a_healthy_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeGeminiWatch:
        def __init__(self, **kwargs: object) -> None:
            pass

        async def observe(self, watchpoint: TripWatchpoint) -> None:
            return None

    monkeypatch.setattr(google_search_watch, "GeminiGoogleSearchWatch", FakeGeminiWatch)
    watch = JudgeGoogleSearchWatch(
        InMemoryIncidentRepository(),
        project="project",
        location="global",
        model="gemini-test",
        daily_limit=0,
    )

    with pytest.raises(WatchProviderError, match="JUDGE_QUOTA_EXHAUSTED"):
        await watch.observe(
            TripWatchpoint(
                watchpoint_id="watch-1",
                trip_id="trip-1",
                kind=WatchpointKind.WEATHER_IMPACT,
                query="Lisbon weather warning",
                due_at="2026-08-17T10:00:00Z",
            )
        )
