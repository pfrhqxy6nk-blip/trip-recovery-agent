from __future__ import annotations

import hmac
import os

import functions_framework
import requests
from flask import Request, Response
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.id_token import fetch_id_token

MAX_BODY_BYTES = 1_000_000
ALLOWED_ROUTES = {
    ("GET", "gemini"): "/connections/gemini",
    ("POST", "gemini-complete"): "/connections/gemini/complete",
    # The Cloud Run worker deliberately exposes Telegram only behind its
    # authenticated /internal boundary.  Forwarding to the public path makes
    # the edge return the worker's 404, which Telegram reports as a failed
    # webhook and leaves the chat looking empty.
    ("POST", "telegram"): "/internal/telegram/webhook",
}


def _worker_base_url() -> str:
    value = os.environ.get("WORKER_BASE_URL", "").rstrip("/")
    if not value.startswith("https://"):
        raise RuntimeError("WORKER_BASE_URL must be configured with HTTPS")
    return value


def _telegram_secret() -> str:
    return os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")


def _response(content: bytes | str, status: int, content_type: str) -> Response:
    response = Response(content, status=status, content_type=content_type)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@functions_framework.http
def trip_recovery_edge(request: Request) -> Response:
    route_name = request.args.get("route", "")
    if request.method == "GET" and route_name == "health":
        return _response('{"status":"ok"}', 200, "application/json")

    route = ALLOWED_ROUTES.get((request.method, route_name))
    if route is None:
        return _response('{"detail":"not found"}', 404, "application/json")

    if request.method == "POST" and request.content_length is not None:
        if request.content_length > MAX_BODY_BYTES:
            return _response('{"detail":"request is too large"}', 413, "application/json")

    expected = _telegram_secret()
    if route == "/internal/telegram/webhook":
        received = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
        if not expected or received is None or not hmac.compare_digest(expected, received):
            return _response('{"detail":"invalid Telegram secret"}', 401, "application/json")

    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = request.stream.read(min(64 * 1024, MAX_BODY_BYTES - total + 1))
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_BODY_BYTES:
            return _response('{"detail":"request is too large"}', 413, "application/json")
        chunks.append(chunk)
    body = b"".join(chunks)

    worker_url = _worker_base_url()
    try:
        identity_token = fetch_id_token(GoogleAuthRequest(), worker_url)
        upstream = requests.request(
            request.method,
            f"{worker_url}{route}",
            data=body if request.method == "POST" else None,
            headers={
                "Authorization": f"Bearer {identity_token}",
                "Content-Type": request.headers.get("Content-Type", "application/json"),
                "X-Telegram-Bot-Api-Secret-Token": expected,
            },
            timeout=(3, 30),
        )
    except (requests.RequestException, ValueError):
        return _response('{"detail":"private worker is unavailable"}', 503, "application/json")

    content = upstream.content
    if route == "/connections/gemini" and upstream.status_code == 200:
        content = content.replace(
            b'fetch(location.pathname+"/complete",{',
            b'fetch(location.pathname+"?route=gemini-complete",{',
        )
    response = _response(
        content,
        upstream.status_code,
        upstream.headers.get("Content-Type", "application/json"),
    )
    for header in (
        "Content-Security-Policy",
        "Referrer-Policy",
        "X-Content-Type-Options",
    ):
        if header in upstream.headers:
            response.headers[header] = upstream.headers[header]
    return response
