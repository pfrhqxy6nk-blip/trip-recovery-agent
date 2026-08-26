from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from app.agents.itinerary_extractor import ItineraryExtractor

FIXTURES = Path(__file__).parents[2] / "demo" / "fixtures"


@pytest.mark.asyncio
async def test_beta_email_fixture_builds_the_demo_itinerary() -> None:
    request = await ItineraryExtractor().extract_from_media(
        (FIXTURES / "warsaw-munich-lisbon-booking-email.txt").read_bytes(),
        "text/plain",
        reference_time=datetime(2026, 9, 1, tzinfo=UTC),
    )
    assert [flight.flight_number for flight in request.flights] == ["LO351", "LH1790"]
    assert request.flights[0].booking_reference == "TWDEMO"
    assert request.hotel is not None


@pytest.mark.asyncio
async def test_beta_pdf_fixture_builds_the_demo_itinerary_without_vision() -> None:
    request = await ItineraryExtractor().extract_from_media(
        (FIXTURES / "warsaw-munich-lisbon-booking.pdf").read_bytes(),
        "application/pdf",
        reference_time=datetime(2026, 9, 1, tzinfo=UTC),
    )
    assert [flight.flight_number for flight in request.flights] == ["LO351", "LH1790"]
    assert request.flights[0].booking_reference == "TWDEMO"
    assert request.hotel is not None


@pytest.mark.asyncio
async def test_beta_pkpass_fixture_is_safe_and_parseable() -> None:
    request = await ItineraryExtractor().extract_from_media(
        (FIXTURES / "warsaw-munich-lisbon-demo.pkpass").read_bytes(),
        "application/vnd.apple.pkpass",
        reference_time=datetime(2026, 9, 1, tzinfo=UTC),
    )
    assert request.flights[0].flight_number == "LO351"
    assert request.flights[0].booking_reference == "TWDEMO"
    assert request.hotel is not None
