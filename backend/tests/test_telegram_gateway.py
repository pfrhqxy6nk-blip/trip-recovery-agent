from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest
from app.models.telegram import TelegramButton, TelegramView
from app.providers.telegram import (
    TelegramBotApiGateway,
    TelegramGatewayError,
    TelegramRetryClass,
)
from pydantic import ValidationError

Handler = Callable[[httpx.Request], httpx.Response]


async def _client(handler: Handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_send_message_maps_structured_keyboard_and_returns_receipt() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["payload"] = request.read()
        captured["timeout"] = request.extensions["timeout"]
        return httpx.Response(
            200,
            json={
                "ok": True,
                "result": {"message_id": 41, "date": 1_777_000_000, "chat": {"id": 202}},
            },
        )

    client = await _client(handler)
    try:
        gateway = TelegramBotApiGateway(bot_token="123:secret-token", client=client)
        view = TelegramView(
            text="Recovery needs approval",
            button_rows=[
                [
                    TelegramButton(text="Approve", callback_data="a:opaque"),
                    TelegramButton(text="Details", callback_data="d:opaque"),
                ],
                [TelegramButton(text="Stop", callback_data="s:opaque")],
            ],
        )

        receipt = await gateway.send_message(chat_id="202", view=view)

        assert captured["path"] == "/bot123:secret-token/sendMessage"
        assert httpx.Response(200, content=captured["payload"]).json() == {
            "chat_id": "202",
            "text": "Recovery needs approval",
            "reply_markup": {
                "inline_keyboard": [
                    [
                        {"text": "Approve", "callback_data": "a:opaque"},
                        {"text": "Details", "callback_data": "d:opaque"},
                    ],
                    [{"text": "Stop", "callback_data": "s:opaque"}],
                ]
            },
        }
        assert captured["timeout"] == {
            "connect": 3.0,
            "read": 10.0,
            "write": 10.0,
            "pool": 3.0,
        }
        assert receipt.chat_id == "202"
        assert receipt.message_id == 41
        assert receipt.date == 1_777_000_000
    finally:
        await client.aclose()


async def test_flat_buttons_map_to_one_compatibility_row() -> None:
    payload: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        payload.update(httpx.Response(200, content=request.read()).json())
        return httpx.Response(
            200, json={"ok": True, "result": {"message_id": 8, "chat": {"id": "chat"}}}
        )

    client = await _client(handler)
    try:
        gateway = TelegramBotApiGateway(bot_token="token", client=client)
        await gateway.send_message(
            chat_id="chat",
            view=TelegramView(
                text="Choose",
                buttons=[TelegramButton(text="Continue", callback_data="onboard:continue")],
            ),
        )
    finally:
        await client.aclose()

    assert payload["reply_markup"]["inline_keyboard"] == [
        [{"text": "Continue", "callback_data": "onboard:continue"}]
    ]


async def test_html_parse_mode_is_sent_only_when_requested() -> None:
    payload: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        payload.update(httpx.Response(200, content=request.read()).json())
        return httpx.Response(
            200, json={"ok": True, "result": {"message_id": 9, "chat": {"id": "chat"}}}
        )

    client = await _client(handler)
    try:
        gateway = TelegramBotApiGateway(bot_token="token", client=client)
        await gateway.send_message(
            chat_id="chat", view=TelegramView(text="<b>Ready</b>", parse_mode="HTML")
        )
    finally:
        await client.aclose()

    assert payload["parse_mode"] == "HTML"


async def test_edit_message_treats_message_not_modified_as_idempotent_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/editMessageText")
        return httpx.Response(
            400,
            json={"ok": False, "description": "Bad Request: message is not modified"},
        )

    client = await _client(handler)
    try:
        gateway = TelegramBotApiGateway(bot_token="token", client=client)
        receipt = await gateway.edit_message(
            chat_id="202", message_id=99, view=TelegramView(text="Already current")
        )
    finally:
        await client.aclose()

    assert receipt.chat_id == "202"
    assert receipt.message_id == 99


async def test_answer_callback_query_sends_prompt_acknowledgement_shape() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(httpx.Response(200, content=request.read()).json())
        return httpx.Response(200, json={"ok": True, "result": True})

    client = await _client(handler)
    try:
        gateway = TelegramBotApiGateway(bot_token="token", client=client)
        await gateway.answer_callback_query(
            callback_query_id="callback-1", text="Approved", show_alert=False
        )
    finally:
        await client.aclose()

    assert captured == {
        "callback_query_id": "callback-1",
        "show_alert": False,
        "text": "Approved",
    }


async def test_download_file_resolves_path_and_enforces_byte_limit() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/getFile"):
            return httpx.Response(
                200, json={"ok": True, "result": {"file_path": "docs/ticket.pdf"}}
            )
        return httpx.Response(200, headers={"content-length": "7"}, content=b"%PDF-1")

    client = await _client(handler)
    try:
        gateway = TelegramBotApiGateway(
            bot_token="super-secret-token", client=client, api_base_url="https://telegram.test"
        )
        downloaded = await gateway.download_file(
            file_id="file-1", file_name="ticket.pdf", mime_type="application/pdf", max_bytes=16
        )
    finally:
        await client.aclose()

    assert calls == [
        "/botsuper-secret-token/getFile",
        "/file/botsuper-secret-token/docs/ticket.pdf",
    ]
    assert downloaded.content == b"%PDF-1"
    assert downloaded.file_name == "ticket.pdf"


async def test_download_file_rejects_oversized_content_without_leaking_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getFile"):
            return httpx.Response(200, json={"ok": True, "result": {"file_path": "ticket.pdf"}})
        return httpx.Response(200, headers={"content-length": "100"}, content=b"too large")

    client = await _client(handler)
    try:
        gateway = TelegramBotApiGateway(bot_token="super-secret-token", client=client)
        with pytest.raises(TelegramGatewayError) as caught:
            await gateway.download_file(
                file_id="file-1", file_name="ticket.pdf", mime_type="application/pdf", max_bytes=16
            )
    finally:
        await client.aclose()

    assert caught.value.status_code == 413
    assert "super-secret-token" not in str(caught.value)


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (httpx.ConnectTimeout("connect failed"), TelegramRetryClass.SAFE_RETRY),
        (httpx.ReadTimeout("response lost"), TelegramRetryClass.UNKNOWN_OUTCOME),
    ],
)
async def test_network_failures_are_sanitized_and_not_retried_blindly(
    failure: httpx.RequestError, expected: TelegramRetryClass
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        failure.request = request
        raise failure

    client = await _client(handler)
    try:
        gateway = TelegramBotApiGateway(bot_token="super-secret-token", client=client)
        with pytest.raises(TelegramGatewayError) as caught:
            await gateway.send_message(chat_id="202", view=TelegramView(text="Status"))
    finally:
        await client.aclose()

    assert calls == 1
    assert caught.value.retry_class == expected
    assert "super-secret-token" not in str(caught.value)
    assert "super-secret-token" not in repr(caught.value)


@pytest.mark.parametrize(
    ("status_code", "body", "expected", "retry_after"),
    [
        (
            429,
            {"ok": False, "parameters": {"retry_after": 7}},
            TelegramRetryClass.SAFE_RETRY,
            7,
        ),
        (500, {"ok": False}, TelegramRetryClass.UNKNOWN_OUTCOME, None),
        (403, {"ok": False}, TelegramRetryClass.TERMINAL, None),
    ],
)
async def test_api_failure_retry_classification(
    status_code: int,
    body: dict[str, object],
    expected: TelegramRetryClass,
    retry_after: int | None,
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status_code, json=body)

    client = await _client(handler)
    try:
        gateway = TelegramBotApiGateway(bot_token="token", client=client)
        with pytest.raises(TelegramGatewayError) as caught:
            await gateway.send_message(chat_id="202", view=TelegramView(text="Status"))
    finally:
        await client.aclose()

    assert calls == 1
    assert caught.value.retry_class == expected
    assert caught.value.retry_after_seconds == retry_after


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, content=b"not-json"),
        httpx.Response(200, json={"ok": True}),
        httpx.Response(200, json=["unexpected"]),
    ],
)
async def test_malformed_success_response_is_unknown_outcome(
    response: httpx.Response,
) -> None:
    client = await _client(lambda _: response)
    try:
        gateway = TelegramBotApiGateway(bot_token="token", client=client)
        with pytest.raises(TelegramGatewayError) as caught:
            await gateway.send_message(chat_id="202", view=TelegramView(text="Status"))
    finally:
        await client.aclose()

    assert caught.value.retry_class == TelegramRetryClass.UNKNOWN_OUTCOME


def test_callback_data_enforces_telegram_utf8_byte_limit() -> None:
    TelegramButton(text="OK", callback_data="x" * 64)

    with pytest.raises(ValidationError, match="64 UTF-8 bytes"):
        TelegramButton(text="Too long", callback_data="€" * 22)


def test_view_rejects_ambiguous_keyboard_shapes() -> None:
    button = TelegramButton(text="OK", callback_data="ok")

    with pytest.raises(ValidationError, match="either buttons or button_rows"):
        TelegramView(text="Choose", buttons=[button], button_rows=[[button]])
