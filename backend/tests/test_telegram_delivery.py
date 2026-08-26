from datetime import UTC, datetime

from app.models.telegram import TelegramMessageReceipt, TelegramView
from app.providers.telegram import TelegramGatewayError, TelegramRetryClass
from app.services.memory import InMemoryIncidentRepository
from app.services.telegram_delivery import DurableTelegramDelivery


class RecordingGateway:
    def __init__(self) -> None:
        self.calls = 0

    async def send_message(self, *, chat_id: str, view: TelegramView) -> TelegramMessageReceipt:
        self.calls += 1
        return TelegramMessageReceipt(chat_id=chat_id, message_id=40 + self.calls)

    async def edit_message(
        self, *, chat_id: str, message_id: int, view: TelegramView
    ) -> TelegramMessageReceipt:
        raise AssertionError("not used")

    async def answer_callback_query(
        self,
        *,
        callback_query_id: str,
        text: str | None = None,
        show_alert: bool = False,
    ) -> None:
        raise AssertionError("not used")


async def test_receipted_notification_is_not_sent_twice() -> None:
    repository = InMemoryIncidentRepository()
    gateway = RecordingGateway()
    delivery = DurableTelegramDelivery(repository, gateway)
    now = datetime(2026, 8, 17, tzinfo=UTC)
    arguments = {
        "incident_id": "incident-1",
        "kind": "AWARENESS",
        "dedupe_key": "plan-1",
        "chat_id": "101",
        "view": TelegramView(text="Trip change detected"),
        "now": now,
    }

    assert await delivery.send_once(**arguments)  # type: ignore[arg-type]
    assert await delivery.send_once(**arguments)  # type: ignore[arg-type]

    assert gateway.calls == 1
    assert len(repository.notifications) == 1


class UnknownOutcomeGateway(RecordingGateway):
    async def send_message(self, *, chat_id: str, view: TelegramView) -> TelegramMessageReceipt:
        self.calls += 1
        raise TelegramGatewayError(
            operation="send_message",
            retry_class=TelegramRetryClass.UNKNOWN_OUTCOME,
        )


async def test_unknown_delivery_is_not_repeated_or_treated_as_delivered() -> None:
    repository = InMemoryIncidentRepository()
    gateway = UnknownOutcomeGateway()
    delivery = DurableTelegramDelivery(repository, gateway)
    now = datetime(2026, 8, 17, tzinfo=UTC)
    view = TelegramView(text="Trip change detected")

    first = await delivery.send_once(
        incident_id="incident-2",
        kind="AWARENESS",
        dedupe_key="plan-2",
        chat_id="101",
        view=view,
        now=now,
    )
    repeated = await delivery.send_once(
        incident_id="incident-2",
        kind="AWARENESS",
        dedupe_key="plan-2",
        chat_id="101",
        view=view,
        now=now,
    )

    assert first is False and repeated is False
    assert gateway.calls == 1
    assert next(iter(repository.notifications.values())).status == "UNKNOWN"


class BlockedGateway(RecordingGateway):
    async def send_message(self, *, chat_id: str, view: TelegramView) -> TelegramMessageReceipt:
        self.calls += 1
        raise TelegramGatewayError(
            operation="send_message",
            retry_class=TelegramRetryClass.TERMINAL,
            status_code=403,
        )


async def test_blocked_bot_is_persisted_and_not_retried() -> None:
    repository = InMemoryIncidentRepository()
    gateway = BlockedGateway()
    delivery = DurableTelegramDelivery(repository, gateway)
    now = datetime(2026, 8, 17, tzinfo=UTC)
    arguments = {
        "incident_id": "incident-blocked",
        "kind": "AWARENESS",
        "dedupe_key": "plan-blocked",
        "chat_id": "101",
        "view": TelegramView(text="Trip change detected"),
        "now": now,
    }

    assert await delivery.send_once(**arguments) is False  # type: ignore[arg-type]
    assert await delivery.send_once(**arguments) is False  # type: ignore[arg-type]

    notification = next(iter(repository.notifications.values()))
    assert gateway.calls == 1
    assert notification.status == "BLOCKED"
    assert notification.failure_code == "HTTP_403"
