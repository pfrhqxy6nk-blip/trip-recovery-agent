from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.agents.gemini import GeminiImpactInterpreter
from app.models.ai_connection import (
    AiConnectionStatus,
    AiProviderSelector,
)
from app.models.domain import DeterministicImpact, DisruptionEvent, Trip
from app.services.ports import IncidentRepository, SecretStore, TravelInterpreter


class AiConnectionNeedsAttention(RuntimeError):
    pass


class PerTravelerGeminiRouter:
    """Select an explicit AI identity; never fall back from BYOK to system Vertex."""

    prompt_version = GeminiImpactInterpreter.prompt_version

    def __init__(
        self,
        repository: IncidentRepository,
        secret_store: SecretStore,
        system_interpreter: TravelInterpreter,
        byok_model_id: str,
        interpreter_factory: Callable[[str], TravelInterpreter] | None = None,
        judge_interpreter: TravelInterpreter | None = None,
    ) -> None:
        self._repository = repository
        self._secret_store = secret_store
        self._system = system_interpreter
        self._byok_model_id = byok_model_id
        self._judge = judge_interpreter
        self._factory = interpreter_factory or (
            lambda credential: GeminiImpactInterpreter(byok_model_id, api_key=credential)
        )
        self.model_id = (
            getattr(judge_interpreter, "model_id", f"judge-vertex:{byok_model_id}")
            if judge_interpreter is not None
            else f"per-traveler:{byok_model_id}"
        )

    async def interpret(
        self,
        event: DisruptionEvent,
        trip: Trip,
        deterministic_impact: DeterministicImpact,
    ) -> dict[str, Any]:
        if trip.owner_user_id is None or trip.owner_user_id.startswith("system:"):
            return await self._system.interpret(event, trip, deterministic_impact)
        if not trip.owner_user_id.startswith("telegram:"):
            raise AiConnectionNeedsAttention("AI connection needs attention")
        if self._judge is not None:
            # This branch is explicit judge-mode configuration, not a fallback
            # for a traveler's disconnected BYOK identity.
            return await self._judge.interpret(event, trip, deterministic_impact)
        telegram_user_id = trip.owner_user_id.removeprefix("telegram:")
        connection = await self._repository.get_ai_connection(telegram_user_id)
        if (
            connection is None
            or connection.selector != AiProviderSelector.USER_MANAGED_GEMINI
            or connection.status != AiConnectionStatus.CONNECTED
            or connection.secret_resource_name is None
        ):
            raise AiConnectionNeedsAttention("AI connection needs attention")
        try:
            credential = await self._secret_store.access_secret(
                resource_name=connection.secret_resource_name
            )
            interpreter = self._factory(credential)
            return await interpreter.interpret(event, trip, deterministic_impact)
        except AiConnectionNeedsAttention:
            raise
        except Exception:
            raise AiConnectionNeedsAttention("AI connection needs attention") from None
