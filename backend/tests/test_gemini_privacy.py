from __future__ import annotations

import json

from app.agents.gemini import build_gemini_context_payload
from app.demo_data import build_demo_trip
from app.services.impact import DeterministicImpactEngine

from tests.helpers import disruption_event


def test_gemini_context_excludes_identity_and_booking_pii() -> None:
    trip = build_demo_trip()
    trip.owner_user_id = "telegram:judge-user"
    trip.items[0].booking_reference = "PNR-SECRET-123"
    trip.items[0].contact_email = "traveler@example.com"
    event = disruption_event(event_id="private-event")
    event.context = {
        "airline": "Example Air",
        "source_url": "https://airline.example/status",
        "owner_user_id": "telegram:judge-user",
        "booking_reference": "PNR-SECRET-123",
        "contact_email": "traveler@example.com",
    }

    payload = build_gemini_context_payload(
        event,
        trip,
        DeterministicImpactEngine().calculate(event, trip),
    )
    encoded = json.dumps(payload)

    assert "telegram:judge-user" not in encoded
    assert "PNR-SECRET-123" not in encoded
    assert "traveler@example.com" not in encoded
    assert payload["event"]["context"] == {
        "airline": "Example Air",
        "source_url": "https://airline.example/status",
    }
    assert all("booking_reference" not in item for item in payload["trip"]["items"])
    assert all("contact_email" not in item for item in payload["trip"]["items"])
