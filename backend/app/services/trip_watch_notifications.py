from __future__ import annotations

import logging
from datetime import datetime
from html import escape

from app.models.telegram import TelegramButton, TelegramView
from app.models.watch import GroundedTravelSignal, SourceTrust, TripWatchpoint
from app.services.canonical_hash import grounded_signal_hash
from app.services.ports import IncidentRepository, TelegramGateway
from app.services.telegram_delivery import DurableTelegramDelivery

logger = logging.getLogger(__name__)


class TripWatchSignalNotifier:
    """Deliver non-recovery watch facts without granting them recovery authority."""

    def __init__(self, repository: IncidentRepository, gateway: TelegramGateway) -> None:
        self._repository = repository
        self._delivery = DurableTelegramDelivery(repository, gateway)

    async def notify(
        self,
        *,
        trip_id: str,
        watchpoint: TripWatchpoint,
        signal: GroundedTravelSignal,
        now: datetime,
    ) -> bool:
        trip = await self._repository.get_trip(trip_id)
        if trip is None or trip.owner_user_id is None:
            logger.warning(
                "TRIP_WATCH_SIGNAL_NO_OWNER",
                extra={"trip_id": trip_id, "watchpoint_id": watchpoint.watchpoint_id},
            )
            # There is no safe recipient. Acknowledging the fact prevents an
            # orphaned trip from causing an infinite scheduler retry loop.
            return True
        traveler = await self._repository.get_traveler_by_user_id(trip.owner_user_id)
        if traveler is None:
            logger.warning(
                "TRIP_WATCH_SIGNAL_NO_TRAVELER",
                extra={"trip_id": trip_id, "owner_user_id": trip.owner_user_id},
            )
            return True
        trust_label = (
            "Official source"
            if signal.trust == SourceTrust.OFFICIAL
            else "Public signal — verify before acting"
        )
        kind_label = watchpoint.kind.value.replace("_", " ").title()
        view = TelegramView(
            text=(
                f"<b>Trip Watch · {escape(kind_label)}</b>\n\n"
                f"{escape(signal.summary)}\n\n"
                f"<i>{escape(trust_label)} · your itinerary is unchanged.</i>\n"
                f"Source: {escape(signal.source_title)}"
            ),
            parse_mode="HTML",
            buttons=[TelegramButton(text="Open source", url=signal.source_url)],
        )
        return await self._delivery.send_once(
            incident_id=f"watch:{watchpoint.watchpoint_id}",
            kind="WATCH_SIGNAL",
            dedupe_key=grounded_signal_hash(signal),
            chat_id=traveler.telegram_chat_id,
            view=view,
            now=now,
        )
