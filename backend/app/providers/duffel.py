from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from app.models.domain import Incident, Trip
from app.models.enums import ItemType
from app.models.money import Money
from app.models.recovery import RecoveryOption
from app.services.canonical_hash import canonical_hash


class DuffelQuoteError(RuntimeError):
    """A bounded, user-safe failure while searching Duffel for a recovery quote."""


class DuffelFlightQuoteClient:
    """Search-only Duffel adapter for a recovery option.

    This client deliberately stops at a provider quote.  It never creates an order,
    confirms a change, or handles payment.  A later order-change adapter must require
    a real Duffel order id and a fresh approval before mutating anything.
    """

    def __init__(
        self,
        *,
        access_token: str,
        client: httpx.AsyncClient | None = None,
        base_url: str = "https://api.duffel.com",
    ) -> None:
        if not access_token or len(access_token) > 4096:
            raise ValueError("Duffel access token is required")
        if base_url != "https://api.duffel.com":
            raise ValueError("Duffel quote client accepts only the production API URL")
        self._access_token = access_token
        self._client = client or httpx.AsyncClient()
        self._base_url = base_url

    async def search_recovery_option(
        self, *, trip: Trip, incident: Incident, now: datetime
    ) -> RecoveryOption:
        impact = incident.deterministic_impact
        if impact is None or impact.connection_feasible:
            raise DuffelQuoteError("recovery impact is not actionable")
        flights = sorted(
            (item for item in trip.items if item.type == ItemType.FLIGHT),
            key=lambda item: item.end_at,
        )
        if not flights:
            raise DuffelQuoteError("recovery requires a flight in the itinerary")
        final_flight = flights[-1]
        if not final_flight.origin or not final_flight.destination:
            raise DuffelQuoteError("recovery flight route is incomplete")
        body = {
            "slices": [
                {
                    "origin": final_flight.origin,
                    "destination": final_flight.destination,
                    "departure_date": final_flight.start_at.astimezone(UTC).date().isoformat(),
                }
            ],
            "passengers": [{"type": "adult"}],
            "cabin_class": "economy",
        }
        try:
            response = await self._client.post(
                f"{self._base_url}/air/offer_requests",
                headers={
                    "Authorization": f"Bearer {self._access_token}",
                    "Duffel-Version": "v2",
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=httpx.Timeout(12.0),
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise DuffelQuoteError("Duffel quote service is temporarily unavailable") from exc
        if response.status_code in {401, 403}:
            raise DuffelQuoteError("Duffel credentials were rejected")
        if response.status_code == 429 or response.status_code >= 500:
            raise DuffelQuoteError("Duffel quote service is temporarily unavailable")
        if response.status_code >= 400:
            raise DuffelQuoteError("Duffel could not search this recovery route")
        try:
            payload = response.json()
        except ValueError as exc:
            raise DuffelQuoteError("Duffel returned an invalid quote response") from exc
        offer = self._select_offer(payload)
        return self._to_option(offer, now=now)

    @classmethod
    def _select_offer(cls, payload: Any) -> dict[str, Any]:
        data = payload.get("data") if isinstance(payload, dict) else None
        offers: Any = data.get("offers") if isinstance(data, dict) else None
        if not isinstance(offers, list):
            raise DuffelQuoteError("Duffel returned no recovery offers")
        usable = [offer for offer in offers if isinstance(offer, dict) and cls._offer_id(offer)]
        if not usable:
            raise DuffelQuoteError("Duffel returned no usable recovery offers")
        usable.sort(key=lambda offer: cls._amount_minor(offer))
        return usable[0]

    @staticmethod
    def _offer_id(offer: dict[str, Any]) -> str | None:
        value = offer.get("id")
        return value if isinstance(value, str) and value.startswith("off_") else None

    @staticmethod
    def _amount_minor(offer: dict[str, Any]) -> int:
        total = offer.get("total_amount")
        amount = total.get("amount") if isinstance(total, dict) else None
        try:
            return int(Decimal(str(amount)) * 100)
        except (InvalidOperation, TypeError, ValueError):
            return 2**63 - 1

    @classmethod
    def _to_option(cls, offer: dict[str, Any], *, now: datetime) -> RecoveryOption:
        offer_id = cls._offer_id(offer)
        if offer_id is None:
            raise DuffelQuoteError("Duffel offer id is missing")
        total = offer.get("total_amount")
        currency = total.get("currency") if isinstance(total, dict) else None
        if not isinstance(currency, str) or len(currency) != 3:
            raise DuffelQuoteError("Duffel offer currency is invalid")
        minor_units = cls._amount_minor(offer)
        if minor_units == 2**63 - 1:
            raise DuffelQuoteError("Duffel offer price is invalid")
        arriving_at = cls._arrival(offer)
        if arriving_at is None:
            raise DuffelQuoteError("Duffel offer arrival time is missing")
        expires_at = cls._datetime(offer.get("expires_at")) or (now + timedelta(minutes=10))
        fingerprint = canonical_hash(
            {
                "offer_id": offer_id,
                "amount": minor_units,
                "currency": currency.upper(),
                "arrival_at": arriving_at,
            }
        )
        return RecoveryOption(
            provider="duffel",
            provider_option_id=offer_id,
            option_fingerprint=fingerprint,
            incremental_cost=Money(currency=currency.upper(), minor_units=minor_units),
            quote_expires_at=expires_at,
            provider_snapshot_hash=canonical_hash(offer),
            arrival_at=arriving_at,
            reversible=False,
            reversible_until=None,
        )

    @classmethod
    def _arrival(cls, offer: dict[str, Any]) -> datetime | None:
        slices = offer.get("slices")
        if not isinstance(slices, list) or not slices:
            return None
        final_slice = slices[-1]
        segments = final_slice.get("segments") if isinstance(final_slice, dict) else None
        if not isinstance(segments, list) or not segments:
            return None
        final_segment = segments[-1]
        if not isinstance(final_segment, dict):
            return None
        return cls._datetime(final_segment.get("arriving_at"))

    @staticmethod
    def _datetime(value: Any) -> datetime | None:
        if not isinstance(value, str):
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
        return parsed.astimezone(UTC)
