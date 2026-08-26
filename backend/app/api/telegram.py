from __future__ import annotations

import hmac
import json
from datetime import datetime
from typing import cast

from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.api.routes import dispatch_pending_workflow_commands
from app.models.telegram import TelegramButton, TelegramView
from app.providers.telegram import TelegramGatewayError
from app.services.canonical_hash import canonical_hash
from app.services.onboarding import OnboardingError, TelegramOnboardingService
from app.services.ports import (
    EventPayloadConflict,
    SecretStore,
    TelegramGateway,
    TelegramMediaGateway,
)
from app.services.telegram_ai import (
    TelegramAiConnectionError,
    TelegramAiConnectionService,
)
from app.services.telegram_calendar import TelegramCalendarError, TelegramCalendarService
from app.services.telegram_conversation import TelegramConversationService
from app.services.telegram_demo import TelegramDemoError, TelegramDemoService
from app.services.telegram_gmail import TelegramGmailError, TelegramGmailService
from app.services.telegram_planning import TelegramPlanningError, TelegramPlanningService
from app.services.telegram_recovery import RecoveryInteractionError, TelegramRecoveryService
from app.services.telegram_trips import TelegramTripError, TelegramTripService

router = APIRouter(prefix="/telegram", tags=["telegram"])
MAX_TELEGRAM_UPDATE_BYTES = 64 * 1024
MAX_TELEGRAM_MEDIA_BYTES = 12 * 1024 * 1024
TELEGRAM_UPDATES_PER_MINUTE = 30
_ALLOWED_DOCUMENT_EXTENSIONS = {".pdf", ".pkpass", ".png", ".jpg", ".jpeg", ".webp"}
_ALLOWED_MIME_TYPES = {
    "application/pdf",
    "application/vnd.apple.pkpass",
    "application/zip",
    "image/jpeg",
    "image/png",
    "image/webp",
}


class TelegramActor(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    user_id: int = Field(alias="id")


class TelegramChat(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int


class TelegramPhotoSize(BaseModel):
    model_config = ConfigDict(extra="ignore")

    file_id: str
    file_unique_id: str | None = None
    width: int | None = None
    height: int | None = None
    file_size: int | None = None


class TelegramDocument(BaseModel):
    model_config = ConfigDict(extra="ignore")

    file_id: str
    file_name: str | None = None
    mime_type: str | None = None
    file_size: int | None = None


class TelegramMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    chat: TelegramChat
    from_user: TelegramActor = Field(alias="from")
    message_id: int | None = None
    text: str = ""
    caption: str = ""
    document: TelegramDocument | None = None
    photo: list[TelegramPhotoSize] = Field(default_factory=list)


class TelegramCallback(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = ""
    from_user: TelegramActor = Field(alias="from")
    message: TelegramMessage
    data: str = Field(min_length=1, max_length=64)


class TelegramUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    update_id: int
    message: TelegramMessage | None = None
    callback_query: TelegramCallback | None = None

    @model_validator(mode="after")
    def has_exactly_one_supported_update(self) -> TelegramUpdate:
        if (self.message is None) == (self.callback_query is None):
            raise ValueError("Telegram update must contain exactly one message or callback_query")
        return self


def _service(request: Request) -> TelegramOnboardingService:
    service = cast(TelegramOnboardingService | None, request.app.state.container.onboarding)
    if service is None:
        raise RuntimeError("Telegram onboarding service is unavailable")
    return service


def _gateway(request: Request) -> TelegramGateway | None:
    return cast(TelegramGateway | None, request.app.state.container.telegram_gateway)


def _media_gateway(request: Request) -> TelegramMediaGateway | None:
    gateway = _gateway(request)
    if gateway is None or not callable(getattr(gateway, "download_file", None)):
        return None
    return cast(TelegramMediaGateway, gateway)


def _recovery_service(request: Request) -> TelegramRecoveryService:
    service = cast(TelegramRecoveryService | None, request.app.state.container.telegram_recovery)
    if service is None:
        raise RuntimeError("Telegram recovery service is unavailable")
    return service


def _trip_service(request: Request) -> TelegramTripService:
    service = cast(TelegramTripService | None, request.app.state.container.telegram_trips)
    if service is None:
        raise RuntimeError("Telegram trip service is unavailable")
    return service


def _ai_service(request: Request) -> TelegramAiConnectionService:
    service = cast(TelegramAiConnectionService | None, request.app.state.container.telegram_ai)
    if service is None:
        raise TelegramAiConnectionError("Gemini connection service is not enabled")
    return service


def _calendar_service(request: Request) -> TelegramCalendarService:
    service = cast(TelegramCalendarService | None, request.app.state.container.telegram_calendar)
    if service is None:
        raise TelegramCalendarError("Calendar connection service is not enabled")
    return service


def _gmail_service(request: Request) -> TelegramGmailService:
    service = cast(TelegramGmailService | None, request.app.state.container.telegram_gmail)
    if service is None:
        raise TelegramGmailError("Gmail connection service is not enabled")
    return service


def _settings_view(
    view: TelegramView, *, calendar_available: bool, gmail_available: bool
) -> TelegramView:
    """Expose optional Google connections only inside the existing settings command."""

    connection_buttons = []
    if calendar_available:
        connection_buttons.append(
            TelegramButton(text="Google Calendar", callback_data="calendar:menu")
        )
    if gmail_available:
        connection_buttons.append(TelegramButton(text="Gmail drafts", callback_data="gmail:menu"))
    if not connection_buttons:
        return view
    return view.model_copy(update={"button_rows": [connection_buttons]})


def _planning_service(request: Request) -> TelegramPlanningService:
    service = cast(TelegramPlanningService | None, request.app.state.container.telegram_planning)
    if service is None:
        raise TelegramPlanningError("trip planning is not enabled")
    return service


def _demo_service(request: Request) -> TelegramDemoService:
    service = cast(TelegramDemoService | None, request.app.state.container.telegram_demo)
    if service is None:
        raise RuntimeError("Telegram demo service is unavailable")
    return service


def _conversation_service(request: Request) -> TelegramConversationService:
    service = cast(
        TelegramConversationService | None, request.app.state.container.telegram_conversation
    )
    if service is None:
        service = TelegramConversationService(request.app.state.container.repository)
        request.app.state.container.telegram_conversation = service
    return service


async def _delete_traveler_data(request: Request, telegram_user_id: str) -> TelegramView:
    """Delete the caller's persisted records and revoke any stored credentials."""

    resources = await request.app.state.container.repository.delete_traveler_data(
        telegram_user_id
    )
    stores: list[SecretStore] = []
    for store in (
        request.app.state.container.secret_store,
        request.app.state.container.connection_secret_store,
    ):
        if store is not None and all(store is not existing for existing in stores):
            stores.append(store)
    failed = 0
    for resource_name in resources:
        deleted = False
        for store in stores:
            try:
                await store.delete_secret(resource_name=resource_name)
                deleted = True
                break
            except Exception:
                continue
        if not deleted:
            failed += 1
    if failed:
        return TelegramView(
            text=(
                "Your trip data was removed, but one stored connection could not be revoked "
                "automatically. Please revoke that Google/Gemini connection in its account "
                "security settings."
            )
        )
    return TelegramView(
        text=(
            "Your Trip Watch data, trip documents, recovery history and stored connections "
            "were deleted. You can start again with /start."
        )
    )


def _media_descriptor(update: TelegramUpdate) -> tuple[str, str | None, str | None]:
    """Return file id/name/mime for the largest photo or supported document."""
    message = update.message
    if message is None:
        raise HTTPException(status_code=400, detail="media update has no message")
    if message.document is not None:
        document = message.document
        file_name = document.file_name
        suffix = ""
        if file_name:
            suffix = f".{file_name.rsplit('.', 1)[-1].lower()}" if "." in file_name else ""
        mime_type = (document.mime_type or "").lower() or None
        if mime_type not in _ALLOWED_MIME_TYPES and suffix not in _ALLOWED_DOCUMENT_EXTENSIONS:
            raise HTTPException(status_code=415, detail="unsupported itinerary document type")
        return document.file_id, file_name, mime_type
    if message.photo:
        photo = max(message.photo, key=lambda item: item.file_size or 0)
        return photo.file_id, None, "image/jpeg"
    raise HTTPException(status_code=400, detail="media update has no supported file")


async def _deliver_view(
    *, update: TelegramUpdate, view: TelegramView, gateway: TelegramGateway | None
) -> None:
    if gateway is None:
        return
    if update.callback_query is not None:
        callback = update.callback_query
        if callback.message.message_id is not None:
            await gateway.edit_message(
                chat_id=str(callback.message.chat.id),
                message_id=callback.message.message_id,
                view=view,
            )
            return
        await gateway.send_message(chat_id=str(callback.message.chat.id), view=view)
        return
    if update.message is not None:
        await gateway.send_message(chat_id=str(update.message.chat.id), view=view)


async def _ack_callback(update: TelegramUpdate, gateway: TelegramGateway | None) -> None:
    callback = update.callback_query
    if gateway is None or callback is None or not callback.id:
        return
    try:
        await gateway.answer_callback_query(callback_query_id=callback.id)
    except TelegramGatewayError:
        # A Telegram acknowledgement is best-effort and must not become recovery authority.
        # Durable callback claiming below still prevents duplicated state changes on retry.
        return


def _actor_and_kind(update: TelegramUpdate) -> tuple[str, str]:
    if update.callback_query is not None:
        return str(update.callback_query.from_user.user_id), "callback"
    if update.message is None:  # guarded by TelegramUpdate validation
        raise RuntimeError("validated Telegram update has no actor")
    return str(update.message.from_user.user_id), "message"


@router.post("/webhook", response_model=TelegramView)
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> TelegramView:
    expected = request.app.state.container.settings.telegram_webhook_secret
    if (
        not expected
        or x_telegram_bot_api_secret_token is None
        or not hmac.compare_digest(expected, x_telegram_bot_api_secret_token)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid Telegram secret"
        )

    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > MAX_TELEGRAM_UPDATE_BYTES:
                raise HTTPException(status_code=413, detail="Telegram update is too large")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid Content-Length") from exc
    body = await request.body()
    if len(body) > MAX_TELEGRAM_UPDATE_BYTES:
        raise HTTPException(status_code=413, detail="Telegram update is too large")
    try:
        update = TelegramUpdate.model_validate(json.loads(body))
    except (json.JSONDecodeError, UnicodeDecodeError, ValidationError) as exc:
        raise HTTPException(status_code=400, detail="malformed Telegram update") from exc

    gateway = _gateway(request)
    await _ack_callback(update, gateway)
    telegram_user_id, update_kind = _actor_and_kind(update)
    now = request.app.state.container.clock()
    rate_window = datetime.fromtimestamp(int(now.timestamp()) // 60 * 60, tz=now.tzinfo)
    allowed = await request.app.state.container.repository.claim_telegram_rate_slot(
        telegram_user_id=telegram_user_id,
        update_kind=update_kind,
        window_started_at=rate_window,
        limit=TELEGRAM_UPDATES_PER_MINUTE,
    )
    if not allowed:
        raise HTTPException(status_code=429, detail="Telegram update rate limit exceeded")

    # Approval callbacks are claimed atomically by consume_approval together with
    # the approval state change and resume outbox record. Pre-claiming them here
    # would make that transaction reject every legitimate approval.
    is_approval_callback = (
        update.callback_query is not None
        and update.callback_query.data.startswith(("a:", "s:", "f:", "r:"))
    )
    if not is_approval_callback:
        try:
            accepted = await request.app.state.container.repository.claim_telegram_update(
                update_id=str(update.update_id), payload_hash=canonical_hash(update)
            )
        except EventPayloadConflict as exc:
            raise HTTPException(status_code=409, detail="Telegram update ID conflict") from exc
        if not accepted:
            return TelegramView(text="This update was already handled.")

    service = _service(request)
    view = TelegramView(text="Use /start to set up your Trip Recovery Agent.")
    try:
        if update.message is not None and update.message.text == "/delete_my_data":
            view = await _delete_traveler_data(
                request, telegram_user_id=str(update.message.from_user.user_id)
            )
        elif update.message is not None and update.message.text == "/start":
            view = await service.start(
                telegram_user_id=str(update.message.from_user.user_id),
                telegram_chat_id=str(update.message.chat.id),
                now=request.app.state.container.clock(),
            )
        elif update.message is not None and update.message.text == "/demo":
            # Keep the normal user journey minimal while exposing an explicit,
            # judge-friendly entry point for the isolated Telegram recovery story.
            view = await _demo_service(request).handle(
                telegram_user_id=str(update.message.from_user.user_id),
                telegram_chat_id=str(update.message.chat.id),
                callback_data="demo:start",
                update_id=str(update.update_id),
                now=request.app.state.container.clock(),
            )
        elif update.message is not None and update.message.text == "/settings":
            view = await service.settings(
                telegram_user_id=str(update.message.from_user.user_id),
                telegram_chat_id=str(update.message.chat.id),
            )
            view = _settings_view(
                view,
                calendar_available=request.app.state.container.telegram_calendar is not None,
                gmail_available=request.app.state.container.telegram_gmail is not None,
            )
        elif update.message is not None and update.message.text.startswith("/limit"):
            view = await service.custom_spending_limit(
                telegram_user_id=str(update.message.from_user.user_id),
                telegram_chat_id=str(update.message.chat.id),
                command=update.message.text,
                now=now,
            )
        elif update.message is not None and update.message.text == "/addtrip":
            view = await _trip_service(request).handle(
                telegram_user_id=str(update.message.from_user.user_id),
                telegram_chat_id=str(update.message.chat.id),
                callback_data="trip:menu",
                now=now,
            )
        elif update.message is not None and TelegramTripService.looks_like_itinerary(
            update.message.text
        ):
            view = await _trip_service(request).handle_message(
                telegram_user_id=str(update.message.from_user.user_id),
                telegram_chat_id=str(update.message.chat.id),
                text=update.message.text,
                now=now,
            )
        elif update.message is not None and TelegramPlanningService.looks_like_planning(
            update.message.text
        ):
            view = await _planning_service(request).handle_message(
                telegram_user_id=str(update.message.from_user.user_id),
                telegram_chat_id=str(update.message.chat.id),
                text=update.message.text,
                now=now,
            )
        elif update.message is not None and (
            update.message.document is not None or update.message.photo
        ):
            gateway_for_media = _media_gateway(request)
            if gateway_for_media is None:
                raise HTTPException(status_code=503, detail="Telegram media gateway is unavailable")
            file_id, file_name, mime_type = _media_descriptor(update)
            downloaded = await gateway_for_media.download_file(
                file_id=file_id,
                file_name=file_name,
                mime_type=mime_type,
                max_bytes=MAX_TELEGRAM_MEDIA_BYTES,
            )
            raw_caption = update.message.caption or update.message.text
            view = await _trip_service(request).handle_media_message(
                telegram_user_id=str(update.message.from_user.user_id),
                telegram_chat_id=str(update.message.chat.id),
                media_bytes=downloaded.content,
                mime_type=downloaded.mime_type or mime_type or "application/octet-stream",
                caption=raw_caption,
                source_id=downloaded.file_id,
                source_name=downloaded.file_name,
                now=now,
            )
        elif update.message is not None:
            view = await _conversation_service(request).handle(
                telegram_user_id=str(update.message.from_user.user_id),
                telegram_chat_id=str(update.message.chat.id),
                text=update.message.text,
                now=now,
            )
        elif update.callback_query is not None:
            callback = update.callback_query
            if callback.data.startswith("demo:"):
                view = await _demo_service(request).handle(
                    telegram_user_id=str(callback.from_user.user_id),
                    telegram_chat_id=str(callback.message.chat.id),
                    callback_data=callback.data,
                    update_id=str(update.update_id),
                    now=request.app.state.container.clock(),
                )
            elif callback.data.startswith("chat:"):
                view = await _conversation_service(request).handle_callback(
                    telegram_user_id=str(callback.from_user.user_id),
                    telegram_chat_id=str(callback.message.chat.id),
                    callback_data=callback.data,
                )
            elif callback.data.startswith("trip:"):
                view = await _trip_service(request).handle(
                    telegram_user_id=str(callback.from_user.user_id),
                    telegram_chat_id=str(callback.message.chat.id),
                    callback_data=callback.data,
                    now=now,
                )
            elif callback.data.startswith("plan:"):
                view = await _planning_service(request).handle(
                    telegram_user_id=str(callback.from_user.user_id),
                    telegram_chat_id=str(callback.message.chat.id),
                    callback_data=callback.data,
                    now=now,
                )
            elif callback.data.startswith("ai:"):
                view = await _ai_service(request).handle(
                    telegram_user_id=str(callback.from_user.user_id),
                    telegram_chat_id=str(callback.message.chat.id),
                    callback_data=callback.data,
                    now=request.app.state.container.clock(),
                )
            elif callback.data.startswith("calendar:"):
                view = await _calendar_service(request).handle(
                    telegram_user_id=str(callback.from_user.user_id),
                    telegram_chat_id=str(callback.message.chat.id),
                    callback_data=callback.data,
                    now=request.app.state.container.clock(),
                )
            elif callback.data.startswith("gmail:"):
                view = await _gmail_service(request).handle(
                    telegram_user_id=str(callback.from_user.user_id),
                    telegram_chat_id=str(callback.message.chat.id),
                    callback_data=callback.data,
                    now=request.app.state.container.clock(),
                )
            elif callback.data.startswith("claim:"):
                view = await _recovery_service(request).claim_view(
                    incident_id=callback.data.removeprefix("claim:"),
                    telegram_user_id=str(callback.from_user.user_id),
                    telegram_chat_id=str(callback.message.chat.id),
                )
            elif callback.data.startswith(("a:", "d:", "c:", "s:", "f:", "r:")):
                view = await _recovery_service(request).handle_callback(
                    telegram_user_id=str(callback.from_user.user_id),
                    telegram_chat_id=str(callback.message.chat.id),
                    callback_data=callback.data,
                    update_id=str(update.update_id),
                    now=request.app.state.container.clock(),
                )
            else:
                view = await service.callback(
                    telegram_user_id=str(callback.from_user.user_id),
                    telegram_chat_id=str(callback.message.chat.id),
                    callback_data=callback.data,
                    now=request.app.state.container.clock(),
                )
    except TelegramPlanningError as exc:
        # User-input and planning-draft errors are recoverable conversation states.
        # Returning HTTP 400 here made Telegram retry the update without ever showing
        # the traveler what detail was missing, which looked like a blank bot. Keep the
        # message in English and deliver it through the normal Telegram view path.
        view = TelegramView(
            text=(
                "I couldn't continue planning yet.\n\n"
                f"{str(exc)}\n\n"
                "Send the missing trip detail in plain English and I will continue."
            )
        )
    except (
        OnboardingError,
        RecoveryInteractionError,
        TelegramTripError,
        TelegramAiConnectionError,
        TelegramCalendarError,
        TelegramGmailError,
        TelegramDemoError,
    ) as exc:
        # Telegram retries non-2xx webhook responses. For ordinary messages,
        # returning a small recoverable view is therefore safer for the
        # conversation than surfacing a bare 400 (which looks like a blank bot
        # and causes the same update to be delivered repeatedly). Callback
        # failures remain fail-closed so an invalid or cross-user action never
        # gets acknowledged as a successful state transition.
        if update_kind == "message":
            view = TelegramView(
                text=(
                    "I couldn't complete that yet.\n\n"
                    f"{str(exc)}\n\n"
                    "Send the document or message again, or use /start to restart setup."
                )
            )
        else:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except TelegramGatewayError as exc:
        status_code = 413 if exc.status_code == 413 else 502
        raise HTTPException(
            status_code=status_code, detail="Telegram media transfer failed"
        ) from exc
    await dispatch_pending_workflow_commands(request)
    await _deliver_view(update=update, view=view, gateway=gateway)
    return view
