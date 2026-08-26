from datetime import UTC, datetime
from typing import Any

import pytest
from app.agents.judge_impact import JudgeImpactInterpreter
from app.agents.router import AiConnectionNeedsAttention, PerTravelerGeminiRouter
from app.demo_data import build_owned_demo_trip
from app.models.ai_connection import AiConnection, AiConnectionStatus
from app.services.impact import DeterministicImpactEngine
from app.services.memory import InMemoryIncidentRepository

from tests.helpers import ValidInterpreter, disruption_event


class OneSecret:
    async def put_user_secret(self, *, user_id: str, value: str) -> str:
        raise AssertionError("not used")

    async def delete_secret(self, *, resource_name: str) -> None:
        raise AssertionError("not used")

    async def access_secret(self, *, resource_name: str) -> str:
        assert resource_name == "projects/test/secrets/gemini/versions/1"
        return "opaque-user-credential"


async def test_owned_trip_uses_only_selected_user_connection() -> None:
    repository = InMemoryIncidentRepository()
    now = datetime(2026, 8, 17, tzinfo=UTC)
    await repository.save_ai_connection(
        AiConnection(
            telegram_user_id="101",
            status=AiConnectionStatus.CONNECTED,
            secret_resource_name="projects/test/secrets/gemini/versions/1",
            key_fingerprint="…12345678",
            created_at=now,
            validated_at=now,
        )
    )
    credentials: list[str] = []

    def factory(credential: str) -> ValidInterpreter:
        credentials.append(credential)
        return ValidInterpreter()

    router = PerTravelerGeminiRouter(
        repository,
        OneSecret(),
        ValidInterpreter(),
        "gemini-test",
        interpreter_factory=factory,
    )
    trip = build_owned_demo_trip(owner_user_id="telegram:101", trip_id="pilot-trip:101")
    event = disruption_event().model_copy(update={"trip_id": trip.trip_id})
    impact = DeterministicImpactEngine().calculate(event, trip)

    result = await router.interpret(event, trip, impact)

    assert result["normalized_event_type"] == "flight_delay"
    assert credentials == ["opaque-user-credential"]


async def test_owned_trip_never_falls_back_when_connection_is_missing() -> None:
    repository = InMemoryIncidentRepository()
    router = PerTravelerGeminiRouter(repository, OneSecret(), ValidInterpreter(), "gemini-test")
    trip = build_owned_demo_trip(owner_user_id="telegram:101", trip_id="pilot-trip:101")
    event = disruption_event().model_copy(update={"trip_id": trip.trip_id})
    impact = DeterministicImpactEngine().calculate(event, trip)

    with pytest.raises(AiConnectionNeedsAttention, match="needs attention"):
        await router.interpret(event, trip, impact)


async def test_judge_mode_uses_explicit_shared_interpreter_without_user_connection() -> None:
    repository = InMemoryIncidentRepository()
    judge = ValidInterpreter()
    router = PerTravelerGeminiRouter(
        repository,
        OneSecret(),
        ValidInterpreter(),
        "gemini-test",
        judge_interpreter=judge,
    )
    trip = build_owned_demo_trip(owner_user_id="telegram:judge", trip_id="pilot-trip:judge")
    event = disruption_event().model_copy(update={"trip_id": trip.trip_id})
    impact = DeterministicImpactEngine().calculate(event, trip)

    result = await router.interpret(event, trip, impact)

    assert result["normalized_event_type"] == "flight_delay"
    assert judge.calls == 1
    assert router.model_id == "gemini-test-model"


async def test_judge_impact_interpreter_stops_at_shared_daily_limit() -> None:
    repository = InMemoryIncidentRepository()
    interpreter = JudgeImpactInterpreter(
        repository,
        project="test-project",
        location="europe-west3",
        model="gemini-test",
        daily_limit=0,
    )
    trip = build_owned_demo_trip(owner_user_id="telegram:judge", trip_id="pilot-trip:judge")
    event = disruption_event().model_copy(update={"trip_id": trip.trip_id})
    impact = DeterministicImpactEngine().calculate(event, trip)

    result: dict[str, Any] = await interpreter.interpret(event, trip, impact)

    assert result["confidence"] == 1.0
    assert result["contextual_factors"][-1] == "shared Vertex budget exhausted"


async def test_judge_impact_interpreter_marks_vertex_outage_without_retrying() -> None:
    class BrokenInterpreter:
        async def interpret(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
            raise RuntimeError("provider details must not escape")

    repository = InMemoryIncidentRepository()
    interpreter = JudgeImpactInterpreter(
        repository,
        project="test-project",
        location="europe-west3",
        model="gemini-test",
        daily_limit=1,
    )
    interpreter._interpreter = BrokenInterpreter()  # type: ignore[assignment]
    trip = build_owned_demo_trip(owner_user_id="telegram:judge", trip_id="pilot-trip:judge")
    event = disruption_event().model_copy(update={"trip_id": trip.trip_id})
    impact = DeterministicImpactEngine().calculate(event, trip)

    result: dict[str, Any] = await interpreter.interpret(event, trip, impact)

    assert result["contextual_factors"][-1] == "shared Vertex reasoning unavailable"
    assert "provider details" not in result["explanation"]
