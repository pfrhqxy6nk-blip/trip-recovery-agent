from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from app.api import telegram as telegram_api
from app.config import Settings
from app.main import AppContainer, create_app
from app.models.enums import PolicyMode
from app.models.telegram import TelegramFileDownload, TelegramMessageReceipt, TelegramView
from app.services.memory import InMemoryIncidentRepository, LocalEventPublisher
from app.services.onboarding import TelegramOnboardingService
from app.services.ports import TelegramGateway
from app.services.telegram_planning import TelegramPlanningService
from app.services.telegram_trips import TelegramTripService
from app.workflows.impact_analysis import ImpactAnalysisWorkflow
from httpx import ASGITransport, AsyncClient
from pytest import MonkeyPatch

from tests.helpers import ValidInterpreter


def message_update(
    update_id: int, text: str, *, user_id: int = 101, chat_id: int = 202
) -> dict[str, object]:
    return {
        "update_id": update_id,
        "message": {"chat": {"id": chat_id}, "from": {"id": user_id}, "text": text},
    }


def callback_update(
    update_id: int, data: str, *, user_id: int = 101, chat_id: int = 202
) -> dict[str, object]:
    return {
        "update_id": update_id,
        "callback_query": {
            "id": f"callback-{update_id}",
            "from": {"id": user_id},
            "message": {
                "message_id": 70 + update_id,
                "chat": {"id": chat_id},
                "from": {"id": user_id},
                "text": "",
            },
            "data": data,
        },
    }


async def telegram_client(
    gateway: TelegramGateway | None = None, *, enable_planning: bool = False
) -> AsyncClient:
    # Unit-test the combined app role explicitly. Production uses the split
    # edge/worker runtime, where the public webhook is exposed by app.edge.
    settings = Settings(
        pubsub_transport="local",
        app_role="all",
        telegram_webhook_secret="test-secret-123456",
    )
    repository = InMemoryIncidentRepository()
    container = AppContainer(
        settings,
        repository,
        LocalEventPublisher(),
        ImpactAnalysisWorkflow(repository, ValidInterpreter()),
        TelegramOnboardingService(repository),
        gateway,
        telegram_trips=TelegramTripService(repository, pilot_enabled=False),
        telegram_planning=TelegramPlanningService(repository) if enable_planning else None,
    )
    app = create_app(settings, container=container)
    app.state.container = container
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    return client


class RecordingTelegramGateway:
    def __init__(self) -> None:
        self.sent: list[tuple[str, TelegramView]] = []
        self.edited: list[tuple[str, int, TelegramView]] = []
        self.answered: list[str] = []
        self.events: list[str] = []

    async def send_message(self, *, chat_id: str, view: TelegramView) -> TelegramMessageReceipt:
        self.sent.append((chat_id, view))
        self.events.append("send")
        return TelegramMessageReceipt(chat_id=chat_id, message_id=101)

    async def edit_message(
        self, *, chat_id: str, message_id: int, view: TelegramView
    ) -> TelegramMessageReceipt:
        self.edited.append((chat_id, message_id, view))
        self.events.append("edit")
        return TelegramMessageReceipt(chat_id=chat_id, message_id=message_id)

    async def answer_callback_query(
        self,
        *,
        callback_query_id: str,
        text: str | None = None,
        show_alert: bool = False,
    ) -> None:
        self.answered.append(callback_query_id)
        self.events.append("answer")


class MediaTelegramGateway(RecordingTelegramGateway):
    def __init__(self, content: bytes | None = None) -> None:
        super().__init__()
        self.downloads: list[tuple[str, int]] = []
        self.content = content or b"%PDF-1.7 ticket"

    async def download_file(
        self, *, file_id: str, file_name: str | None, mime_type: str | None, max_bytes: int
    ) -> TelegramFileDownload:
        self.downloads.append((file_id, max_bytes))
        return TelegramFileDownload(
            file_id=file_id,
            file_name=file_name,
            mime_type=mime_type,
            content=self.content,
        )


async def test_resumable_onboarding_activates_policy() -> None:
    client = await telegram_client()
    headers = {"X-Telegram-Bot-Api-Secret-Token": "test-secret-123456"}
    try:
        start = await client.post(
            "/telegram/webhook", json=message_update(1, "/start"), headers=headers
        )
        assert start.status_code == 200
        assert start.json()["button_rows"][0][0]["callback_data"] == "onboard:setup"
        assert start.json()["button_rows"][1][0]["callback_data"] == "onboard:setup"
        assert "demo" not in start.json()["text"].lower()

        callbacks = [
            "onboard:setup",
            "onboard:calendar_auto",
            "onboard:service_auto",
            "onboard:reversible_auto",
            "onboard:spend_20",
            "onboard:boundary_continue",
            "onboard:activate",
        ]
        response = start
        for offset, callback in enumerate(callbacks, start=2):
            response = await client.post(
                "/telegram/webhook", json=callback_update(offset, callback), headers=headers
            )
            assert response.status_code == 200

        assert "active" in response.json()["text"].lower()
        settings = await client.post(
            "/telegram/webhook", json=message_update(20, "/settings"), headers=headers
        )
        assert settings.status_code == 200
        assert "€20" in settings.json()["text"]
        assert "Google Calendar connection: not connected" in settings.json()["text"]
        assert "Gmail connection: not connected" in settings.json()["text"]
    finally:
        await client.aclose()


async def test_first_user_activation_switches_to_plain_english_chat() -> None:
    client = await telegram_client(enable_planning=True)
    headers = {"X-Telegram-Bot-Api-Secret-Token": "test-secret-123456"}
    try:
        start = await client.post(
            "/telegram/webhook", json=message_update(1, "/start"), headers=headers
        )
        start_payload = start.json()
        start_buttons = [
            button["text"] for row in start_payload.get("button_rows", []) for button in row
        ]
        assert start_buttons == ["Start my trip", "Plan a trip"]

        callbacks = [
            "onboard:setup",
            "onboard:calendar_auto",
            "onboard:service_auto",
            "onboard:reversible_auto",
            "onboard:spend_none",
            "onboard:boundary_continue",
            "onboard:activate",
        ]
        response = start
        for offset, callback in enumerate(callbacks, start=2):
            response = await client.post(
                "/telegram/webhook", json=callback_update(offset, callback), headers=headers
            )
            assert response.status_code == 200

        activated = response.json()
        assert activated["button_rows"] == []
        assert activated["buttons"] == []
        assert "plain English" in activated["text"]

        planning = await client.post(
            "/telegram/webhook",
            json=message_update(20, "I want to go to Paris for 6 nights, budget €600, from Kyiv."),
            headers=headers,
        )
        assert planning.status_code == 200
        planning_payload = planning.json()
        assert "Planning" in planning_payload["text"]
        assert "Paris" in planning_payload["text"]
        assert "Live Google Search is temporarily unavailable" in planning_payload["text"]
        assert "Google Flights" in planning_payload["text"]
        assert "ibis Paris République" in planning_payload["text"]
        assert "Flight search" in planning_payload["text"]
        assert len(planning_payload["button_rows"]) == 3
    finally:
        await client.aclose()


async def test_telegram_webhook_rejects_forgery_and_duplicate_update() -> None:
    client = await telegram_client()
    good = {"X-Telegram-Bot-Api-Secret-Token": "test-secret-123456"}
    try:
        rejected = await client.post("/telegram/webhook", json=message_update(1, "/start"))
        assert rejected.status_code == 401

        first = await client.post(
            "/telegram/webhook", json=message_update(2, "/start"), headers=good
        )
        duplicate = await client.post(
            "/telegram/webhook", json=message_update(2, "/start"), headers=good
        )
        assert first.status_code == 200
        assert duplicate.json()["text"] == "This update was already handled."
    finally:
        await client.aclose()


async def test_free_text_routes_to_the_safe_trip_conversation() -> None:
    client = await telegram_client()
    headers = {"X-Telegram-Bot-Api-Secret-Token": "test-secret-123456"}
    try:
        await client.post("/telegram/webhook", json=message_update(1, "/start"), headers=headers)
        for offset, callback in enumerate(
            [
                "onboard:setup",
                "onboard:calendar_auto",
                "onboard:service_auto",
                "onboard:reversible_auto",
                "onboard:spend_none",
                "onboard:boundary_continue",
                "onboard:activate",
            ],
            start=2,
        ):
            await client.post(
                "/telegram/webhook", json=callback_update(offset, callback), headers=headers
            )
        response = await client.post(
            "/telegram/webhook", json=message_update(20, "что ты отслеживаешь?"), headers=headers
        )
    finally:
        await client.aclose()

    assert response.status_code == 200
    assert "weather warnings" in response.json()["text"]


async def test_urgent_airline_alert_does_not_overwrite_the_itinerary_draft() -> None:
    client = await telegram_client()
    headers = {"X-Telegram-Bot-Api-Secret-Token": "test-secret-123456"}
    try:
        await client.post("/telegram/webhook", json=message_update(1, "/start"), headers=headers)
        for offset, callback in enumerate(
            [
                "onboard:setup",
                "onboard:calendar_auto",
                "onboard:service_auto",
                "onboard:reversible_auto",
                "onboard:spend_none",
                "onboard:boundary_continue",
                "onboard:activate",
            ],
            start=2,
        ):
            await client.post(
                "/telegram/webhook", json=callback_update(offset, callback), headers=headers
            )
        response = await client.post(
            "/telegram/webhook",
            json=message_update(
                20,
                "My flight LO351 WAW -> MUC is delayed by 105 minutes; I will miss my connection.",
            ),
            headers=headers,
        )
    finally:
        await client.aclose()

    assert response.status_code == 200
    assert "Don’t make a rushed booking" in response.json()["text"]
    assert "Save trip" not in response.json()["text"]


async def test_forwarded_booking_email_routes_to_multimodal_trip_draft() -> None:
    client = await telegram_client()
    headers = {"X-Telegram-Bot-Api-Secret-Token": "test-secret-123456"}
    try:
        await client.post("/telegram/webhook", json=message_update(1, "/start"), headers=headers)
        for offset, callback in enumerate(
            [
                "onboard:setup",
                "onboard:calendar_auto",
                "onboard:service_auto",
                "onboard:reversible_auto",
                "onboard:spend_none",
                "onboard:boundary_continue",
                "onboard:activate",
            ],
            start=2,
        ):
            await client.post(
                "/telegram/webhook", json=callback_update(offset, callback), headers=headers
            )
        response = await client.post(
            "/telegram/webhook",
            json=message_update(
                20,
                "Booking confirmation: LOT flight LO351 from WAW to MUC. PNR ABC123.",
            ),
            headers=headers,
        )
    finally:
        await client.aclose()

    assert response.status_code == 200
    assert "LO351" in response.json()["text"]
    payload = response.json()
    button_texts = {button["text"] for button in payload.get("buttons", [])}
    button_texts.update(button["text"] for row in payload.get("button_rows", []) for button in row)
    assert "Save trip" in button_texts


async def test_flexible_trip_brief_routes_through_real_webhook() -> None:
    client = await telegram_client(enable_planning=True)
    headers = {"X-Telegram-Bot-Api-Secret-Token": "test-secret-123456"}
    try:
        await client.post("/telegram/webhook", json=message_update(1, "/start"), headers=headers)
        for offset, callback in enumerate(
            [
                "onboard:setup",
                "onboard:calendar_auto",
                "onboard:service_auto",
                "onboard:reversible_auto",
                "onboard:spend_none",
                "onboard:boundary_continue",
                "onboard:activate",
            ],
            start=2,
        ):
            response = await client.post(
                "/telegram/webhook", json=callback_update(offset, callback), headers=headers
            )
            assert response.status_code == 200

        response = await client.post(
            "/telegram/webhook",
            json=message_update(20, "I want to go to Paris for 6 nights, budget €600, from Kyiv."),
            headers=headers,
        )
    finally:
        await client.aclose()

    assert response.status_code == 200
    payload = response.json()
    assert "flexible dates" in payload["text"].lower()
    assert "€600" in payload["text"]
    button_texts = {button["text"] for button in payload.get("buttons", [])}
    button_texts.update(button["text"] for row in payload.get("button_rows", []) for button in row)
    assert any(text.startswith("Choose ") for text in button_texts)
    assert "Book" not in payload["text"]


async def test_planning_input_error_is_delivered_as_an_english_chat_message() -> None:
    client = await telegram_client(enable_planning=True)
    headers = {"X-Telegram-Bot-Api-Secret-Token": "test-secret-123456"}
    try:
        await client.post("/telegram/webhook", json=message_update(1, "/start"), headers=headers)
        for offset, callback in enumerate(
            [
                "onboard:setup",
                "onboard:calendar_auto",
                "onboard:service_auto",
                "onboard:reversible_auto",
                "onboard:spend_none",
                "onboard:boundary_continue",
                "onboard:activate",
            ],
            start=2,
        ):
            await client.post(
                "/telegram/webhook", json=callback_update(offset, callback), headers=headers
            )
        response = await client.post(
            "/telegram/webhook",
            json=message_update(20, "/plan Paris | 2026-10-10 | not-a-date | 600 | museums"),
            headers=headers,
        )
    finally:
        await client.aclose()

    assert response.status_code == 200
    assert "couldn't continue planning" in response.json()["text"]
    assert "plain English" in response.json()["text"]


async def test_cross_user_callback_cannot_change_onboarding() -> None:
    client = await telegram_client()
    headers = {"X-Telegram-Bot-Api-Secret-Token": "test-secret-123456"}
    try:
        await client.post("/telegram/webhook", json=message_update(1, "/start"), headers=headers)
        stolen = await client.post(
            "/telegram/webhook",
            json=callback_update(2, "onboard:setup", user_id=999),
            headers=headers,
        )
        assert stolen.status_code == 400
    finally:
        await client.aclose()


async def test_configured_gateway_sends_and_edits_real_telegram_views() -> None:
    gateway = RecordingTelegramGateway()
    client = await telegram_client(gateway)
    headers = {"X-Telegram-Bot-Api-Secret-Token": "test-secret-123456"}
    try:
        start = await client.post(
            "/telegram/webhook", json=message_update(1, "/start"), headers=headers
        )
        callback = await client.post(
            "/telegram/webhook",
            json=callback_update(2, "onboard:setup"),
            headers=headers,
        )
    finally:
        await client.aclose()

    assert start.status_code == 200
    assert callback.status_code == 200
    assert gateway.sent[0][0] == "202"
    assert gateway.sent[0][1].button_rows[0][0].callback_data == "onboard:setup"
    assert gateway.sent[0][1].button_rows[1][0].callback_data == "onboard:setup"
    assert gateway.answered == ["callback-2"]
    assert gateway.events[-2:] == ["answer", "edit"]
    assert gateway.edited[0][0:2] == ("202", 72)
    assert "calendar" in gateway.edited[0][2].text.lower()


async def test_document_webhook_downloads_media_before_multimodal_intake() -> None:
    gateway = MediaTelegramGateway()
    client = await telegram_client(gateway)
    headers = {"X-Telegram-Bot-Api-Secret-Token": "test-secret-123456"}
    document_update = {
        "update_id": 30,
        "message": {
            "chat": {"id": 202},
            "from": {"id": 101},
            "caption": (
                "Booking confirmation LO351 WAW MUC PNR ABC999; "
                "2026-08-20T15:00:00+00:00 2026-08-20T18:00:00+00:00"
            ),
            "document": {
                "file_id": "telegram-file-1",
                "file_name": "ticket.pdf",
                "mime_type": "application/pdf",
            },
        },
    }
    try:
        await client.post("/telegram/webhook", json=message_update(1, "/start"), headers=headers)
        for offset, callback in enumerate(
            [
                "onboard:setup",
                "onboard:calendar_auto",
                "onboard:service_auto",
                "onboard:reversible_auto",
                "onboard:spend_none",
                "onboard:boundary_continue",
                "onboard:activate",
            ],
            start=2,
        ):
            await client.post(
                "/telegram/webhook", json=callback_update(offset, callback), headers=headers
            )
        response = await client.post("/telegram/webhook", json=document_update, headers=headers)
    finally:
        await client.aclose()

    assert response.status_code == 200
    assert gateway.downloads == [("telegram-file-1", telegram_api.MAX_TELEGRAM_MEDIA_BYTES)]
    assert "LO351" in response.json()["text"]


async def test_forwarded_beta_pdf_builds_draft_without_caption() -> None:
    fixture = (
        Path(__file__).parents[2] / "demo" / "fixtures" / "warsaw-munich-lisbon-booking.pdf"
    ).read_bytes()
    gateway = MediaTelegramGateway(content=fixture)
    client = await telegram_client(gateway)
    headers = {"X-Telegram-Bot-Api-Secret-Token": "test-secret-123456"}
    document_update = {
        "update_id": 31,
        "message": {
            "chat": {"id": 202},
            "from": {"id": 101},
            "document": {
                "file_id": "telegram-beta-pdf",
                "file_name": "warsaw-munich-lisbon-booking.pdf",
                "mime_type": "application/pdf",
            },
        },
    }
    try:
        await client.post("/telegram/webhook", json=message_update(1, "/start"), headers=headers)
        for offset, callback in enumerate(
            [
                "onboard:setup",
                "onboard:calendar_auto",
                "onboard:service_auto",
                "onboard:reversible_auto",
                "onboard:spend_none",
                "onboard:boundary_continue",
                "onboard:activate",
            ],
            start=2,
        ):
            await client.post(
                "/telegram/webhook", json=callback_update(offset, callback), headers=headers
            )
        response = await client.post("/telegram/webhook", json=document_update, headers=headers)
    finally:
        await client.aclose()

    assert response.status_code == 200
    assert "LO351" in response.json()["text"]
    payload = response.json()
    button_texts = {button["text"] for button in payload.get("buttons", [])}
    button_texts.update(button["text"] for row in payload.get("button_rows", []) for button in row)
    assert "Save trip" in button_texts


async def test_unreadable_media_returns_a_recoverable_telegram_message() -> None:
    gateway = MediaTelegramGateway(content=b"not a ticket")
    client = await telegram_client(gateway)
    headers = {"X-Telegram-Bot-Api-Secret-Token": "test-secret-123456"}
    document_update = {
        "update_id": 32,
        "message": {
            "chat": {"id": 202},
            "from": {"id": 101},
            "document": {
                "file_id": "telegram-invalid-pdf",
                "file_name": "not-a-ticket.pdf",
                "mime_type": "application/pdf",
            },
        },
    }
    try:
        await client.post("/telegram/webhook", json=message_update(1, "/start"), headers=headers)
        for offset, callback in enumerate(
            [
                "onboard:setup",
                "onboard:calendar_auto",
                "onboard:service_auto",
                "onboard:reversible_auto",
                "onboard:spend_none",
                "onboard:boundary_continue",
                "onboard:activate",
            ],
            start=2,
        ):
            await client.post(
                "/telegram/webhook", json=callback_update(offset, callback), headers=headers
            )
        response = await client.post("/telegram/webhook", json=document_update, headers=headers)
    finally:
        await client.aclose()

    assert response.status_code == 200
    assert "could not read a flight or hotel" in response.json()["text"]
    assert "send the document or message again" in response.json()["text"].lower()


async def test_unsupported_media_explains_accepted_formats() -> None:
    gateway = MediaTelegramGateway()
    client = await telegram_client(gateway)
    headers = {"X-Telegram-Bot-Api-Secret-Token": "test-secret-123456"}
    update = {
        "update_id": 33,
        "message": {
            "chat": {"id": 202},
            "from": {"id": 101},
            "document": {
                "file_id": "telegram-unsupported",
                "file_name": "passport.zip",
                "mime_type": "application/octet-stream",
            },
        },
    }
    try:
        response = await client.post("/telegram/webhook", json=update, headers=headers)
    finally:
        await client.aclose()

    assert response.status_code == 200
    text = response.json()["text"]
    assert "PDF ticket" in text
    assert ".pkpass" in text
    assert "PNG/JPG/WebP" in text


async def test_webhook_rejects_malformed_oversized_and_unknown_updates() -> None:
    client = await telegram_client()
    headers = {
        "X-Telegram-Bot-Api-Secret-Token": "test-secret-123456",
        "Content-Type": "application/json",
    }
    try:
        malformed = await client.post("/telegram/webhook", content=b"{", headers=headers)
        unsupported = await client.post(
            "/telegram/webhook",
            json={"update_id": 1, "edited_message": {"text": "ignored"}},
            headers=headers,
        )
        oversized = await client.post(
            "/telegram/webhook",
            content=b"x" * (telegram_api.MAX_TELEGRAM_UPDATE_BYTES + 1),
            headers=headers,
        )
    finally:
        await client.aclose()

    assert malformed.status_code == 400
    assert unsupported.status_code == 400
    assert oversized.status_code == 413


async def test_update_id_collision_is_rejected_and_rate_limit_is_enforced(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(telegram_api, "TELEGRAM_UPDATES_PER_MINUTE", 2)
    client = await telegram_client()
    headers = {"X-Telegram-Bot-Api-Secret-Token": "test-secret-123456"}
    try:
        first = await client.post(
            "/telegram/webhook", json=message_update(1, "/start"), headers=headers
        )
        collision = await client.post(
            "/telegram/webhook", json=message_update(1, "/settings"), headers=headers
        )
        limited = await client.post(
            "/telegram/webhook", json=message_update(2, "/start"), headers=headers
        )
    finally:
        await client.aclose()

    assert first.status_code == 200
    assert collision.status_code == 409
    assert limited.status_code == 429


async def test_custom_spending_limit_and_policy_version_two_survive_settings_restart() -> None:
    client = await telegram_client()
    headers = {"X-Telegram-Bot-Api-Secret-Token": "test-secret-123456"}
    try:
        await client.post("/telegram/webhook", json=message_update(1, "/start"), headers=headers)
        for update_id, callback in enumerate(
            [
                "onboard:setup",
                "onboard:calendar_auto",
                "onboard:service_auto",
                "onboard:reversible_auto",
                "onboard:spend_custom",
            ],
            start=2,
        ):
            response = await client.post(
                "/telegram/webhook",
                json=callback_update(update_id, callback),
                headers=headers,
            )
            assert response.status_code == 200
        custom = await client.post(
            "/telegram/webhook", json=message_update(10, "/limit 37.50"), headers=headers
        )
        for update_id, callback in enumerate(
            ["onboard:boundary_continue", "onboard:activate"], start=11
        ):
            activated = await client.post(
                "/telegram/webhook",
                json=callback_update(update_id, callback),
                headers=headers,
            )
        await client.post(
            "/telegram/webhook", json=message_update(20, "/settings"), headers=headers
        )
        for update_id, callback in enumerate(
            [
                "onboard:restart",
                "onboard:calendar_ask",
                "onboard:service_ask",
                "onboard:reversible_ask",
                "onboard:spend_none",
                "onboard:boundary_continue",
                "onboard:activate",
            ],
            start=21,
        ):
            version_two = await client.post(
                "/telegram/webhook",
                json=callback_update(update_id, callback),
                headers=headers,
            )
        version_two_settings = await client.post(
            "/telegram/webhook", json=message_update(40, "/settings"), headers=headers
        )
    finally:
        await client.aclose()

    assert custom.status_code == 200
    assert "settings are active" in activated.json()["text"].lower()
    assert "settings are active" in version_two.json()["text"].lower()
    assert "disabled" in version_two_settings.json()["text"].lower()


async def test_settings_draft_does_not_mutate_active_policy_version() -> None:
    repository = InMemoryIncidentRepository()
    service = TelegramOnboardingService(repository)
    now = datetime(2026, 8, 17, tzinfo=UTC)
    await service.start(telegram_user_id="101", telegram_chat_id="202", now=now)
    for action in (
        "setup",
        "calendar_auto",
        "service_auto",
        "reversible_auto",
        "spend_20",
        "boundary_continue",
        "activate",
    ):
        await service.callback(
            telegram_user_id="101",
            telegram_chat_id="202",
            callback_data=f"onboard:{action}",
            now=now,
        )
    policy_one = await repository.get_traveler_policy(user_id="telegram:101", version=1)

    await service.callback(
        telegram_user_id="101",
        telegram_chat_id="202",
        callback_data="onboard:restart",
        now=now,
    )
    await service.callback(
        telegram_user_id="101",
        telegram_chat_id="202",
        callback_data="onboard:calendar_ask",
        now=now,
    )
    still_active = await repository.get_traveler_policy(user_id="telegram:101", version=1)
    traveler_draft = await repository.get_traveler("101")
    for action in (
        "service_ask",
        "reversible_ask",
        "spend_none",
        "boundary_continue",
        "activate",
    ):
        await service.callback(
            telegram_user_id="101",
            telegram_chat_id="202",
            callback_data=f"onboard:{action}",
            now=now,
        )
    policy_two = await repository.get_traveler_policy(user_id="telegram:101", version=2)

    assert policy_one is not None and policy_one.calendar_mode == PolicyMode.AUTO
    assert still_active == policy_one
    assert traveler_draft is not None and traveler_draft.calendar_mode == PolicyMode.ASK
    assert policy_two is not None and policy_two.calendar_mode == PolicyMode.ASK
