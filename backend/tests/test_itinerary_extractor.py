from __future__ import annotations

from datetime import UTC, datetime
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from app.agents.itinerary_extractor import ItineraryExtractor


@pytest.mark.asyncio
async def test_media_fallback_reads_caption_without_inventing_a_booking() -> None:
    extractor = ItineraryExtractor()
    request = await extractor.extract_from_media(
        b"not an OCR readable image",
        "image/png",
        caption=(
            "Boarding pass LO351 WAW MUC PNR ABC123; "
            "2026-08-20T15:00:00+00:00 2026-08-20T18:00:00+00:00"
        ),
        reference_time=datetime(2026, 8, 20, tzinfo=UTC),
    )
    assert request.flights[0].flight_number == "LO351"
    assert request.flights[0].booking_reference == "ABC123"


@pytest.mark.asyncio
async def test_media_fallback_reads_apple_wallet_pass_json() -> None:
    payload = (
        b'{"organizationName":"LOT","description":"Flight LO351 WAW MUC",'
        b'"serialNumber":"ABC123",'
        b'"departureDate":"2026-08-20T15:00:00+00:00",'
        b'"arrivalDate":"2026-08-20T18:00:00+00:00"}'
    )
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("pass.json", payload)
    extractor = ItineraryExtractor()
    request = await extractor.extract_from_media(
        buffer.getvalue(),
        "application/vnd.apple.pkpass",
        reference_time=datetime(2026, 8, 20, tzinfo=UTC),
    )
    assert request.flights[0].flight_number == "LO351"
    assert request.flights[0].booking_reference == "ABC123"


@pytest.mark.asyncio
async def test_media_fallback_rejects_unreadable_binary_instead_of_fabricating_flight() -> None:
    with pytest.raises(ValueError, match="no flight number"):
        await ItineraryExtractor().extract_from_media(
            b"opaque image bytes", "image/png", reference_time=datetime(2026, 8, 20, tzinfo=UTC)
        )


@pytest.mark.asyncio
async def test_media_fallback_rejects_booking_without_explicit_times() -> None:
    with pytest.raises(ValueError, match="explicit departure and arrival times"):
        await ItineraryExtractor().extract_from_media(
            b"opaque image bytes",
            "image/png",
            caption="Boarding pass LO351 WAW MUC PNR ABC123",
            reference_time=datetime(2026, 8, 20, tzinfo=UTC),
        )


@pytest.mark.asyncio
async def test_media_fallback_accepts_hotel_only_booking_with_explicit_stay_times() -> None:
    request = await ItineraryExtractor().extract_from_media(
        b"opaque screenshot bytes",
        "image/png",
        caption=(
            "Airbnb reservation Lisbon apartment; "
            "2026-08-20T15:00:00+00:00 2026-08-23T10:00:00+00:00"
        ),
        reference_time=datetime(2026, 8, 20, tzinfo=UTC),
    )
    assert request.flights == []
    assert request.hotel is not None
    assert request.hotel.name == "Lisbon apartment"


@pytest.mark.asyncio
async def test_media_fallback_rejects_oversized_upload() -> None:
    with pytest.raises(ValueError, match="12 MiB"):
        await ItineraryExtractor().extract_from_media(
            b"x" * (ItineraryExtractor.MAX_MEDIA_BYTES + 1),
            "application/pdf",
            reference_time=datetime(2026, 8, 20, tzinfo=UTC),
        )


@pytest.mark.asyncio
async def test_media_fallback_rejects_pkpass_compression_amplification() -> None:
    buffer = BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("pass.json", b"{" + b' "x": "a"' * 100_000 + b"}")
    with pytest.raises(ValueError, match="no flight number"):
        await ItineraryExtractor().extract_from_media(
            buffer.getvalue(),
            "application/vnd.apple.pkpass",
            reference_time=datetime(2026, 8, 20, tzinfo=UTC),
        )
