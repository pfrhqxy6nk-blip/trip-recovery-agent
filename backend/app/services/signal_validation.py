from __future__ import annotations

from urllib.parse import urlparse

from app.models.domain import DisruptionEvent, Trip
from app.models.enums import ItemType
from app.models.watch import GroundedTravelSignal, SourceTrust, TripWatchpoint, WatchpointKind
from app.services.canonical_hash import grounded_signal_hash


class SignalRejected(ValueError):
    pass


def official_source_url_is_trusted(source_url: str, trusted_domains: list[str]) -> bool:
    """Require an HTTPS source host to match the watchpoint allow-list."""

    parsed = urlparse(source_url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme != "https" or not host:
        return False
    return any(host == domain or host.endswith(f".{domain}") for domain in trusted_domains)


class GroundedSignalValidator:
    """The deterministic boundary between web evidence and recovery authority."""

    def to_disruption(
        self, *, trip: Trip, watchpoint: TripWatchpoint, signal: GroundedTravelSignal
    ) -> DisruptionEvent:
        if signal.watchpoint_id != watchpoint.watchpoint_id or signal.trust != SourceTrust.OFFICIAL:
            raise SignalRejected("only an official signal for this watchpoint may trigger recovery")
        if not official_source_url_is_trusted(signal.source_url, watchpoint.trusted_domains):
            raise SignalRejected("official signal source is outside the watchpoint allow-list")
        if watchpoint.kind != WatchpointKind.FLIGHT_STATUS:
            raise SignalRejected(
                "this watchpoint may inform the traveler but cannot trigger recovery"
            )
        if (
            signal.suggested_event_type != "FLIGHT_ARRIVAL_DELAY"
            or signal.observed_flight is None
            or signal.old_arrival is None
            or signal.new_arrival is None
        ):
            raise SignalRejected("signal lacks the exact flight-delay facts required for recovery")
        item = next(
            (
                candidate
                for candidate in trip.items
                if candidate.item_id == watchpoint.item_id and candidate.type == ItemType.FLIGHT
            ),
            None,
        )
        if item is None or item.external_id != signal.observed_flight:
            raise SignalRejected("signal flight does not match the protected itinerary item")
        if item.end_at != signal.old_arrival or signal.new_arrival <= signal.old_arrival:
            raise SignalRejected("signal arrival times do not prove a new delay")
        # Delivery metadata must not change the deterministic recovery event id.
        fingerprint = grounded_signal_hash(signal)
        return DisruptionEvent(
            event_id=f"grounded-{fingerprint[:40]}",
            trip_id=trip.trip_id,
            type="FLIGHT_ARRIVAL_DELAY",
            flight=item.external_id,
            old_arrival=signal.old_arrival,
            new_arrival=signal.new_arrival,
            context={
                "source_url": signal.source_url,
                "source_title": signal.source_title,
                "trust": signal.trust.value,
                "grounded_signal_fingerprint": fingerprint,
                **(
                    {"airline_fault": signal.airline_fault}
                    if signal.airline_fault is not None
                    else {}
                ),
            },
        )
