from datetime import UTC, date, datetime

import httpx
import pytest
from app.models.domain import TravelItem
from app.models.enums import ItemType
from app.models.monitoring import ObservationStatus
from app.providers.amadeus import AmadeusFlightStatusClient, AmadeusFlightStatusError


def item() -> TravelItem:
    return TravelItem(
        item_id="flight-1",
        trip_id="trip-1",
        type=ItemType.FLIGHT,
        provider="LOT",
        external_id="LO351",
        origin="WAW",
        destination="MUC",
        scheduled_local_date=date(2026, 8, 20),
        start_at=datetime(2026, 8, 20, 13, tzinfo=UTC),
        end_at=datetime(2026, 8, 20, 16, tzinfo=UTC),
    )


async def test_production_client_authenticates_and_normalizes_delayed_status() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json={"access_token": "test-token", "expires_in": 3600})
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "dated-flight-1",
                        "flightPoints": [
                            {"departure": {"timings": []}},
                            {
                                "arrival": {
                                    "timings": [
                                        {"qualifier": "STA", "value": "2026-08-20T16:00:00Z"},
                                        {"qualifier": "ETA", "value": "2026-08-20T17:45:00Z"},
                                    ]
                                }
                            },
                        ],
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = AmadeusFlightStatusClient(client_id="client", client_secret="secret", client=http)
        snapshot = await client.fetch_snapshot(
            subscription_id="monitor:trip-1:flight-1",
            source_id="amadeus-flight-status-v1",
            item=item(),
            observed_at=datetime(2026, 8, 20, 14, tzinfo=UTC),
        )

    assert snapshot.status == ObservationStatus.DELAYED
    assert snapshot.observed_arrival == datetime(2026, 8, 20, 17, 45, tzinfo=UTC)
    assert seen[1].url.params["carrierCode"] == "LO"
    assert seen[1].url.params["flightNumber"] == "351"
    assert seen[1].url.params["scheduledDepartureDate"] == "2026-08-20"
    assert seen[1].headers["authorization"] == "Bearer test-token"


async def test_client_rejects_test_url_and_unusable_response() -> None:
    async with httpx.AsyncClient() as http:
        with pytest.raises(ValueError, match="production"):
            AmadeusFlightStatusClient(
                client_id="client",
                client_secret="secret",
                client=http,
                base_url="https://test.api.amadeus.com",
            )

    async def broken(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"access_token": "token", "expires_in": 3600})

    async with httpx.AsyncClient(transport=httpx.MockTransport(broken)) as http:
        client = AmadeusFlightStatusClient(client_id="client", client_secret="secret", client=http)
        with pytest.raises(AmadeusFlightStatusError, match="usable arrival"):
            await client.fetch_snapshot(
                subscription_id="monitor:trip-1:flight-1",
                source_id="amadeus-flight-status-v1",
                item=item(),
                observed_at=datetime(2026, 8, 20, 14, tzinfo=UTC),
            )
