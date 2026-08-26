from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

from app.agents.google_search_watch import WatchProviderError
from app.models.domain import TravelItem
from app.models.enums import ItemType
from app.models.monitoring import ObservationSnapshot
from app.models.watch import GroundedTravelSignal, SourceTrust, TripWatchpoint, WatchpointKind
from app.providers.amadeus import AmadeusFlightStatusError
from app.services.monitoring import MonitoringError, MonitoringService
from app.services.ports import IncidentRepository
from app.services.trip_watch_workflow import WatchGrounder

logger = logging.getLogger(__name__)


class FlightStatusClient(Protocol):
    async def fetch_snapshot(
        self, *, subscription_id: str, source_id: str, item: TravelItem, observed_at: datetime
    ) -> ObservationSnapshot: ...


class AmadeusFlightWatch:
    """Authoritative flight-status grounder for the autonomous watch loop.

    The adapter only handles a flight-status watchpoint whose subscription was
    explicitly activated for Amadeus.  It returns the same cited fact contract as
    Search Watch, so the existing deterministic validator and recovery workflow
    remain the single authority for downstream actions.
    """

    _SOURCE_ID = "amadeus-flight-status-v1"
    _SOURCE_URL = "https://api.amadeus.com/v2/schedule/flights"

    def __init__(
        self,
        repository: IncidentRepository,
        client: FlightStatusClient,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._client = client
        self._monitoring = MonitoringService(repository)
        self._clock = clock or (lambda: datetime.now(UTC))

    async def observe(self, watchpoint: TripWatchpoint) -> GroundedTravelSignal | None:
        if watchpoint.kind != WatchpointKind.FLIGHT_STATUS:
            return None
        if watchpoint.item_id is None:
            raise WatchProviderError("AMADEUS_WATCHPOINT_NOT_BOUND")
        if "api.amadeus.com" not in watchpoint.trusted_domains:
            logger.warning(
                "AMADEUS_WATCHPOINT_NOT_BOUND",
                extra={"watchpoint_id": watchpoint.watchpoint_id},
            )
            raise WatchProviderError("AMADEUS_WATCHPOINT_NOT_BOUND")
        trip = await self._repository.get_trip(watchpoint.trip_id)
        if trip is None:
            raise WatchProviderError("AMADEUS_TRIP_NOT_FOUND")
        item = next(
            (candidate for candidate in trip.items if candidate.item_id == watchpoint.item_id),
            None,
        )
        if item is None or item.type != ItemType.FLIGHT:
            raise WatchProviderError("AMADEUS_ITEM_NOT_BOUND")
        subscription_id = f"monitor:{trip.trip_id}:{item.item_id}"
        subscription = await self._repository.get_monitoring_subscription(subscription_id)
        if subscription is None or subscription.source_id != self._SOURCE_ID:
            logger.warning(
                "AMADEUS_SUBSCRIPTION_NOT_BOUND",
                extra={"watchpoint_id": watchpoint.watchpoint_id},
            )
            raise WatchProviderError("AMADEUS_SUBSCRIPTION_NOT_BOUND")
        observed_at = self._clock()
        try:
            snapshot = await self._client.fetch_snapshot(
                subscription_id=subscription.subscription_id,
                source_id=self._SOURCE_ID,
                item=item,
                observed_at=observed_at,
            )
            event = await self._monitoring.ingest_snapshot(snapshot)
        except (AmadeusFlightStatusError, MonitoringError):
            await self._monitoring.mark_live_status_degraded(
                subscription_id=subscription.subscription_id,
                now=observed_at,
            )
            logger.warning(
                "AMADEUS_FLIGHT_CHECK_FAILED",
                extra={"watchpoint_id": watchpoint.watchpoint_id},
            )
            # A failed live check is not evidence of an on-time flight.  Raise a
            # bounded provider error so TripWatchWorkflow also keeps the
            # watchpoint visibly degraded and retries on the next tick.
            raise WatchProviderError("AMADEUS_PROVIDER_ERROR") from None
        except Exception:
            # httpx and credential-library failures are not all wrapped by the
            # provider adapter. They still mean the live source is unknown and
            # must degrade coverage before the bounded error is retried.
            await self._monitoring.mark_live_status_degraded(
                subscription_id=subscription.subscription_id,
                now=observed_at,
            )
            logger.warning(
                "AMADEUS_FLIGHT_PROVIDER_ERROR",
                extra={"watchpoint_id": watchpoint.watchpoint_id},
            )
            raise WatchProviderError("AMADEUS_PROVIDER_ERROR") from None
        if event is None:
            return None
        return GroundedTravelSignal(
            watchpoint_id=watchpoint.watchpoint_id,
            summary=(
                f"Amadeus reports {item.external_id or item.provider} arriving at "
                f"{event.new_arrival.isoformat()} instead of {event.old_arrival.isoformat()}."
            ),
            source_url=self._SOURCE_URL,
            source_title="Amadeus Flight Status",
            trust=SourceTrust.OFFICIAL,
            source_updated_at=snapshot.source_updated_at,
            observed_at=snapshot.observed_at,
            affects_trip=True,
            suggested_event_type="FLIGHT_ARRIVAL_DELAY",
            observed_flight=item.external_id,
            old_arrival=event.old_arrival,
            new_arrival=event.new_arrival,
        )


class AutonomousWatchGrounder:
    """Route flight status to Amadeus and every other watchpoint to Search Watch."""

    def __init__(self, search: WatchGrounder, flight: AmadeusFlightWatch | None = None) -> None:
        self._search = search
        self._flight = flight

    async def observe(self, watchpoint: TripWatchpoint) -> GroundedTravelSignal | None:
        if watchpoint.kind == WatchpointKind.FLIGHT_STATUS and self._flight is not None:
            return await self._flight.observe(watchpoint)
        return await self._search.observe(watchpoint)
