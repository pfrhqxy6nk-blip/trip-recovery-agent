from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from app.models.domain import DeterministicImpact, DisruptionEvent, Trip


def disruption_event(
    *,
    event_id: str = "demo-delay-001",
    new_arrival: datetime = datetime(2026, 8, 20, 19, 45, tzinfo=UTC),
) -> DisruptionEvent:
    return DisruptionEvent(
        event_id=event_id,
        trip_id="demo-trip-001",
        type="flight_delay",
        flight="LO351",
        old_arrival=datetime(2026, 8, 20, 18, 0, tzinfo=UTC),
        new_arrival=new_arrival,
    )


class ValidInterpreter:
    model_id = "gemini-test-model"
    prompt_version = "impact-interpretation-test-v1"

    def __init__(self, *, delay: float = 0) -> None:
        self.calls = 0
        self.delay = delay

    async def interpret(
        self, event: DisruptionEvent, trip: Trip, deterministic_impact: DeterministicImpact
    ) -> dict[str, Any]:
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        return {
            "normalized_event_type": "flight_delay",
            "summary": f"{event.flight} changed and requires impact review.",
            "contextual_factors": ["protected connection status is unknown"],
            "explanation": (
                "The deterministic engine found the connection infeasible."
                if not deterministic_impact.connection_feasible
                else "The deterministic engine found that the connection remains feasible."
            ),
            "confidence": 0.92,
        }


class InvalidInterpreter:
    model_id = "gemini-test-model"
    prompt_version = "impact-interpretation-test-v1"

    async def interpret(
        self, event: DisruptionEvent, trip: Trip, deterministic_impact: DeterministicImpact
    ) -> dict[str, Any]:
        return {
            "connection_feasible": True,
            "summary": "Attempts to overwrite authoritative state",
        }
