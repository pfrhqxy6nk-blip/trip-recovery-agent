from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.agents.gemini import GeminiImpactInterpreter
from app.models.domain import DeterministicImpact, DisruptionEvent, Trip
from app.services.judge_quota import claim_judge_vertex_slot
from app.services.ports import IncidentRepository, TravelInterpreter


class JudgeImpactInterpreter:
    """Shared-credit impact explanation explicitly enabled for the judge sandbox.

    Recovery authority remains deterministic. Gemini only explains the already
    calculated impact; when the bounded project bucket is exhausted or Vertex is
    unavailable, a conservative deterministic explanation keeps the autonomous
    workflow useful without opening an unbounded billing path.
    """

    prompt_version = GeminiImpactInterpreter.prompt_version

    def __init__(
        self,
        repository: IncidentRepository,
        *,
        project: str,
        location: str,
        model: str,
        daily_limit: int,
        daily_user_limit: int | None = None,
    ) -> None:
        self._repository = repository
        self._daily_limit = daily_limit
        self._daily_user_limit = daily_user_limit or daily_limit
        self._interpreter: TravelInterpreter = GeminiImpactInterpreter(model)
        self.model_id = f"judge-vertex:{model}"
        del project, location

    async def interpret(
        self,
        event: DisruptionEvent,
        trip: Trip,
        deterministic_impact: DeterministicImpact,
    ) -> dict[str, Any]:
        now = datetime.now(UTC)
        day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        owner = trip.owner_user_id or f"trip:{trip.trip_id}"
        allowed = await claim_judge_vertex_slot(
            self._repository,
            telegram_user_id=owner.removeprefix("telegram:"),
            window_started_at=day,
            global_limit=self._daily_limit,
            per_user_limit=self._daily_user_limit,
        )
        if not allowed:
            return self._deterministic_interpretation(
                event, deterministic_impact, status="QUOTA_EXHAUSTED"
            )
        try:
            return await self._interpreter.interpret(event, trip, deterministic_impact)
        except Exception:
            # Explanatory Gemini output is optional; the deterministic impact and
            # policy/recovery workflow remain the source of truth for judges.
            return self._deterministic_interpretation(
                event, deterministic_impact, status="VERTEX_UNAVAILABLE"
            )

    @staticmethod
    def _deterministic_interpretation(
        event: DisruptionEvent,
        impact: DeterministicImpact,
        *,
        status: str | None = None,
    ) -> dict[str, Any]:
        delta = impact.arrival_delta_minutes
        affected = len(impact.affected_item_ids)
        feasibility = "no longer feasible" if not impact.connection_feasible else "still feasible"
        factors = ["deterministic impact engine", "source-validated disruption"]
        if status == "QUOTA_EXHAUSTED":
            factors.append("shared Vertex budget exhausted")
        elif status == "VERTEX_UNAVAILABLE":
            factors.append("shared Vertex reasoning unavailable")
        return {
            "normalized_event_type": event.type,
            "summary": f"Flight {event.flight} arrival changed by {delta} minutes.",
            "contextual_factors": factors,
            "explanation": (
                f"The protected connection is {feasibility}; {affected} downstream itinerary "
                "item(s) were evaluated before recovery policy was applied. "
                + (
                    "Gemini explanation was unavailable; no additional shared-credit request "
                    "was retried."
                    if status is not None
                    else ""
                )
            ),
            "confidence": 1.0,
        }
