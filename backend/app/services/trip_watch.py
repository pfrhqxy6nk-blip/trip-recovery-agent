from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.models.domain import Trip
from app.models.enums import ItemType
from app.models.watch import TripWatchpoint, WatchpointKind


class TripWatchPlanner:
    """Deterministically creates the small set of questions that protect a trip."""

    _AIRLINE_DOMAINS: dict[str, tuple[str, ...]] = {
        "lot": ("lot.com",),
        "lufthansa": ("lufthansa.com",),
        "british airways": ("ba.com",),
        "ryanair": ("ryanair.com",),
        "easyjet": ("easyjet.com",),
        "wizz air": ("wizzair.com",),
        "air france": ("airfrance.com",),
        "klm": ("klm.com",),
        "swiss": ("swiss.com",),
    }

    def build(
        self, trip: Trip, *, now: datetime, amadeus_enabled: bool = False
    ) -> list[TripWatchpoint]:
        """Create an immediately actionable, cost-bounded watch schedule.

        A trip must be observed when it is saved, not first at departure.  The
        cadence becomes denser near the affected itinerary item, so an itinerary
        added months ahead does not consume a Gemini request every 30 minutes.
        """
        watchpoints: list[TripWatchpoint] = []
        for item in trip.items:
            if item.type == ItemType.FLIGHT:
                flight = item.external_id or item.provider
                watchpoints.extend(
                    [
                        self._point(
                            trip,
                            item.item_id,
                            WatchpointKind.FLIGHT_STATUS,
                            f"{flight} flight status delay cancellation",
                            now,
                            item.start_at,
                            trusted_domains=self._flight_domains(
                                item.provider, amadeus_enabled=amadeus_enabled
                            ),
                        ),
                        self._point(
                            trip,
                            item.item_id,
                            WatchpointKind.AIRPORT_DISRUPTION,
                            f"{item.origin or ''} airport disruption strike closure weather",
                            now,
                            item.start_at,
                        ),
                        self._point(
                            trip,
                            item.item_id,
                            WatchpointKind.WEATHER_IMPACT,
                            (
                                f"{item.destination or item.location or ''} "
                                "weather warning travel disruption"
                            ),
                            now,
                            item.end_at,
                        ),
                    ]
                )
            elif item.type == ItemType.HOTEL_ARRIVAL:
                watchpoints.append(
                    self._point(
                        trip,
                        item.item_id,
                        WatchpointKind.HOTEL_STATUS,
                        f"{item.location or item.provider} closure evacuation check-in notice",
                        now,
                        item.start_at,
                    )
                )
            elif item.type == ItemType.TRANSFER:
                watchpoints.append(
                    self._point(
                        trip,
                        item.item_id,
                        WatchpointKind.GROUND_TRANSFER,
                        f"{item.location or item.destination or ''} transport strike road closure",
                        now,
                        item.start_at,
                    )
                )
            elif item.type == ItemType.ACTIVITY:
                watchpoints.append(
                    self._point(
                        trip,
                        item.item_id,
                        WatchpointKind.ACTIVITY_STATUS,
                        f"{item.location or item.provider} closed changed hours cancelled",
                        now,
                        item.start_at,
                    )
                )
        return watchpoints

    @staticmethod
    def _point(
        trip: Trip,
        item_id: str,
        kind: WatchpointKind,
        query: str,
        now: datetime,
        affected_at: datetime,
        trusted_domains: list[str] | None = None,
    ) -> TripWatchpoint:
        interval = TripWatchPlanner._interval_minutes(now, affected_at)
        return TripWatchpoint(
            watchpoint_id=f"watch:{trip.trip_id}:{item_id}:{kind.value.lower()}",
            trip_id=trip.trip_id,
            item_id=item_id,
            kind=kind,
            query=" ".join(query.split()),
            trusted_domains=trusted_domains or [],
            due_at=now,
            check_interval_minutes=interval,
        )

    @staticmethod
    def _interval_minutes(now: datetime, affected_at: datetime) -> int:
        horizon = affected_at.astimezone(UTC) - now.astimezone(UTC)
        if horizon > timedelta(days=7):
            return 720
        if horizon > timedelta(days=3):
            return 360
        if horizon > timedelta(hours=6):
            return 60
        return 30

    @classmethod
    def _airline_domains(cls, provider: str) -> list[str]:
        normalized = " ".join(provider.lower().split())
        if normalized in cls._AIRLINE_DOMAINS:
            return list(cls._AIRLINE_DOMAINS[normalized])
        for airline, domains in cls._AIRLINE_DOMAINS.items():
            if airline in normalized:
                return list(domains)
        return []

    @classmethod
    def _flight_domains(cls, provider: str, *, amadeus_enabled: bool) -> list[str]:
        domains = cls._airline_domains(provider)
        if amadeus_enabled and "api.amadeus.com" not in domains:
            domains.append("api.amadeus.com")
        return domains
