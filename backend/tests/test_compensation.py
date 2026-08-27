from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.demo_data import build_demo_trip, build_owned_demo_trip
from app.models.compensation import (
    DisruptionCategory,
    RegulationJurisdiction,
)
from app.models.domain import DisruptionEvent, Incident
from app.models.enums import IncidentStatus
from app.models.money import Money
from app.services.compensation import (
    PassengerCompensationService,
    calculate_haversine_distance,
    extract_airline_code,
)
from app.services.memory import InMemoryIncidentRepository
from app.services.telegram_demo import TelegramDemoService
from app.workflows.recovery import RecoveryWorkflow


def test_haversine_distance_calculations() -> None:
    waw_muc = calculate_haversine_distance("WAW", "MUC")
    assert 740 <= waw_muc <= 790

    waw_lis = calculate_haversine_distance("WAW", "LIS")
    assert 2700 <= waw_lis <= 2800

    fra_jfk = calculate_haversine_distance("FRA", "JFK")
    assert 6100 <= fra_jfk <= 6300


def test_airline_code_extractor() -> None:
    assert extract_airline_code("LO351") == "LO"
    assert extract_airline_code("BA 123") == "BA"
    assert extract_airline_code("LH400") == "LH"
    assert extract_airline_code("U2890") == "U2"


def test_eu261_tier1_short_haul_delay() -> None:
    # WAW -> MUC (~760km) with 200 min delay (>3h) on LOT Polish Airlines
    assessment = PassengerCompensationService.assess_flight_disruption(
        flight_number="LO351",
        origin="WAW",
        destination="MUC",
        delay_minutes=200,
        disruption_category=DisruptionCategory.FLIGHT_DELAY,
    )
    assert assessment.eligible is True
    assert assessment.jurisdiction == RegulationJurisdiction.EU261
    assert assessment.amount == Money(currency="EUR", minor_units=25_000)
    assert assessment.distance_km <= 1500
    assert assessment.claim_ready is True
    assert "Regulation (EC) No 261/2004, Article 7" in assessment.legal_citations


def test_eu261_tier2_medium_haul_delay() -> None:
    # WAW -> LIS (~2750km) with 195 min delay on TAP Air Portugal
    assessment = PassengerCompensationService.assess_flight_disruption(
        flight_number="TP123",
        origin="WAW",
        destination="LIS",
        delay_minutes=195,
        disruption_category=DisruptionCategory.FLIGHT_DELAY,
    )
    assert assessment.eligible is True
    assert assessment.jurisdiction == RegulationJurisdiction.EU261
    assert assessment.amount == Money(currency="EUR", minor_units=40_000)
    assert 1500 < assessment.distance_km <= 3500


def test_eu261_tier3_long_haul_delay() -> None:
    # FRA -> JFK (~6200km) with 240 min delay on Lufthansa
    assessment = PassengerCompensationService.assess_flight_disruption(
        flight_number="LH400",
        origin="FRA",
        destination="JFK",
        delay_minutes=240,
        disruption_category=DisruptionCategory.FLIGHT_DELAY,
    )
    assert assessment.eligible is True
    assert assessment.jurisdiction == RegulationJurisdiction.EU261
    assert assessment.amount == Money(currency="EUR", minor_units=60_000)
    assert assessment.distance_km > 3500


def test_uk261_delay() -> None:
    # LHR -> EDI with 190 min delay on British Airways
    assessment = PassengerCompensationService.assess_flight_disruption(
        flight_number="BA1450",
        origin="LHR",
        destination="EDI",
        delay_minutes=190,
        disruption_category=DisruptionCategory.FLIGHT_DELAY,
    )
    assert assessment.eligible is True
    assert assessment.jurisdiction == RegulationJurisdiction.UK261
    assert assessment.amount == Money(currency="GBP", minor_units=22_000)


def test_cancellation_reason_does_not_claim_a_delay_threshold() -> None:
    assessment = PassengerCompensationService.assess_flight_disruption(
        flight_number="LO351",
        origin="WAW",
        destination="MUC",
        delay_minutes=0,
        disruption_category=DisruptionCategory.FLIGHT_CANCELLATION,
    )
    assert assessment.eligible is True
    assert "3-hour statutory threshold" not in " ".join(assessment.reasons)
    assert "cancellation" in " ".join(assessment.reasons).lower()


def test_uk261_claim_letter_uses_uk_legal_reference() -> None:
    scheduled = datetime(2026, 8, 20, 18, 0, tzinfo=UTC)
    actual = datetime(2026, 8, 20, 21, 30, tzinfo=UTC)
    assessment = PassengerCompensationService.assess_flight_disruption(
        flight_number="BA1450",
        origin="LHR",
        destination="EDI",
        delay_minutes=210,
    )
    letter = PassengerCompensationService.generate_claim_letter(
        incident_id="uk-claim-123",
        passenger_name="Alex Traveler",
        flight_number="BA1450",
        origin="LHR",
        destination="EDI",
        scheduled_arrival=scheduled,
        actual_arrival=actual,
        assessment=assessment,
    )
    assert "UK261" in letter.body_en
    assert "Regulation (EC) No 261/2004" not in letter.body_en
    assert "UK261" in letter.body_ru


def test_cancellation_always_eligible() -> None:
    # Flight cancelled (even with 0 delay minutes specified)
    assessment = PassengerCompensationService.assess_flight_disruption(
        flight_number="LO351",
        origin="WAW",
        destination="MUC",
        delay_minutes=0,
        disruption_category=DisruptionCategory.FLIGHT_CANCELLATION,
    )
    assert assessment.eligible is True
    assert assessment.amount == Money(currency="EUR", minor_units=25_000)


def test_cancellation_claim_letter_describes_cancellation_not_delay() -> None:
    assessment = PassengerCompensationService.assess_flight_disruption(
        flight_number="LO351",
        origin="WAW",
        destination="MUC",
        delay_minutes=0,
        disruption_category=DisruptionCategory.FLIGHT_CANCELLATION,
    )
    letter = PassengerCompensationService.generate_claim_letter(
        incident_id="inc-cancel-123",
        passenger_name="Alex Traveler",
        flight_number="LO351",
        origin="WAW",
        destination="MUC",
        scheduled_arrival=datetime(2026, 8, 20, 18, 0, tzinfo=UTC),
        actual_arrival=datetime(2026, 8, 20, 18, 0, tzinfo=UTC),
        assessment=assessment,
    )
    assert "flight was cancelled" in letter.body_en
    assert "arrived 0 minutes late" not in letter.body_en


def test_sub_threshold_delay_not_eligible() -> None:
    # Delay of 105 minutes is below 180-minute statutory threshold
    assessment = PassengerCompensationService.assess_flight_disruption(
        flight_number="LO351",
        origin="WAW",
        destination="MUC",
        delay_minutes=105,
        disruption_category=DisruptionCategory.FLIGHT_DELAY,
    )
    assert assessment.eligible is False
    assert assessment.amount is None
    assert assessment.claim_ready is False


def test_extraordinary_circumstances_hold_eu_claim_for_review() -> None:
    assessment = PassengerCompensationService.assess_flight_disruption(
        flight_number="LO351",
        origin="WAW",
        destination="MUC",
        delay_minutes=240,
        extraordinary_circumstances=True,
    )
    assert assessment.jurisdiction == RegulationJurisdiction.EU261
    assert assessment.eligible is False
    assert assessment.claim_ready is False
    assert assessment.amount is None
    assert "extraordinary" in " ".join(assessment.reasons).lower()


def test_us_dot_does_not_invent_fixed_cash_compensation() -> None:
    assessment = PassengerCompensationService.assess_flight_disruption(
        flight_number="AA100",
        origin="JFK",
        destination="LAX",
        delay_minutes=240,
    )
    assert assessment.jurisdiction == RegulationJurisdiction.US_DOT
    assert assessment.eligible is False
    assert assessment.amount is None
    assert assessment.claim_ready is True
    assert "fixed cash" in " ".join(assessment.reasons).lower()


def test_claim_letter_generation() -> None:
    scheduled = datetime(2026, 8, 20, 18, 0, tzinfo=UTC)
    actual = datetime(2026, 8, 20, 21, 30, tzinfo=UTC)  # 210 min delay

    letter = PassengerCompensationService.generate_claim_letter(
        incident_id="inc-test-12345",
        passenger_name="Alex Traveler",
        flight_number="LO351",
        origin="WAW",
        destination="LIS",
        scheduled_arrival=scheduled,
        actual_arrival=actual,
        booking_reference="PNR-XYZ-99",
    )

    assert letter.claim_id == "CLM-INC-TEST-LO351"
    assert letter.passenger_name == "Alex Traveler"
    assert letter.compensation_amount == Money(currency="EUR", minor_units=40_000)
    assert letter.delay_minutes == 210
    assert "Regulation (EC) No 261/2004" in letter.legal_basis
    assert "Alex Traveler" in letter.body_en
    assert "PNR-XYZ-99" in letter.body_en
    assert "€400.00" in letter.body_en
    assert "14" in letter.body_en  # 14 days deadline
    assert "Алекс" not in letter.body_en  # English body
    assert "Alex Traveler" in letter.body_ru
    assert "€400.00" in letter.body_ru
    assert not any("А" <= character <= "я" for character in letter.subject_ru + letter.body_ru)


def test_claim_letter_keeps_sources_and_is_explicitly_reviewable() -> None:
    scheduled = datetime(2026, 8, 20, 18, 0, tzinfo=UTC)
    actual = datetime(2026, 8, 20, 21, 30, tzinfo=UTC)
    letter = PassengerCompensationService.generate_claim_letter(
        incident_id="inc-source-123",
        passenger_name="Alex Traveler",
        flight_number="LO351",
        origin="WAW",
        destination="LIS",
        scheduled_arrival=scheduled,
        actual_arrival=actual,
        source_links=["https://airline.example/status/LO351"],
        evidence_timestamps=[actual],
    )
    assert letter.review_required is True
    assert letter.source_links == ["https://airline.example/status/LO351"]
    assert letter.evidence_timestamps == [actual]
    assert "airline.example/status/LO351" in letter.body_en


def test_ineligible_assessment_cannot_generate_claim() -> None:
    assessment = PassengerCompensationService.assess_flight_disruption(
        flight_number="LO351", origin="WAW", destination="MUC", delay_minutes=30
    )
    with pytest.raises(ValueError, match="not ready"):
        PassengerCompensationService.generate_claim_letter(
            incident_id="inc-not-ready",
            passenger_name="Alex",
            flight_number="LO351",
            origin="WAW",
            destination="MUC",
            scheduled_arrival=datetime(2026, 8, 20, 18, 0, tzinfo=UTC),
            actual_arrival=datetime(2026, 8, 20, 18, 30, tzinfo=UTC),
            assessment=assessment,
        )


async def test_assess_incident_e2e() -> None:
    trip = build_demo_trip()
    event = DisruptionEvent(
        event_id="test-delay-event",
        trip_id="demo-trip-001",
        type="flight_delay",
        flight="LO351",
        old_arrival=datetime(2026, 8, 20, 18, 0, tzinfo=UTC),
        new_arrival=datetime(2026, 8, 20, 21, 15, tzinfo=UTC),  # 195 min delay
        context={"airline_fault": True},
    )
    incident = Incident(
        incident_id="incident-comp-001",
        trip_id="demo-trip-001",
        external_event_id="ext-delay-001",
        correlation_id="corr-comp-001",
        trigger=event,
        status=IncidentStatus.WAITING_APPROVAL,
    )

    assessment, claim_letter = PassengerCompensationService.assess_incident(
        incident=incident, trip=trip, passenger_name="John Doe"
    )

    assert assessment.eligible is True
    assert assessment.amount == Money(currency="EUR", minor_units=25_000)
    assert claim_letter is not None
    assert claim_letter.passenger_name == "John Doe"
    assert claim_letter.flight_number == "LO351"


async def test_assess_incident_uses_pnr_and_verified_source_context() -> None:
    trip = build_demo_trip().model_copy(
        update={
            "items": [
                build_demo_trip().items[0].model_copy(update={"booking_reference": "ABC123"}),
                *build_demo_trip().items[1:],
            ]
        }
    )
    event = DisruptionEvent(
        event_id="test-context-event",
        trip_id=trip.trip_id,
        type="flight_delay",
        flight="LO351",
        old_arrival=datetime(2026, 8, 20, 18, 0, tzinfo=UTC),
        new_arrival=datetime(2026, 8, 20, 21, 15, tzinfo=UTC),
        context={
            "source_links": ["https://status.example/LO351"],
            "source_timestamps": ["2026-08-20T21:16:00Z"],
            "airline_fault": True,
        },
    )
    incident = Incident(
        incident_id="incident-context-001",
        trip_id=trip.trip_id,
        external_event_id=event.event_id,
        correlation_id="corr-context-001",
        trigger=event,
        status=IncidentStatus.WAITING_APPROVAL,
    )

    assessment, claim_letter = PassengerCompensationService.assess_incident(
        incident=incident, trip=trip, passenger_name="John Doe"
    )

    assert assessment.source_links == ["https://status.example/LO351"]
    assert assessment.source_timestamps == [datetime(2026, 8, 20, 21, 16, tzinfo=UTC)]
    assert claim_letter is not None
    assert claim_letter.booking_reference == "ABC123"
    assert "ABC123" in claim_letter.body_en
    assert "2026-08-20T21:16:00+00:00" in claim_letter.body_en


async def test_assess_incident_holds_claim_when_airline_fault_is_unverified() -> None:
    trip = build_demo_trip()
    event = DisruptionEvent(
        event_id="cause-missing-event",
        trip_id=trip.trip_id,
        type="flight_delay",
        flight="LO351",
        old_arrival=datetime(2026, 8, 20, 18, 0, tzinfo=UTC),
        new_arrival=datetime(2026, 8, 20, 21, 30, tzinfo=UTC),
        context={"source_links": ["https://airline.example/status/LO351"]},
    )
    incident = Incident(
        incident_id="cause-missing-incident",
        trip_id=trip.trip_id,
        external_event_id=event.event_id,
        correlation_id="cause-missing-correlation",
        trigger=event,
        status=IncidentStatus.WAITING_APPROVAL,
    )

    assessment, claim_letter = PassengerCompensationService.assess_incident(
        incident=incident, trip=trip
    )

    assert assessment.eligible is False
    assert assessment.claim_ready is False
    assert claim_letter is None
    assert "not verified" in " ".join(assessment.reasons).lower()


async def test_telegram_demo_claim_view() -> None:
    from app.models.enums import OnboardingStep
    from app.models.telegram import TravelerProfile

    now = datetime(2026, 8, 20, tzinfo=UTC)
    repository = InMemoryIncidentRepository()
    traveler = TravelerProfile(
        user_id="telegram:user-claim-1",
        telegram_user_id="user-claim-1",
        telegram_chat_id="chat-claim-1",
        onboarding_step=OnboardingStep.COMPLETE,
        created_at=now,
        updated_at=now,
    )
    await repository.save_traveler(traveler)
    trip_id = f"telegram-demo-trip:{traveler.telegram_user_id}"
    await repository.seed_trip(
        build_owned_demo_trip(owner_user_id=traveler.user_id, trip_id=trip_id)
    )

    event = DisruptionEvent(
        event_id="telegram-demo:user-claim-1:up1",
        trip_id=trip_id,
        type="flight_delay",
        flight="LO351",
        old_arrival=datetime(2026, 8, 20, 18, 0, tzinfo=UTC),
        new_arrival=datetime(2026, 8, 20, 21, 30, tzinfo=UTC),  # 210 min delay -> eligible
        context={"airline_fault": True},
    )
    incident = Incident(
        incident_id="inc-demo-claim",
        trip_id=trip_id,
        external_event_id=event.event_id,
        correlation_id="corr-demo-claim",
        trigger=event,
        status=IncidentStatus.RECOVERED,
    )
    await repository.claim_event(
        event=event,
        incident=incident,
        worker_id="w1",
        lease_expires_at=datetime(2026, 8, 21, tzinfo=UTC),
    )

    demo_service = TelegramDemoService(repository, RecoveryWorkflow(repository))
    view = await demo_service.handle(
        telegram_user_id=traveler.telegram_user_id,
        telegram_chat_id=traveler.telegram_chat_id,
        callback_data="demo:claim:inc-demo-claim",
        update_id="up2",
        now=now,
    )

    assert "EU261 STATUTORY COMPENSATION" in view.text
    assert "CLAIM ELIGIBLE" in view.text
    assert "€250.00" in view.text
    assert "LO351" in view.text


async def test_compensation_api_routes() -> None:
    from app.config import Settings
    from app.main import AppContainer, create_app
    from app.services.memory import LocalEventPublisher
    from app.workflows.impact_analysis import ImpactAnalysisWorkflow
    from httpx import ASGITransport, AsyncClient

    from tests.helpers import ValidInterpreter

    settings = Settings(pubsub_transport="local")
    repository = InMemoryIncidentRepository()
    await repository.seed_trip(build_demo_trip())

    event = DisruptionEvent(
        event_id="api-claim-event",
        trip_id="demo-trip-001",
        type="flight_delay",
        flight="LO351",
        old_arrival=datetime(2026, 8, 20, 18, 0, tzinfo=UTC),
        new_arrival=datetime(2026, 8, 20, 21, 30, tzinfo=UTC),  # 210 min delay
        context={"airline_fault": True},
    )
    incident = Incident(
        incident_id="api-claim-incident",
        trip_id="demo-trip-001",
        external_event_id=event.event_id,
        correlation_id="corr-api-claim",
        trigger=event,
        status=IncidentStatus.RECOVERED,
    )
    await repository.claim_event(
        event=event,
        incident=incident,
        worker_id="w1",
        lease_expires_at=datetime(2026, 8, 21, tzinfo=UTC),
    )

    container = AppContainer(
        settings,
        repository,
        LocalEventPublisher(),
        ImpactAnalysisWorkflow(repository, ValidInterpreter()),
    )
    app = create_app(settings, container=container)
    app.state.container = container

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        comp_res = await client.get("/internal/incidents/api-claim-incident/compensation")
        assert comp_res.status_code == 200
        comp_data = comp_res.json()
        assert comp_data["eligible"] is True
        assert comp_data["jurisdiction"] == "EU261"
        assert comp_data["amount"]["minor_units"] == 25000

        letter_res = await client.get(
            "/internal/incidents/api-claim-incident/claim-letter?passenger_name=Alex"
        )
        assert letter_res.status_code == 200
        letter_data = letter_res.json()
        assert letter_data["passenger_name"] == "Alex"
        assert "Regulation (EC) No 261/2004" in letter_data["body_en"]
        assert "€250.00" in letter_data["body_en"]
