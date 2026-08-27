from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol

from app.models.watch import GroundedTravelSignal, TripWatchpoint
from app.services.canonical_hash import grounded_signal_hash
from app.services.ports import EventPublisher, IncidentRepository
from app.services.signal_validation import GroundedSignalValidator, SignalRejected


class WatchGrounder(Protocol):
    async def observe(self, watchpoint: TripWatchpoint) -> GroundedTravelSignal | None: ...


class WatchSignalNotifier(Protocol):
    async def notify(
        self,
        *,
        trip_id: str,
        watchpoint: TripWatchpoint,
        signal: GroundedTravelSignal,
        now: datetime,
    ) -> bool: ...


class WatchpointConfigurationError(RuntimeError):
    """A durable watchpoint cannot run against its configured trip."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class TripWatchWorkflow:
    """Runs a watchpoint and records only sourced, deduplicated signals."""

    def __init__(
        self,
        repository: IncidentRepository,
        grounder: WatchGrounder,
        publisher: EventPublisher | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._grounder = grounder
        self._publisher = publisher
        self._validator = GroundedSignalValidator()
        self._clock = clock or (lambda: datetime.now(UTC))

    async def run_watchpoint(self, watchpoint: TripWatchpoint) -> GroundedTravelSignal | None:
        checked_at = self._clock()
        scheduled = watchpoint.model_copy(
            update={
                "last_checked_at": checked_at,
                "due_at": checked_at + timedelta(minutes=watchpoint.check_interval_minutes),
                "last_error_at": None,
                "last_error_code": None,
            }
        )
        claimed = await self._repository.reschedule_watchpoint(
            scheduled,
            expected_due_at=watchpoint.due_at,
        )
        if not claimed:
            return None
        try:
            # A watchpoint is only meaningful while its trip exists.  Keep
            # this lookup inside the same retry boundary as the provider call
            # so a transient Firestore failure is also persisted as degraded.
            if await self._repository.get_trip(watchpoint.trip_id) is None:
                raise WatchpointConfigurationError(
                    "TRIP_NOT_FOUND",
                    "watchpoint references a trip that is no longer available",
                )
            signal = await self._grounder.observe(watchpoint)
        except Exception as error:
            # Persist only a bounded error class; provider details may contain
            # secrets or traveler data and must never reach the watchpoint record.
            provider_code = getattr(error, "code", None)
            await self._repository.reschedule_watchpoint(
                scheduled.model_copy(
                    update={
                        "last_error_at": checked_at,
                        "last_error_code": (
                            provider_code
                            if isinstance(provider_code, str)
                            and provider_code.isascii()
                            and provider_code.isupper()
                            and all(
                                character.isalnum() or character == "_"
                                for character in provider_code
                            )
                            and len(provider_code) <= 80
                            else "PROVIDER_ERROR"
                        ),
                    }
                ),
                expected_due_at=scheduled.due_at,
            )
            raise
        if signal is None or signal.watchpoint_id != watchpoint.watchpoint_id:
            return None
        if not signal.affects_trip or not signal.source_url.startswith("https://"):
            return None
        recorded = await self._repository.reschedule_watchpoint(
            scheduled.model_copy(update={"last_signal_at": checked_at}),
            expected_due_at=scheduled.due_at,
        )
        if not recorded:
            return None
        return signal if await self._repository.put_grounded_signal(signal) else None

    async def publish_pending_events(
        self, *, limit: int = 20, notifier: WatchSignalNotifier | None = None
    ) -> int:
        """Flush grounded facts recorded before Pub/Sub acknowledged them."""
        published = 0
        for signal in await self._repository.list_unpublished_grounded_signals(limit=limit):
            watchpoint = await self._repository.get_watchpoint(signal.watchpoint_id)
            if watchpoint is None:
                continue
            trip = await self._repository.get_trip(watchpoint.trip_id)
            if trip is None or self._publisher is None:
                continue
            try:
                event = self._validator.to_disruption(
                    trip=trip, watchpoint=watchpoint, signal=signal
                )
                await self._publisher.publish(event)
                if await self._repository.mark_grounded_signal_published(
                    signal=signal, published_at=self._clock()
                ):
                    published += 1
            except SignalRejected:
                # Affected-but-non-recovery facts still require a traveler
                # notification. Without a gateway, keep the outbox pending so
                # a later tick can deliver it instead of silently losing it.
                if notifier is None and signal.affects_trip:
                    continue
                delivered = (
                    await notifier.notify(
                        trip_id=trip.trip_id,
                        watchpoint=watchpoint,
                        signal=signal,
                        now=self._clock(),
                    )
                    if notifier is not None and signal.affects_trip
                    else True
                )
                if not delivered:
                    continue
                # Informational/non-recovery signals remain auditable but do not
                # belong in the recovery topic. Mark them handled only after the
                # Telegram delivery intent has been durably acknowledged.
                await self._repository.mark_grounded_signal_published(
                    signal=signal, published_at=self._clock()
                )
                published += 1 if notifier is not None and signal.affects_trip else 0
                continue
        return published

    async def publish_recovery_event(
        self,
        *,
        watchpoint: TripWatchpoint,
        signal: GroundedTravelSignal,
        notifier: WatchSignalNotifier | None = None,
    ) -> str | None:
        if self._publisher is None:
            return None
        trip = await self._repository.get_trip(watchpoint.trip_id)
        if trip is None:
            return None
        try:
            event = self._validator.to_disruption(trip=trip, watchpoint=watchpoint, signal=signal)
        except SignalRejected:
            # The fact has already been durably recorded by run_watchpoint. A
            # direct tick path must acknowledge informational/rejected facts too,
            # otherwise the same non-actionable signal is reconsidered forever.
            if notifier is None and signal.affects_trip:
                return None
            delivered = (
                await notifier.notify(
                    trip_id=watchpoint.trip_id,
                    watchpoint=watchpoint,
                    signal=signal,
                    now=self._clock(),
                )
                if notifier is not None and signal.affects_trip
                else True
            )
            if not delivered:
                return None
            await self._repository.mark_grounded_signal_published(
                signal=signal, published_at=self._clock()
            )
            return f"watch-{grounded_signal_hash(signal)[:32]}" if notifier else None
        message_id = await self._publisher.publish(event)
        await self._repository.mark_grounded_signal_published(
            signal=signal, published_at=self._clock()
        )
        return message_id
