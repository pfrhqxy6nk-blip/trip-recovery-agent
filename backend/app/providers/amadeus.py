from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

import httpx

from app.models.domain import TravelItem
from app.models.monitoring import ObservationSnapshot, ObservationStatus


class AmadeusFlightStatusError(RuntimeError):
    """A non-authoritative or invalid Amadeus response; never interpret it as on-time."""


_FLIGHT = re.compile(r"^([A-Z0-9]{2})([0-9]{1,4}[A-Z]?)$")


class AmadeusFlightStatusClient:
    """Small production-only client for Amadeus On-Demand Flight Status.

    It does not schedule polling or mutate trip state. Callers must enforce their own
    global budget and pass a subscription already bound to the flight item.
    """

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        client: httpx.AsyncClient,
        base_url: str = "https://api.amadeus.com",
    ) -> None:
        if base_url.rstrip("/") != "https://api.amadeus.com":
            raise ValueError("live status client accepts only the production Amadeus URL")
        self._client_id = client_id
        self._client_secret = client_secret
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._access_token: str | None = None
        self._token_expires_at: datetime | None = None

    async def fetch_snapshot(
        self, *, subscription_id: str, source_id: str, item: TravelItem, observed_at: datetime
    ) -> ObservationSnapshot:
        if item.scheduled_local_date is None:
            raise AmadeusFlightStatusError("stored flight is missing its local departure date")
        carrier, number = self._flight_parts(item.external_id or "")
        token = await self._access_token_for(observed_at)
        response = await self._client.get(
            f"{self._base_url}/v2/schedule/flights",
            params={
                "carrierCode": carrier,
                "flightNumber": number,
                "scheduledDepartureDate": item.scheduled_local_date.isoformat(),
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        if response.status_code == 401:
            self._access_token = None
            token = await self._access_token_for(observed_at, force_refresh=True)
            response = await self._client.get(
                f"{self._base_url}/v2/schedule/flights",
                params={
                    "carrierCode": carrier,
                    "flightNumber": number,
                    "scheduledDepartureDate": item.scheduled_local_date.isoformat(),
                },
                headers={"Authorization": f"Bearer {token}"},
            )
        if response.status_code >= 400:
            raise AmadeusFlightStatusError(
                f"flight status request failed with HTTP {response.status_code}"
            )
        try:
            record = response.json()["data"][0]
            arrival = self._arrival(record)
        except (IndexError, KeyError, TypeError, ValueError) as exc:
            raise AmadeusFlightStatusError(
                "flight status response has no usable arrival time"
            ) from exc
        status = ObservationStatus.DELAYED if arrival > item.end_at else ObservationStatus.ON_TIME
        return ObservationSnapshot(
            subscription_id=subscription_id,
            source_id=source_id,
            status=status,
            scheduled_arrival=item.end_at,
            observed_arrival=arrival,
            source_updated_at=observed_at,
            observed_at=observed_at,
            provider_event_id=self._provider_event_id(record, carrier, number),
        )

    async def _access_token_for(self, now: datetime, *, force_refresh: bool = False) -> str:
        if (
            not force_refresh
            and self._access_token is not None
            and self._token_expires_at is not None
            and self._token_expires_at > now + timedelta(seconds=30)
        ):
            return self._access_token
        response = await self._client.post(
            f"{self._base_url}/v1/security/oauth2/token",
            data={
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if response.status_code >= 400:
            raise AmadeusFlightStatusError("Amadeus authentication failed")
        try:
            payload = response.json()
            token = str(payload["access_token"])
            expires_in = int(payload["expires_in"])
        except (KeyError, TypeError, ValueError) as exc:
            raise AmadeusFlightStatusError("Amadeus token response is invalid") from exc
        if not token or expires_in <= 30:
            raise AmadeusFlightStatusError("Amadeus token response is unusable")
        self._access_token = token
        self._token_expires_at = now + timedelta(seconds=expires_in)
        return token

    @staticmethod
    def _flight_parts(value: str) -> tuple[str, str]:
        match = _FLIGHT.fullmatch(value.replace(" ", "").upper())
        if match is None:
            raise AmadeusFlightStatusError("flight number is not usable for Amadeus status lookup")
        return match.group(1), match.group(2)

    @staticmethod
    def _arrival(record: dict[str, Any]) -> datetime:
        points = record.get("flightPoints")
        if not isinstance(points, list) or not points:
            raise ValueError("flight points missing")
        arrival = points[-1].get("arrival")
        timings = arrival.get("timings") if isinstance(arrival, dict) else None
        if not isinstance(timings, list):
            raise ValueError("arrival timings missing")
        preferred = None
        for qualifier in ("ATA", "ETA", "STA"):
            preferred = next(
                (
                    timing.get("value")
                    for timing in timings
                    if isinstance(timing, dict) and timing.get("qualifier") == qualifier
                ),
                None,
            )
            if preferred is not None:
                break
        if not isinstance(preferred, str):
            raise ValueError("arrival timing missing")
        parsed = datetime.fromisoformat(preferred.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("arrival timing is naive")
        return parsed

    @staticmethod
    def _provider_event_id(record: dict[str, Any], carrier: str, number: str) -> str:
        return str(
            record.get("id") or f"{carrier}{number}:{record.get('scheduledDepartureDate', '')}"
        )
