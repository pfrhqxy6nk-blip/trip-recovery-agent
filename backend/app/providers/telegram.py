from __future__ import annotations

from enum import StrEnum
from typing import Any

import httpx

from app.models.telegram import TelegramFileDownload, TelegramMessageReceipt, TelegramView


class TelegramRetryClass(StrEnum):
    """Whether a durable caller may safely retry a Telegram operation."""

    SAFE_RETRY = "SAFE_RETRY"
    UNKNOWN_OUTCOME = "UNKNOWN_OUTCOME"
    TERMINAL = "TERMINAL"


class TelegramGatewayError(RuntimeError):
    """Sanitized provider failure that never contains the bot token or response body."""

    def __init__(
        self,
        *,
        operation: str,
        retry_class: TelegramRetryClass,
        status_code: int | None = None,
        retry_after_seconds: int | None = None,
    ) -> None:
        self.operation = operation
        self.retry_class = retry_class
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds
        status = f", status={status_code}" if status_code is not None else ""
        super().__init__(f"Telegram {operation} failed ({retry_class.value}{status})")


class TelegramBotApiGateway:
    """Thin async Telegram Bot API adapter with explicit retry semantics.

    Telegram does not accept caller-provided idempotency keys. The adapter therefore
    performs one HTTP attempt only. A durable workflow can retry SAFE_RETRY failures;
    UNKNOWN_OUTCOME must be reconciled rather than blindly repeating ``sendMessage``.
    """

    _DEFAULT_TIMEOUT = httpx.Timeout(connect=3.0, read=10.0, write=10.0, pool=3.0)

    def __init__(
        self,
        *,
        bot_token: str,
        client: httpx.AsyncClient,
        timeout: httpx.Timeout | None = None,
        api_base_url: str = "https://api.telegram.org",
    ) -> None:
        if not bot_token.strip():
            raise ValueError("Telegram bot token must be configured")
        self._client = client
        self._timeout = timeout or self._DEFAULT_TIMEOUT
        self._api_base_url = api_base_url.rstrip("/")
        self._endpoint = f"{self._api_base_url}/bot{bot_token}"

    async def send_message(self, *, chat_id: str, view: TelegramView) -> TelegramMessageReceipt:
        result = await self._post(
            "sendMessage",
            operation="send_message",
            payload=self._message_payload(chat_id=chat_id, view=view),
        )
        return self._message_receipt(result, fallback_chat_id=chat_id, operation="send_message")

    async def edit_message(
        self, *, chat_id: str, message_id: int, view: TelegramView
    ) -> TelegramMessageReceipt:
        payload = self._message_payload(chat_id=chat_id, view=view)
        payload["message_id"] = message_id
        try:
            result = await self._post(
                "editMessageText",
                operation="edit_message",
                payload=payload,
                message_not_modified_is_success=True,
            )
        except _MessageNotModified:
            return TelegramMessageReceipt(chat_id=chat_id, message_id=message_id)
        return self._message_receipt(result, fallback_chat_id=chat_id, operation="edit_message")

    async def answer_callback_query(
        self,
        *,
        callback_query_id: str,
        text: str | None = None,
        show_alert: bool = False,
    ) -> None:
        if not callback_query_id:
            raise ValueError("callback_query_id must not be empty")
        payload: dict[str, object] = {
            "callback_query_id": callback_query_id,
            "show_alert": show_alert,
        }
        if text is not None:
            if len(text) > 200:
                raise ValueError("Telegram callback answer text must not exceed 200 characters")
            payload["text"] = text
        result = await self._post(
            "answerCallbackQuery",
            operation="answer_callback_query",
            payload=payload,
        )
        if result is not True:
            raise TelegramGatewayError(
                operation="answer_callback_query",
                retry_class=TelegramRetryClass.UNKNOWN_OUTCOME,
            )

    async def download_file(
        self,
        *,
        file_id: str,
        file_name: str | None,
        mime_type: str | None,
        max_bytes: int,
    ) -> TelegramFileDownload:
        """Resolve a Telegram file id and download it with a hard byte ceiling.

        Telegram's file path contains no user data beyond the provider path. The bot
        token remains confined to the adapter endpoint and is never included in
        errors or persisted metadata.
        """
        if not file_id:
            raise ValueError("file_id must not be empty")
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        result = await self._post("getFile", operation="get_file", payload={"file_id": file_id})
        if not isinstance(result, dict) or not isinstance(result.get("file_path"), str):
            raise TelegramGatewayError(
                operation="get_file", retry_class=TelegramRetryClass.UNKNOWN_OUTCOME
            )
        file_path = result["file_path"]
        try:
            async with self._client.stream(
                "GET",
                f"{self._api_base_url}/file/{self._endpoint.rsplit('/', 1)[-1]}/{file_path}",
                timeout=self._timeout,
            ) as response:
                if not response.is_success:
                    retry_class = (
                        TelegramRetryClass.UNKNOWN_OUTCOME
                        if response.status_code >= 500
                        else TelegramRetryClass.TERMINAL
                    )
                    raise TelegramGatewayError(
                        operation="download_file",
                        retry_class=retry_class,
                        status_code=response.status_code,
                    )
                content_length = response.headers.get("content-length")
                if content_length is not None:
                    try:
                        if int(content_length) > max_bytes:
                            raise TelegramGatewayError(
                                operation="download_file",
                                retry_class=TelegramRetryClass.TERMINAL,
                                status_code=413,
                            )
                    except ValueError:
                        pass
                chunks: list[bytes] = []
                total = 0
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        raise TelegramGatewayError(
                            operation="download_file",
                            retry_class=TelegramRetryClass.TERMINAL,
                            status_code=413,
                        )
                    chunks.append(chunk)
        except (httpx.ConnectTimeout, httpx.ConnectError, httpx.PoolTimeout):
            raise TelegramGatewayError(
                operation="download_file", retry_class=TelegramRetryClass.SAFE_RETRY
            ) from None
        except (httpx.ReadTimeout, httpx.WriteTimeout, httpx.ReadError, httpx.WriteError):
            raise TelegramGatewayError(
                operation="download_file", retry_class=TelegramRetryClass.UNKNOWN_OUTCOME
            ) from None
        except httpx.RequestError:
            raise TelegramGatewayError(
                operation="download_file", retry_class=TelegramRetryClass.UNKNOWN_OUTCOME
            ) from None
        content = b"".join(chunks)
        if not content:
            raise TelegramGatewayError(
                operation="download_file", retry_class=TelegramRetryClass.TERMINAL, status_code=204
            )
        return TelegramFileDownload(
            file_id=file_id, file_name=file_name, mime_type=mime_type, content=content
        )

    @staticmethod
    def _message_payload(*, chat_id: str, view: TelegramView) -> dict[str, object]:
        if not chat_id:
            raise ValueError("chat_id must not be empty")
        payload: dict[str, object] = {"chat_id": chat_id, "text": view.text}
        if view.parse_mode is not None:
            payload["parse_mode"] = view.parse_mode
        rows = view.inline_keyboard()
        if rows:
            payload["reply_markup"] = {
                "inline_keyboard": [
                    [button.model_dump(mode="json", exclude_none=True) for button in row]
                    for row in rows
                ]
            }
        return payload

    async def _post(
        self,
        method: str,
        *,
        operation: str,
        payload: dict[str, object],
        message_not_modified_is_success: bool = False,
    ) -> Any:
        try:
            response = await self._client.post(
                f"{self._endpoint}/{method}", json=payload, timeout=self._timeout
            )
        except (httpx.ConnectTimeout, httpx.ConnectError, httpx.PoolTimeout):
            raise TelegramGatewayError(
                operation=operation, retry_class=TelegramRetryClass.SAFE_RETRY
            ) from None
        except (httpx.ReadTimeout, httpx.WriteTimeout, httpx.ReadError, httpx.WriteError):
            raise TelegramGatewayError(
                operation=operation, retry_class=TelegramRetryClass.UNKNOWN_OUTCOME
            ) from None
        except httpx.RequestError:
            raise TelegramGatewayError(
                operation=operation, retry_class=TelegramRetryClass.UNKNOWN_OUTCOME
            ) from None

        body = self._json_object(response, operation=operation)
        if response.is_success and body.get("ok") is True and "result" in body:
            return body["result"]

        description = body.get("description")
        if (
            message_not_modified_is_success
            and response.status_code == 400
            and isinstance(description, str)
            and "message is not modified" in description.lower()
        ):
            raise _MessageNotModified

        retry_after = self._retry_after(body)
        if response.status_code == 429:
            retry_class = TelegramRetryClass.SAFE_RETRY
        elif response.is_success or response.status_code >= 500:
            # Telegram may have accepted a mutation before returning a server error.
            retry_class = TelegramRetryClass.UNKNOWN_OUTCOME
        else:
            retry_class = TelegramRetryClass.TERMINAL
        raise TelegramGatewayError(
            operation=operation,
            retry_class=retry_class,
            status_code=response.status_code,
            retry_after_seconds=retry_after,
        )

    @staticmethod
    def _json_object(response: httpx.Response, *, operation: str) -> dict[str, Any]:
        try:
            body = response.json()
        except ValueError as exc:
            retry_class = (
                TelegramRetryClass.UNKNOWN_OUTCOME
                if response.is_success or response.status_code >= 500
                else TelegramRetryClass.TERMINAL
            )
            raise TelegramGatewayError(
                operation=operation,
                retry_class=retry_class,
                status_code=response.status_code,
            ) from exc
        if not isinstance(body, dict):
            raise TelegramGatewayError(
                operation=operation,
                retry_class=TelegramRetryClass.UNKNOWN_OUTCOME,
                status_code=response.status_code,
            )
        return body

    @staticmethod
    def _retry_after(body: dict[str, Any]) -> int | None:
        parameters = body.get("parameters")
        if not isinstance(parameters, dict):
            return None
        value = parameters.get("retry_after")
        return value if isinstance(value, int) and value >= 0 else None

    @staticmethod
    def _message_receipt(
        result: Any, *, fallback_chat_id: str, operation: str
    ) -> TelegramMessageReceipt:
        if not isinstance(result, dict):
            raise TelegramGatewayError(
                operation=operation, retry_class=TelegramRetryClass.UNKNOWN_OUTCOME
            )
        message_id = result.get("message_id")
        chat = result.get("chat")
        chat_id: object = fallback_chat_id
        if isinstance(chat, dict):
            chat_id = chat.get("id", fallback_chat_id)
        date = result.get("date")
        if not isinstance(message_id, int) or isinstance(message_id, bool):
            raise TelegramGatewayError(
                operation=operation, retry_class=TelegramRetryClass.UNKNOWN_OUTCOME
            )
        normalized_date = date if isinstance(date, int) and not isinstance(date, bool) else None
        return TelegramMessageReceipt(
            chat_id=str(chat_id), message_id=message_id, date=normalized_date
        )


class _MessageNotModified(Exception):
    pass
