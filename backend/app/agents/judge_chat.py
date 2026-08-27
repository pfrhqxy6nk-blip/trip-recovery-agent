from __future__ import annotations

from datetime import UTC, datetime

from app.services.judge_quota import claim_judge_vertex_slot
from app.services.ports import IncidentRepository


class VertexJudgeChat:
    """Bounded, read-only Gemini access for a hackathon judge session.

    This is deliberately separate from BYOK and recovery. It has a single
    Firestore-backed daily bucket shared by every Telegram user, a small output
    budget, and a prompt that forbids actions or credential collection.
    """

    def __init__(
        self,
        repository: IncidentRepository,
        *,
        project: str,
        location: str,
        model: str,
        daily_limit: int,
        daily_user_limit: int | None = None,
        max_output_tokens: int,
    ) -> None:
        from google import genai

        self._repository = repository
        self._client = genai.Client(vertexai=True, project=project, location=location)
        self._model = model
        self._daily_limit = daily_limit
        self._daily_user_limit = daily_user_limit or daily_limit
        self._max_output_tokens = max_output_tokens

    async def answer(
        self, *, text: str, now: datetime, telegram_user_id: str | None = None
    ) -> str | None:
        normalized = " ".join(text.split())
        if not normalized or len(normalized) > 1000:
            return None
        day = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        allowed = await claim_judge_vertex_slot(
            self._repository,
            telegram_user_id=telegram_user_id or "anonymous",
            window_started_at=day,
            global_limit=self._daily_limit,
            per_user_limit=self._daily_user_limit,
        )
        if not allowed:
            return (
                "The shared judge demo quota is full for today. The deterministic demo and "
                "trip status remain available."
            )
        try:
            from google.genai import types

            response = await self._client.aio.models.generate_content(
                model=self._model,
                contents=(
                    "You are the read-only explanation layer of Trip Recovery Agent. Answer "
                    "the judge's question in at most 500 characters. Explain monitoring, "
                    "sources, impact, policy, or the demo. Never claim to have changed a "
                    "booking, sent a message, charged money, or verified an external system. "
                    "Never request API keys, passwords, passport data, or payment details. "
                    f"Judge question: {normalized}"
                ),
                config=types.GenerateContentConfig(
                    temperature=0.2,
                    max_output_tokens=self._max_output_tokens,
                    tools=[types.Tool(google_search=types.GoogleSearch())],
                ),
            )
            answer = (response.text or "").strip()
            return answer[:2000] if answer else None
        except Exception:
            return (
                "Google Gemini project quota or billing is unavailable right now. "
                "The safe deterministic demo still works, but no more shared-credit "
                "requests will be attempted."
            )
