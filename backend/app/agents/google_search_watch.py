from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any

from app.models.ai_connection import AiConnectionStatus, AiProviderSelector
from app.models.watch import GroundedTravelSignal, SourceTrust, TripWatchpoint
from app.services.judge_quota import claim_judge_vertex_slot
from app.services.ports import IncidentRepository, SecretStore
from app.services.signal_validation import official_source_url_is_trusted


class WatchProviderError(RuntimeError):
    """Sanitized provider failure that the scheduler can persist safely."""

    _ALLOWED_CODES = {
        "AI_CONNECTION_REQUIRED",
        "EMPTY_GROUNDED_RESPONSE",
        "INVALID_GROUNDED_RESPONSE",
        "JUDGE_QUOTA_EXHAUSTED",
        "SEARCH_PROVIDER_ERROR",
        "SEARCH_PROVIDER_TIMEOUT",
        "AMADEUS_ITEM_NOT_BOUND",
        "AMADEUS_PROVIDER_ERROR",
        "AMADEUS_SUBSCRIPTION_NOT_BOUND",
        "AMADEUS_TRIP_NOT_FOUND",
        "AMADEUS_WATCHPOINT_NOT_BOUND",
        "TRIP_NOT_FOUND",
        "TRIP_OWNER_NOT_BOUND",
    }

    def __init__(self, code: str) -> None:
        # Provider SDK messages can contain URLs, credentials, or traveler data.
        # Keep only a short allow-listed operational code for durable state/UI.
        safe_code = code if code in self._ALLOWED_CODES else "PROVIDER_ERROR"
        self.code = safe_code
        super().__init__(safe_code)


class GeminiGoogleSearchWatch:
    """Gemini-on-Vertex Google Search grounding for public trip signals.

    The prompt contains only the watchpoint query, never booking references, documents,
    traveler identity, or policy. A response without an HTTPS source is discarded.
    """

    _REQUEST_TIMEOUT_SECONDS = 20

    def __init__(
        self,
        *,
        project: str,
        location: str,
        model: str,
        api_key: str | None = None,
    ) -> None:
        if not model:
            raise ValueError("GEMINI_MODEL_ID is required for Trip Watch")
        from google import genai

        self._client = (
            genai.Client(api_key=api_key)
            if api_key is not None
            else genai.Client(vertexai=True, project=project, location=location)
        )
        self._model = model

    async def observe(self, watchpoint: TripWatchpoint) -> GroundedTravelSignal | None:
        from google.genai import types

        prompt = (
            "You are a travel-disruption observer. Search the public web for this narrowly "
            f"scoped watchpoint: {watchpoint.query!r}. Return JSON only with keys summary, "
            "source_url, source_title, trust (OFFICIAL or PUBLIC_SIGNAL), affects_trip, and "
            "suggested_event_type. If an official source explicitly attributes the disruption "
            "to the carrier, also return airline_fault=true; otherwise return null. For an "
            "official flight arrival delay, also return "
            "observed_flight, "
            "old_arrival and new_arrival as ISO 8601 timestamps. Return affects_trip=false when "
            "no current, sourced change "
            "is found. Never infer a booking-specific change or invent a source URL. The trusted "
            f"source hosts for recovery are {watchpoint.trusted_domains!r}; any other source "
            "must be PUBLIC_SIGNAL even if it reports the same event."
        )
        try:
            async with asyncio.timeout(self._REQUEST_TIMEOUT_SECONDS):
                response = await self._client.aio.models.generate_content(
                    model=self._model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        temperature=0,
                        tools=[types.Tool(google_search=types.GoogleSearch())],
                        response_mime_type="application/json",
                    ),
                )
        except TimeoutError:
            raise WatchProviderError("SEARCH_PROVIDER_TIMEOUT") from None
        return self._parse_response(response, watchpoint)

    @classmethod
    def _parse_response(
        cls, response: Any, watchpoint: TripWatchpoint
    ) -> GroundedTravelSignal | None:
        """Turn a grounded model response into a cited fact or a bounded error.

        An affected response without a URL that Vertex actually grounded is not an
        unchanged trip.  Treat it as degraded so the scheduler retries instead of
        clearing the watchpoint's error state and presenting false coverage.
        """
        text = getattr(response, "text", None)
        if not text:
            raise WatchProviderError("EMPTY_GROUNDED_RESPONSE")
        try:
            payload: dict[str, Any] = json.loads(text)
            if not isinstance(payload, dict):
                raise WatchProviderError("INVALID_GROUNDED_RESPONSE")
            if not payload.get("affects_trip"):
                return None
            source_url = str(payload["source_url"])
            if not source_url.startswith("https://") or source_url not in cls._grounding_urls(
                response
            ):
                raise WatchProviderError("INVALID_GROUNDED_RESPONSE")
            model_trust = SourceTrust(str(payload["trust"]))
            # Gemini's trust label is advisory. Recovery authority requires both
            # the label and a host in the deterministic watchpoint allow-list.
            trust = (
                SourceTrust.OFFICIAL
                if model_trust == SourceTrust.OFFICIAL
                and official_source_url_is_trusted(source_url, watchpoint.trusted_domains)
                else SourceTrust.PUBLIC_SIGNAL
            )
            return GroundedTravelSignal(
                watchpoint_id=watchpoint.watchpoint_id,
                summary=str(payload["summary"]),
                source_url=source_url,
                source_title=str(payload["source_title"]),
                trust=trust,
                observed_at=datetime.now().astimezone(),
                affects_trip=True,
                suggested_event_type=str(
                    payload.get("suggested_event_type") or "WEB_TRAVEL_SIGNAL"
                ),
                airline_fault=(
                    payload["airline_fault"]
                    if isinstance(payload.get("airline_fault"), bool)
                    else None
                ),
                observed_flight=payload.get("observed_flight"),
                old_arrival=payload.get("old_arrival"),
                new_arrival=payload.get("new_arrival"),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            # A malformed model response is not evidence of an unchanged trip.
            # Let the workflow persist a degraded state and retry later.
            raise WatchProviderError("INVALID_GROUNDED_RESPONSE") from None

    @staticmethod
    def _grounding_urls(response: Any) -> set[str]:
        """Extract only URLs supplied by Vertex grounding metadata, never model text."""
        candidates = getattr(response, "candidates", None) or []
        urls: set[str] = set()
        for candidate in candidates:
            metadata = getattr(candidate, "grounding_metadata", None)
            chunks = getattr(metadata, "grounding_chunks", None) or []
            for chunk in chunks:
                web = getattr(chunk, "web", None)
                uri = getattr(web, "uri", None)
                if isinstance(uri, str) and uri.startswith("https://"):
                    urls.add(uri)
        return urls


class PerTravelerGoogleSearchWatch:
    """Run public-source monitoring under the traveler's chosen Gemini quota.

    There is intentionally no fallback from a Telegram traveler's disconnected
    key to the application's Vertex project.  The worker marks that user's
    watchpoint as needing attention until they reconnect Gemini, keeping the
    billing promise explicit and preventing accidental cross-user selection.
    """

    def __init__(
        self,
        *,
        repository: IncidentRepository,
        secret_store: SecretStore,
        project: str,
        location: str,
        model: str,
    ) -> None:
        self._repository = repository
        self._secret_store = secret_store
        self._project = project
        self._location = location
        self._model = model

    async def observe(self, watchpoint: TripWatchpoint) -> GroundedTravelSignal | None:
        trip = await self._repository.get_trip(watchpoint.trip_id)
        if trip is None:
            raise WatchProviderError("TRIP_NOT_FOUND")
        if trip.owner_user_id is None or not trip.owner_user_id.startswith("telegram:"):
            # A non-owned trip cannot safely select a Gemini identity. Treat it
            # as a configuration fault instead of silently reporting a healthy
            # watch with no provider behind it.
            raise WatchProviderError("TRIP_OWNER_NOT_BOUND")
        connection = await self._repository.get_ai_connection(
            trip.owner_user_id.removeprefix("telegram:")
        )
        if (
            connection is None
            or connection.selector != AiProviderSelector.USER_MANAGED_GEMINI
            or connection.status != AiConnectionStatus.CONNECTED
            or connection.secret_resource_name is None
        ):
            # A Telegram trip without its explicitly selected Gemini identity
            # is not healthy monitoring. Surface a bounded state so the
            # scheduler/status view cannot present an unwatched trip as safe.
            raise WatchProviderError("AI_CONNECTION_REQUIRED")
        try:
            api_key = await self._secret_store.access_secret(
                resource_name=connection.secret_resource_name
            )
            return await GeminiGoogleSearchWatch(
                project=self._project,
                location=self._location,
                model=self._model,
                api_key=api_key,
            ).observe(watchpoint)
        except WatchProviderError:
            raise
        except Exception:
            # Surface a bounded failure to TripWatchWorkflow so the watchpoint is
            # marked degraded. Never chain the provider exception: SDK errors can
            # contain credentials, request URLs, or traveler-specific details.
            raise WatchProviderError("SEARCH_PROVIDER_ERROR") from None


class JudgeGoogleSearchWatch:
    """Shared, bounded Search Watch used only for the judge sandbox.

    This is intentionally opt-in at deployment time and has one daily project-wide
    bucket, so a judge can see autonomous monitoring without supplying a personal key
    or creating an unbounded billing path.
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
    ) -> None:
        self._repository = repository
        self._daily_limit = daily_limit
        self._daily_user_limit = daily_user_limit or daily_limit
        self._watch = GeminiGoogleSearchWatch(project=project, location=location, model=model)

    async def observe(self, watchpoint: TripWatchpoint) -> GroundedTravelSignal | None:
        now = datetime.now().astimezone()
        day = now.astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
        trip = await self._repository.get_trip(watchpoint.trip_id)
        owner = trip.owner_user_id if trip is not None else None
        allowed = await claim_judge_vertex_slot(
            self._repository,
            telegram_user_id=(owner or f"trip:{watchpoint.trip_id}").removeprefix("telegram:"),
            window_started_at=day,
            global_limit=self._daily_limit,
            per_user_limit=self._daily_user_limit,
        )
        if not allowed:
            # Exhausted shared judge credits mean the source was not checked.
            # Preserve that truth in the watchpoint instead of treating the
            # absence of a result as an on-time itinerary.
            raise WatchProviderError("JUDGE_QUOTA_EXHAUSTED")
        try:
            return await self._watch.observe(watchpoint)
        except WatchProviderError:
            raise
        except Exception:
            # Keep the same bounded failure contract as per-traveler monitoring.
            # The workflow records the degraded state and the judge sees truthful
            # coverage instead of a silent, apparently healthy watchpoint.
            raise WatchProviderError("SEARCH_PROVIDER_ERROR") from None
