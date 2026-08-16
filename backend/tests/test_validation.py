from datetime import datetime

import pytest
from app.models.domain import DisruptionEvent
from app.models.pubsub import PubSubEnvelope
from pydantic import ValidationError


def test_invalid_disruption_input_is_rejected() -> None:
    with pytest.raises(ValidationError):
        DisruptionEvent.model_validate(
            {
                "event_id": "",
                "trip_id": "demo-trip-001",
                "type": "flight_delay",
                "flight": "LO351",
                "old_arrival": datetime(2026, 8, 20, 18, 0),
                "new_arrival": "not-a-date",
            }
        )


def test_invalid_pubsub_payload_is_rejected() -> None:
    envelope = PubSubEnvelope.model_validate({"message": {"data": "not-base64!!"}})

    with pytest.raises(ValueError, match="invalid Pub/Sub message data"):
        envelope.message.decode_event()
