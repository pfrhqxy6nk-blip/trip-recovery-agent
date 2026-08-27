from __future__ import annotations

import logging
from datetime import datetime
from typing import Literal

from app.models.telegram import OutboundNotification, TelegramView
from app.providers.telegram import TelegramGatewayError, TelegramRetryClass
from app.services.canonical_hash import canonical_hash
from app.services.ports import IncidentRepository, TelegramGateway

logger = logging.getLogger(__name__)


class NotificationConflict(RuntimeError):
    pass


class DurableTelegramDelivery:
    """Persist an outbound intent and suppress already-receipted redeliveries."""

    def __init__(self, repository: IncidentRepository, gateway: TelegramGateway) -> None:
        self._repository = repository
        self._gateway = gateway

    async def send_once(
        self,
        *,
        incident_id: str,
        kind: Literal["AWARENESS", "APPROVAL", "FINAL", "WATCH_SIGNAL"],
        dedupe_key: str,
        chat_id: str,
        view: TelegramView,
        now: datetime,
    ) -> bool:
        notification_id = canonical_hash(
            {"incident_id": incident_id, "kind": kind, "dedupe_key": dedupe_key}
        )
        existing = await self._repository.get_notification(notification_id)
        if existing is not None and existing.status == "SENT":
            return True
        if existing is not None and existing.status in {"UNKNOWN", "BLOCKED"}:
            return False
        intent = OutboundNotification(
            notification_id=notification_id,
            incident_id=incident_id,
            kind=kind,
            chat_id=chat_id,
            view_hash=canonical_hash(view),
            created_at=now,
        )
        if not await self._repository.store_notification_intent(intent):
            raise NotificationConflict("notification ID was reused with different content")
        try:
            receipt = await self._gateway.send_message(chat_id=chat_id, view=view)
        except TelegramGatewayError as exc:
            if exc.retry_class == TelegramRetryClass.UNKNOWN_OUTCOME:
                await self._repository.mark_notification_unknown(
                    notification_id=notification_id, unknown_at=now
                )
                logger.error(
                    "TELEGRAM_DELIVERY_UNKNOWN",
                    extra={
                        "incident_id": incident_id,
                        "notification_id": notification_id,
                        "provider": "telegram",
                        "result_class": exc.retry_class.value,
                    },
                )
                return False
            if exc.retry_class == TelegramRetryClass.TERMINAL:
                failure_code = (
                    f"HTTP_{exc.status_code}" if exc.status_code is not None else "TERMINAL"
                )
                await self._repository.mark_notification_blocked(
                    notification_id=notification_id,
                    blocked_at=now,
                    failure_code=failure_code,
                )
                logger.error(
                    "TELEGRAM_DELIVERY_BLOCKED",
                    extra={
                        "incident_id": incident_id,
                        "notification_id": notification_id,
                        "provider": "telegram",
                        "result_class": exc.retry_class.value,
                    },
                )
                return False
            logger.warning(
                "TELEGRAM_DELIVERY_RETRYABLE",
                extra={
                    "incident_id": incident_id,
                    "notification_id": notification_id,
                    "provider": "telegram",
                    "result_class": exc.retry_class.value,
                },
            )
            raise
        if not await self._repository.mark_notification_sent(
            notification_id=notification_id,
            message_id=receipt.message_id,
            sent_at=now,
        ):
            raise NotificationConflict("could not persist Telegram delivery receipt")
        logger.info(
            "TELEGRAM_DELIVERY_SENT",
            extra={
                "incident_id": incident_id,
                "notification_id": notification_id,
                "provider": "telegram",
                "result_class": "SENT",
            },
        )
        return True
