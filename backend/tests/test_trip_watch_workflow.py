from datetime import UTC, datetime

import pytest
from app.demo_data import build_demo_trip
from app.models.domain import TravelItem, Trip
from app.models.enums import ItemType
from app.models.telegram import TelegramMessageReceipt, TelegramView, TravelerProfile
from app.models.watch import GroundedTravelSignal, SourceTrust, TripWatchpoint, WatchpointKind
from app.services.memory import InMemoryIncidentRepository, LocalEventPublisher
from app.services.trip_watch_notifications import TripWatchSignalNotifier
from app.services.trip_watch_workflow import TripWatchWorkflow, WatchpointConfigurationError


class WatchGateway:
    def __init__(self) -> None:
        self.sent: list[tuple[str, TelegramView]] = []

    async def send_message(self, *, chat_id: str, view: TelegramView) -> TelegramMessageReceipt:
        self.sent.append((chat_id, view))
        return TelegramMessageReceipt(chat_id=chat_id, message_id=len(self.sent))

    async def edit_message(
        self, *, chat_id: str, message_id: int, view: TelegramView
    ) -> TelegramMessageReceipt:
        return TelegramMessageReceipt(chat_id=chat_id, message_id=message_id)

    async def answer_callback_query(
        self, *, callback_query_id: str, text: str | None = None, show_alert: bool = False
    ) -> None:
        return None


class Grounder:
    async def observe(self, watchpoint: TripWatchpoint) -> GroundedTravelSignal:
        return GroundedTravelSignal(
            watchpoint_id=watchpoint.watchpoint_id,
            summary="Airport strike confirmed.",
            source_url="https://airport.example/alert",
            source_title="Airport alert",
            trust=SourceTrust.OFFICIAL,
            observed_at=datetime(2026, 8, 20, tzinfo=UTC),
            affects_trip=True,
            suggested_event_type="AIRPORT_DISRUPTION",
        )


async def test_watch_workflow_persists_each_grounded_signal_once() -> None:
    repository = InMemoryIncidentRepository()
    await repository.seed_trip(build_demo_trip().model_copy(update={"trip_id": "trip"}))
    watchpoint = TripWatchpoint(
        watchpoint_id="watch:trip:flight:airport",
        trip_id="trip",
        item_id="flight",
        kind=WatchpointKind.AIRPORT_DISRUPTION,
        query="MUC airport strike",
        due_at=datetime(2026, 8, 20, tzinfo=UTC),
    )
    await repository.put_watchpoint(watchpoint)
    workflow = TripWatchWorkflow(
        repository, Grounder(), clock=lambda: datetime(2026, 8, 20, tzinfo=UTC)
    )

    first = await workflow.run_watchpoint(watchpoint)
    duplicate = await workflow.run_watchpoint(watchpoint)

    assert first is not None
    assert duplicate is None
    scheduled = (await repository.list_watchpoints("trip"))[0]
    assert scheduled.last_checked_at == datetime(2026, 8, 20, tzinfo=UTC)
    assert scheduled.due_at == datetime(2026, 8, 20, 0, 30, tzinfo=UTC)


async def test_repeated_polling_time_does_not_create_a_new_grounded_event() -> None:
    repository = InMemoryIncidentRepository()
    await repository.seed_trip(build_demo_trip().model_copy(update={"trip_id": "trip"}))
    watchpoint = TripWatchpoint(
        watchpoint_id="watch:trip:flight:status",
        trip_id="trip",
        item_id="flight",
        kind=WatchpointKind.FLIGHT_STATUS,
        query="LO351 flight status",
        due_at=datetime(2026, 8, 20, tzinfo=UTC),
    )
    await repository.put_watchpoint(watchpoint)

    class PollingGrounder:
        def __init__(self) -> None:
            self.observed_at = datetime(2026, 8, 20, 12, tzinfo=UTC)

        async def observe(self, watchpoint: TripWatchpoint) -> GroundedTravelSignal:
            current = GroundedTravelSignal(
                watchpoint_id=watchpoint.watchpoint_id,
                summary="Official delay",
                source_url="https://airline.example/status",
                source_title="Airline",
                trust=SourceTrust.OFFICIAL,
                observed_at=self.observed_at,
                affects_trip=True,
                suggested_event_type="FLIGHT_ARRIVAL_DELAY",
            )
            self.observed_at = self.observed_at.replace(minute=self.observed_at.minute + 1)
            return current

    grounder = PollingGrounder()
    workflow = TripWatchWorkflow(
        repository,
        grounder,
        clock=lambda: datetime(2026, 8, 20, 12, tzinfo=UTC),
    )

    first = await workflow.run_watchpoint(watchpoint)
    current = (await repository.list_watchpoints("trip"))[0]
    second = await workflow.run_watchpoint(
        current.model_copy(update={"due_at": datetime(2026, 8, 20, 12, tzinfo=UTC)})
    )

    assert first is not None
    assert second is None
    assert len(await repository.list_unpublished_grounded_signals(limit=10)) == 1


async def test_official_exact_delay_is_published_to_existing_recovery_topic() -> None:
    repository = InMemoryIncidentRepository()
    publisher = LocalEventPublisher()
    trip = Trip(
        trip_id="trip",
        origin="WAW",
        destination="MUC",
        starts_at=datetime(2026, 8, 20, 13, tzinfo=UTC),
        ends_at=datetime(2026, 8, 20, 16, tzinfo=UTC),
        items=[
            TravelItem(
                item_id="flight",
                trip_id="trip",
                type=ItemType.FLIGHT,
                provider="LOT",
                external_id="LO351",
                start_at=datetime(2026, 8, 20, 13, tzinfo=UTC),
                end_at=datetime(2026, 8, 20, 16, tzinfo=UTC),
            )
        ],
    )
    await repository.seed_trip(trip)
    watchpoint = TripWatchpoint(
        watchpoint_id="watch:trip:flight:flight_status",
        trip_id="trip",
        item_id="flight",
        kind=WatchpointKind.FLIGHT_STATUS,
        query="LO351 flight status",
        trusted_domains=["airline.example"],
        due_at=datetime(2026, 8, 20, 13, tzinfo=UTC),
    )
    signal = GroundedTravelSignal(
        watchpoint_id=watchpoint.watchpoint_id,
        summary="Official delay",
        source_url="https://airline.example/status",
        source_title="Airline",
        trust=SourceTrust.OFFICIAL,
        observed_at=datetime(2026, 8, 20, 12, tzinfo=UTC),
        affects_trip=True,
        suggested_event_type="FLIGHT_ARRIVAL_DELAY",
        observed_flight="LO351",
        old_arrival=datetime(2026, 8, 20, 16, tzinfo=UTC),
        new_arrival=datetime(2026, 8, 20, 17, 45, tzinfo=UTC),
    )
    workflow = TripWatchWorkflow(repository, Grounder(), publisher)

    message_id = await workflow.publish_recovery_event(watchpoint=watchpoint, signal=signal)

    assert message_id == "local-1"
    assert publisher.events[0].new_arrival == signal.new_arrival


async def test_grounded_signal_is_replayed_after_publish_outage() -> None:
    repository = InMemoryIncidentRepository()
    publisher = LocalEventPublisher()
    trip = build_demo_trip()
    await repository.seed_trip(trip)
    watchpoint = TripWatchpoint(
        watchpoint_id="watch:demo-trip-001:flight-lo351:flight_status",
        trip_id=trip.trip_id,
        item_id="flight-lo351",
        kind=WatchpointKind.FLIGHT_STATUS,
        query="LO351 flight status",
        trusted_domains=["airline.example"],
        due_at=datetime(2026, 8, 20, 12, tzinfo=UTC),
    )
    await repository.put_watchpoint(watchpoint)

    class FlightGrounder:
        async def observe(self, watchpoint: TripWatchpoint) -> GroundedTravelSignal:
            return GroundedTravelSignal(
                watchpoint_id=watchpoint.watchpoint_id,
                summary="Official delay",
                source_url="https://airline.example/status",
                source_title="Airline",
                trust=SourceTrust.OFFICIAL,
                observed_at=datetime(2026, 8, 20, 12, tzinfo=UTC),
                affects_trip=True,
                suggested_event_type="FLIGHT_ARRIVAL_DELAY",
                observed_flight="LO351",
                old_arrival=datetime(2026, 8, 20, 18, tzinfo=UTC),
                new_arrival=datetime(2026, 8, 20, 19, 45, tzinfo=UTC),
            )

    workflow = TripWatchWorkflow(
        repository,
        FlightGrounder(),
        publisher,
        clock=lambda: datetime(2026, 8, 20, 12, tzinfo=UTC),
    )

    signal = await workflow.run_watchpoint(watchpoint)
    assert signal is not None
    assert await repository.list_unpublished_grounded_signals(limit=10)
    assert await workflow.publish_pending_events(limit=10) == 1
    assert await repository.list_unpublished_grounded_signals(limit=10) == []
    # The operational published_at field must not change the event identity.
    assert publisher.events[0].event_id.startswith("grounded-")


async def test_direct_tick_acknowledges_non_recovery_signal() -> None:
    repository = InMemoryIncidentRepository()
    publisher = LocalEventPublisher()
    trip = build_demo_trip()
    await repository.seed_trip(trip)
    watchpoint = TripWatchpoint(
        watchpoint_id="watch:demo-trip-001:flight-lo351:airport_disruption",
        trip_id=trip.trip_id,
        item_id="flight-lo351",
        kind=WatchpointKind.AIRPORT_DISRUPTION,
        query="WAW airport disruption",
        due_at=datetime(2026, 8, 20, 12, tzinfo=UTC),
    )
    signal = GroundedTravelSignal(
        watchpoint_id=watchpoint.watchpoint_id,
        summary="Public airport notice",
        source_url="https://airport.example/alert",
        source_title="Airport alert",
        trust=SourceTrust.PUBLIC_SIGNAL,
        observed_at=datetime(2026, 8, 20, 12, tzinfo=UTC),
        affects_trip=True,
        suggested_event_type="AIRPORT_DISRUPTION",
    )
    await repository.put_watchpoint(watchpoint)
    await repository.put_grounded_signal(signal)
    workflow = TripWatchWorkflow(repository, Grounder(), publisher)

    assert await workflow.publish_recovery_event(watchpoint=watchpoint, signal=signal) is None
    # An affected fact cannot be acknowledged until its Telegram notification
    # is durably delivered; a later tick can retry once the gateway is present.
    assert await repository.list_unpublished_grounded_signals(limit=10)


async def test_non_recovery_signal_is_proactively_delivered_with_source_and_is_idempotent() -> None:
    repository = InMemoryIncidentRepository()
    gateway = WatchGateway()
    trip = build_demo_trip().model_copy(update={"owner_user_id": "telegram:101"})
    await repository.seed_trip(trip)
    await repository.save_traveler(
        TravelerProfile(
            user_id="telegram:101",
            telegram_user_id="101",
            telegram_chat_id="202",
            created_at=datetime(2026, 8, 20, tzinfo=UTC),
            updated_at=datetime(2026, 8, 20, tzinfo=UTC),
        )
    )
    watchpoint = TripWatchpoint(
        watchpoint_id="watch:demo-trip-001:flight-lo351:airport_disruption",
        trip_id=trip.trip_id,
        item_id="flight-lo351",
        kind=WatchpointKind.AIRPORT_DISRUPTION,
        query="WAW airport disruption",
        due_at=datetime(2026, 8, 20, 12, tzinfo=UTC),
    )
    signal = GroundedTravelSignal(
        watchpoint_id=watchpoint.watchpoint_id,
        summary="Airport closure notice affects your connection.",
        source_url="https://airport.example/alert",
        source_title="Airport operational notice",
        trust=SourceTrust.PUBLIC_SIGNAL,
        observed_at=datetime(2026, 8, 20, 12, tzinfo=UTC),
        affects_trip=True,
        suggested_event_type="AIRPORT_DISRUPTION",
    )
    await repository.put_watchpoint(watchpoint)
    await repository.put_grounded_signal(signal)
    workflow = TripWatchWorkflow(repository, Grounder(), LocalEventPublisher())
    notifier = TripWatchSignalNotifier(repository, gateway)

    assert await workflow.publish_pending_events(limit=10, notifier=notifier) == 1
    assert len(gateway.sent) == 1
    chat_id, view = gateway.sent[0]
    assert chat_id == "202"
    assert "Airport closure notice" in view.text
    assert "itinerary is unchanged" in view.text
    assert view.buttons[0].url == signal.source_url

    # Replaying the pending flush cannot send a second Telegram message.
    assert await workflow.publish_pending_events(limit=10, notifier=notifier) == 0
    assert len(gateway.sent) == 1


async def test_watch_provider_failure_is_persisted_as_bounded_degraded_state() -> None:
    repository = InMemoryIncidentRepository()
    await repository.seed_trip(build_demo_trip().model_copy(update={"trip_id": "trip"}))
    watchpoint = TripWatchpoint(
        watchpoint_id="watch:trip:weather",
        trip_id="trip",
        kind=WatchpointKind.WEATHER_IMPACT,
        query="Lisbon weather warning travel disruption",
        due_at=datetime(2026, 8, 20, tzinfo=UTC),
    )
    await repository.put_watchpoint(watchpoint)

    class FailingGrounder:
        async def observe(self, watchpoint: TripWatchpoint) -> GroundedTravelSignal:
            raise RuntimeError("provider payload must not be persisted")

    workflow = TripWatchWorkflow(
        repository,
        FailingGrounder(),
        clock=lambda: datetime(2026, 8, 20, 12, tzinfo=UTC),
    )
    with pytest.raises(RuntimeError):
        await workflow.run_watchpoint(watchpoint)
    failed = await repository.get_watchpoint(watchpoint.watchpoint_id)
    assert failed is not None
    assert failed.last_error_code == "PROVIDER_ERROR"
    assert "provider payload" not in failed.model_dump_json()


async def test_named_watch_provider_failure_preserves_bounded_code() -> None:
    from app.agents.google_search_watch import WatchProviderError

    repository = InMemoryIncidentRepository()
    await repository.seed_trip(build_demo_trip().model_copy(update={"trip_id": "trip"}))
    watchpoint = TripWatchpoint(
        watchpoint_id="watch:trip:provider",
        trip_id="trip",
        kind=WatchpointKind.WEATHER_IMPACT,
        query="Lisbon weather warning travel disruption",
        due_at=datetime(2026, 8, 20, tzinfo=UTC),
    )
    await repository.put_watchpoint(watchpoint)

    class NamedFailingGrounder:
        async def observe(self, watchpoint: TripWatchpoint) -> GroundedTravelSignal:
            raise WatchProviderError("SEARCH_PROVIDER_TIMEOUT")

    workflow = TripWatchWorkflow(
        repository,
        NamedFailingGrounder(),
        clock=lambda: datetime(2026, 8, 20, 12, tzinfo=UTC),
    )
    with pytest.raises(WatchProviderError, match="SEARCH_PROVIDER_TIMEOUT"):
        await workflow.run_watchpoint(watchpoint)
    failed = await repository.get_watchpoint(watchpoint.watchpoint_id)
    assert failed is not None
    assert failed.last_error_code == "SEARCH_PROVIDER_TIMEOUT"


async def test_orphaned_watchpoint_is_persisted_as_trip_not_found() -> None:
    repository = InMemoryIncidentRepository()
    watchpoint = TripWatchpoint(
        watchpoint_id="watch:deleted-trip:weather",
        trip_id="deleted-trip",
        kind=WatchpointKind.WEATHER_IMPACT,
        query="Munich weather warning travel disruption",
        due_at=datetime(2026, 8, 20, tzinfo=UTC),
    )
    await repository.put_watchpoint(watchpoint)
    workflow = TripWatchWorkflow(
        repository,
        Grounder(),
        clock=lambda: datetime(2026, 8, 20, 12, tzinfo=UTC),
    )

    with pytest.raises(WatchpointConfigurationError, match="no longer available"):
        await workflow.run_watchpoint(watchpoint)

    failed = await repository.get_watchpoint(watchpoint.watchpoint_id)
    assert failed is not None
    assert failed.last_error_code == "TRIP_NOT_FOUND"
    assert failed.last_error_at == datetime(2026, 8, 20, 12, tzinfo=UTC)
