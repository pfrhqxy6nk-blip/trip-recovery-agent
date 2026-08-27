from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from app.models.domain import (
    DeterministicImpact,
    DisruptionEvent,
    TravelInterpretation,
    Trip,
)

_SAFE_EVENT_CONTEXT_KEYS = {
    "airline",
    "airport",
    "source",
    "source_title",
    "source_url",
    "weather",
    "severity",
    "status",
    "cause",
}
_SAFE_ITEM_FIELDS = (
    "type",
    "provider",
    "start_at",
    "end_at",
    "origin",
    "destination",
    "departure_terminal",
    "arrival_terminal",
    "location",
    "flexibility",
    "status",
)


def _safe_context(context: dict[str, Any]) -> dict[str, str | int | float | bool]:
    """Keep only bounded, public disruption context before calling an LLM.

    Telegram identities, booking references, email addresses and arbitrary provider
    payloads must never be sent to Gemini.  The allowlist is intentionally narrow;
    adding a field requires an explicit privacy review.
    """

    safe: dict[str, str | int | float | bool] = {}
    for key in _SAFE_EVENT_CONTEXT_KEYS:
        value = context.get(key)
        if isinstance(value, (str, int, float, bool)):
            safe[key] = value if not isinstance(value, str) else value[:500]
    return safe


def build_gemini_context_payload(
    event: DisruptionEvent,
    trip: Trip,
    deterministic_impact: DeterministicImpact,
) -> dict[str, Any]:
    """Build the privacy-minimized, deterministic context sent to Gemini.

    The rules engine remains authoritative. Gemini receives itinerary timing and
    public travel facts needed for explanation, but no user identifiers, PNRs,
    contact details, provider IDs, or raw nested payloads.
    """

    trip_items = [
        {
            field: item.model_dump(mode="json")[field]
            for field in _SAFE_ITEM_FIELDS
            if field in item.model_dump(mode="json")
        }
        for item in trip.items
    ]
    return {
        "event": {
            "type": event.type,
            "flight": event.flight,
            "old_arrival": event.old_arrival.isoformat(),
            "new_arrival": event.new_arrival.isoformat(),
            "context": _safe_context(event.context),
        },
        "trip": {
            "origin": trip.origin,
            "destination": trip.destination,
            "starts_at": trip.starts_at.isoformat(),
            "ends_at": trip.ends_at.isoformat(),
            "status": trip.status,
            "items": trip_items,
            "dependency_count": len(trip.dependencies),
            "minimum_buffers_minutes": [
                dependency.min_buffer_minutes for dependency in trip.dependencies
            ],
        },
        "deterministic_impact": {
            "arrival_delta_minutes": deterministic_impact.arrival_delta_minutes,
            "connection_feasible": deterministic_impact.connection_feasible,
            "affected_item_count": len(deterministic_impact.affected_item_ids),
            "affected_dependency_count": len(deterministic_impact.affected_dependency_ids),
            "buffer_violations": [
                violation.model_dump(mode="json")
                for violation in deterministic_impact.buffer_violations
            ],
            "engine_version": deterministic_impact.engine_version,
        },
    }


class GeminiImpactInterpreter:
    """Google ADK agent backed by the configured Gemini model on Vertex AI."""

    prompt_version = "impact-interpretation-v1"

    def __init__(self, model_id: str, *, api_key: str | None = None) -> None:
        if not model_id:
            raise ValueError("GEMINI_MODEL_ID must be configured for the real interpreter")

        from google.adk.agents import Agent
        from google.adk.models import Gemini
        from google.adk.runners import Runner
        from google.adk.sessions import InMemorySessionService

        self.model_id = model_id
        self._session_service = InMemorySessionService()
        model_kwargs = (
            {"client_kwargs": {"api_key": api_key, "vertexai": False}}
            if api_key is not None
            else {}
        )
        self._agent = Agent(
            name="impact_interpreter",
            model=Gemini(model=model_id, **model_kwargs),
            instruction=(
                "Interpret a validated travel disruption and its deterministic impact. "
                "The deterministic impact is authoritative: never recalculate or contradict "
                "connection_feasible, time arithmetic, buffers, affected items, or affected "
                "dependencies. Normalize messy context, identify contextual travel factors, "
                "and explain the impact clearly. Return only the required structured output."
            ),
            output_schema=TravelInterpretation,
            output_key="travel_interpretation",
        )
        self._runner = Runner(
            agent=self._agent,
            app_name="trip_recovery_agent",
            session_service=self._session_service,
        )

    async def interpret(
        self,
        event: DisruptionEvent,
        trip: Trip,
        deterministic_impact: DeterministicImpact,
    ) -> dict[str, Any]:
        from google.genai import types

        session_id = str(uuid4())
        user_id = "impact-workflow"
        await self._session_service.create_session(
            app_name="trip_recovery_agent", user_id=user_id, session_id=session_id
        )
        prompt = json.dumps(
            {
                "prompt_version": self.prompt_version,
                **build_gemini_context_payload(event, trip, deterministic_impact),
            },
            separators=(",", ":"),
        )
        message = types.Content(role="user", parts=[types.Part(text=prompt)])
        final_text: str | None = None
        async for response_event in self._runner.run_async(
            user_id=user_id, session_id=session_id, new_message=message
        ):
            if response_event.is_final_response() and response_event.content:
                parts = response_event.content.parts or []
                final_text = "".join(part.text or "" for part in parts)

        if not final_text:
            raise ValueError("Gemini returned no final structured response")
        parsed = json.loads(final_text)
        if not isinstance(parsed, dict):
            raise ValueError("Gemini structured response must be an object")
        return parsed
