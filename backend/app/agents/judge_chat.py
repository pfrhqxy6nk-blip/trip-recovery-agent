from __future__ import annotations

from datetime import UTC, datetime

from app.services.judge_quota import claim_judge_vertex_slot
from app.services.ports import IncidentRepository


class VertexJudgeChat:
    """Bounded Gemini concierge for a hackathon judge session.

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
        self,
        *,
        text: str,
        now: datetime,
        telegram_user_id: str | None = None,
        trip_context: str = "",
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
            capability="concierge",
        )
        if not allowed:
            return (
                "I’m temporarily unable to check live sources. I can still keep your itinerary "
                "organised and help you choose the next safe step."
            )
        try:
            from google.genai import types

            response = await self._client.aio.models.generate_content(
                model=self._model,
                contents=(
                    "You are Trip Watch, a calm premium travel agent inside Telegram. Reply in "
                    "natural English, as a capable person helping a traveler — never as a demo, "
                    "a chatbot, or an API. Lead with the useful next step. Keep it to at most "
                    "three compact paragraphs and 700 characters.\n\n"
                    "Use the private trip context only as factual context. Do not invent flight "
                    "status, availability, booking confirmation, prices, sources, or actions. "
                    "Never claim to have booked, paid, sent an email, or verified an external "
                    "system unless the context explicitly says so. For a time-sensitive change, "
                    "ask for the airline/airport notice or a screenshot and explain what you "
                    "will assess. For a planning request, explain that you can compare three "
                    "sourced transport-and-stay options. Do not ask for passwords, API keys, "
                    "passport data, or payment details.\n\n"
                    f"Private trip context:\n{trip_context or 'No saved trip yet.'}\n\n"
                    f"Traveler message: {normalized}"
                ),
                config=types.GenerateContentConfig(
                    temperature=0.35,
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
