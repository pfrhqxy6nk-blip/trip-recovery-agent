from __future__ import annotations

import hmac
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Protocol

import httpx
from fastapi import FastAPI, Header, HTTPException, Request, Response

from app.api.connections import connection_page
from app.config import Settings, get_settings
from app.logging import configure_logging
from app.providers.worker_proxy import CloudRunIdTokenProvider


class IdTokenProvider(Protocol):
    async def token(self) -> str: ...


def create_edge_app(
    settings: Settings | None = None,
    *,
    client: httpx.AsyncClient | None = None,
    token_provider: IdTokenProvider | None = None,
) -> FastAPI:
    resolved = settings or get_settings()
    injected_client = client

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        configure_logging(resolved.log_level)
        if not resolved.worker_base_url:
            raise RuntimeError("WORKER_BASE_URL is required for the public edge")
        owned_client = injected_client is None
        application.state.client = injected_client or httpx.AsyncClient(
            timeout=httpx.Timeout(connect=3.0, read=30.0, write=10.0, pool=3.0)
        )
        application.state.tokens = token_provider or CloudRunIdTokenProvider(
            resolved.worker_base_url
        )
        try:
            yield
        finally:
            if owned_client:
                await application.state.client.aclose()

    application = FastAPI(title="Trip Recovery Agent Edge", lifespan=lifespan)

    @application.middleware("http")
    async def security_headers(request: Request, call_next: Any) -> Response:
        response = await call_next(request)
        if request.url.scheme == "https":
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        return response  # type: ignore[no-any-return]

    @application.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/connections/gemini")
    async def gemini_connection_page() -> Response:
        return await connection_page()

    async def forward(request: Request, path: str) -> Response:
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                if int(content_length) > 1_000_000:
                    raise HTTPException(status_code=413, detail="request is too large")
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="invalid Content-Length") from exc
        chunks: list[bytes] = []
        total = 0
        async for chunk in request.stream():
            total += len(chunk)
            if total > 1_000_000:
                raise HTTPException(status_code=413, detail="request is too large")
            chunks.append(chunk)
        body = b"".join(chunks)
        try:
            oidc_token = await request.app.state.tokens.token()
            upstream = await request.app.state.client.post(
                f"{resolved.worker_base_url.rstrip('/')}{path}",
                content=body,
                headers={
                    "authorization": f"Bearer {oidc_token}",
                    "content-type": request.headers.get("content-type", "application/json"),
                    "x-telegram-bot-api-secret-token": resolved.telegram_webhook_secret,
                },
            )
        except (httpx.RequestError, ValueError):
            raise HTTPException(status_code=503, detail="private worker is unavailable") from None
        return Response(
            content=upstream.content,
            status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type", "application/json"),
        )

    async def forward_get(request: Request, path: str) -> Response:
        try:
            oidc_token = await request.app.state.tokens.token()
            upstream = await request.app.state.client.get(
                f"{resolved.worker_base_url.rstrip('/')}{path}",
                headers={"authorization": f"Bearer {oidc_token}"},
            )
        except (httpx.RequestError, ValueError):
            raise HTTPException(status_code=503, detail="private worker is unavailable") from None
        return Response(
            content=upstream.content,
            status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type", "text/html"),
        )

    @application.post("/telegram/webhook")
    async def telegram_webhook(
        request: Request,
        x_telegram_bot_api_secret_token: str | None = Header(default=None),
    ) -> Response:
        expected = resolved.telegram_webhook_secret
        if (
            not expected
            or x_telegram_bot_api_secret_token is None
            or not hmac.compare_digest(expected, x_telegram_bot_api_secret_token)
        ):
            raise HTTPException(status_code=401, detail="invalid Telegram secret")
        return await forward(request, "/internal/telegram/webhook")

    @application.post("/connections/gemini/complete")
    async def complete_gemini_connection(request: Request) -> Response:
        return await forward(request, "/connections/gemini/complete")

    @application.get("/connections/calendar/callback")
    async def calendar_callback(request: Request) -> Response:
        query = request.url.query
        path = "/connections/calendar/callback"
        if query:
            path = f"{path}?{query}"
        return await forward_get(request, path)

    @application.get("/connections/gmail/callback")
    async def gmail_callback(request: Request) -> Response:
        query = request.url.query
        path = "/connections/gmail/callback"
        if query:
            path = f"{path}?{query}"
        return await forward_get(request, path)

    return application


app = create_edge_app()
