from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from app.models.domain import DeterministicImpact, DisruptionEvent, TravelInterpretation, Trip


class GeminiImpactInterpreter:
    """Google ADK agent backed by the configured Gemini model on Vertex AI."""

    prompt_version = "impact-interpretation-v1"

    def __init__(self, model_id: str) -> None:
        if not model_id:
            raise ValueError("GEMINI_MODEL_ID must be configured for the real interpreter")

        from google.adk.agents import Agent
        from google.adk.models import Gemini
        from google.adk.runners import Runner
        from google.adk.sessions import InMemorySessionService

        self.model_id = model_id
        self._session_service = InMemorySessionService()
        self._agent = Agent(
            name="impact_interpreter",
            model=Gemini(model=model_id),
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
                "event": event.model_dump(mode="json"),
                "trip": trip.model_dump(mode="json"),
                "deterministic_impact": deterministic_impact.model_dump(mode="json"),
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
