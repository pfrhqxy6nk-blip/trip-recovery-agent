from __future__ import annotations

from typing import cast

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, SecretStr

from app.models.ai_connection import AiConnectionStatus
from app.services.ai_connections import AiConnectionError, AiConnectionService
from app.services.calendar_oauth import CalendarOAuthError, CalendarOAuthService
from app.services.gmail_oauth import GmailOAuthError, GmailOAuthService

page_router = APIRouter(prefix="/connections/gemini", tags=["connections"])
worker_router = APIRouter(prefix="/connections/gemini", tags=["connections"])
router = APIRouter()
calendar_worker_router = APIRouter(prefix="/connections/calendar", tags=["connections"])
gmail_worker_router = APIRouter(prefix="/connections/gmail", tags=["connections"])


@page_router.get("", response_class=HTMLResponse)
async def connection_page() -> HTMLResponse:
    return HTMLResponse(
        content="""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Connect Gemini</title></head><body>
<main><h1>Connect Gemini securely</h1>
<p>Paste your Gemini API key here. Never send it in Telegram or email.</p>
<form id="connect"><input id="credential" type="password" autocomplete="off" required>
<button type="submit">Connect Gemini</button></form><p id="status"></p></main>
<script>
const values=new URLSearchParams(location.hash.slice(1));
history.replaceState(null,"",location.pathname);
document.getElementById("connect").addEventListener("submit",async(event)=>{
 event.preventDefault();
 const status=document.getElementById("status"); status.textContent="Connecting…";
 const response=await fetch(location.pathname+"/complete",{
 method:"POST",headers:{"content-type":"application/json"},
 body:JSON.stringify({token:values.get("token"),telegram_user_id:values.get("telegram_user_id"),
 telegram_chat_id:values.get("telegram_chat_id"),api_key:document.getElementById("credential").value})});
 document.getElementById("credential").value="";
 status.textContent=response.ok
  ?"Gemini connected. You may close this page."
  :"Connection failed. Return to Telegram and request a new link.";
});
</script></body></html>""",
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": (
                "default-src 'self'; script-src 'unsafe-inline'; frame-ancestors 'none'"
            ),
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
        },
    )


class CompleteGeminiConnection(BaseModel):
    token: SecretStr
    telegram_user_id: str
    telegram_chat_id: str
    api_key: SecretStr


class GeminiConnectionResponse(BaseModel):
    status: AiConnectionStatus
    key_fingerprint: str | None = None


@worker_router.post("/complete", response_model=GeminiConnectionResponse)
async def complete_connection(
    payload: CompleteGeminiConnection, request: Request
) -> GeminiConnectionResponse:
    service = cast(AiConnectionService | None, request.app.state.container.ai_connections)
    if service is None:
        raise HTTPException(status_code=503, detail="Gemini connection service is unavailable")
    try:
        connection = await service.complete(
            token=payload.token.get_secret_value(),
            telegram_user_id=payload.telegram_user_id,
            telegram_chat_id=payload.telegram_chat_id,
            api_key=payload.api_key.get_secret_value(),
            now=request.app.state.container.clock(),
        )
    except AiConnectionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return GeminiConnectionResponse(
        status=connection.status, key_fingerprint=connection.key_fingerprint
    )


@calendar_worker_router.get("/callback", response_class=HTMLResponse)
async def calendar_callback(request: Request) -> HTMLResponse:
    """Finish Google OAuth without reflecting tokens or state into the page."""

    service = cast(CalendarOAuthService | None, request.app.state.container.calendar_oauth)
    if service is None:
        raise HTTPException(status_code=503, detail="Calendar connection service is unavailable")
    code = request.query_params.get("code", "")
    state = request.query_params.get("state", "")
    error = request.query_params.get("error")
    if error:
        return HTMLResponse(
            content=(
                "<h1>Calendar connection cancelled</h1><p>Return to Telegram and try again.</p>"
            ),
            status_code=400,
            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
        )
    try:
        await service.complete_callback(
            code=code,
            state=state,
            now=request.app.state.container.clock(),
        )
    except CalendarOAuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return HTMLResponse(
        content=(
            "<!doctype html><meta charset='utf-8'><title>Calendar connected</title>"
            "<main><h1>Google Calendar connected</h1>"
            "<p>Trip Watch can now update permitted events and verify every change."
            " Return to Telegram.</p></main>"
        ),
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
        },
    )


@gmail_worker_router.get("/callback", response_class=HTMLResponse)
async def gmail_callback(request: Request) -> HTMLResponse:
    """Finish Gmail OAuth without exposing the code, state, or refresh token."""

    service = cast(GmailOAuthService | None, request.app.state.container.gmail_oauth)
    if service is None:
        raise HTTPException(status_code=503, detail="Gmail connection service is unavailable")
    code = request.query_params.get("code", "")
    state = request.query_params.get("state", "")
    if request.query_params.get("error"):
        return HTMLResponse(
            content="<h1>Gmail connection cancelled</h1><p>Return to Telegram and try again.</p>",
            status_code=400,
            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
        )
    try:
        await service.complete_callback(
            code=code,
            state=state,
            now=request.app.state.container.clock(),
        )
    except GmailOAuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return HTMLResponse(
        content=(
            "<!doctype html><meta charset='utf-8'><title>Gmail connected</title>"
            "<main><h1>Gmail connected</h1><p>Trip Watch may now create a reviewable "
            "late-arrival draft. It will never send email automatically. "
            "Return to Telegram.</p></main>"
        ),
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
        },
    )


router.include_router(page_router)
router.include_router(worker_router)
router.include_router(calendar_worker_router)
router.include_router(gmail_worker_router)
