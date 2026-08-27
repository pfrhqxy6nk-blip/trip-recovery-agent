import httpx
from app.config import Settings
from app.edge import create_edge_app
from httpx import ASGITransport, AsyncClient


class StaticToken:
    async def token(self) -> str:
        return "oidc-test-token"


async def test_public_edge_verifies_secret_and_forwards_to_private_worker() -> None:
    captured: dict[str, object] = {}

    def worker(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["authorization"] = request.headers.get("authorization")
        captured["telegram_secret"] = request.headers.get("x-telegram-bot-api-secret-token")
        return httpx.Response(200, json={"text": "Set up your agent"})

    worker_client = httpx.AsyncClient(transport=httpx.MockTransport(worker))
    settings = Settings(
        pubsub_transport="local",
        telegram_webhook_secret="edge-webhook-secret",
        worker_base_url="https://private-worker.example",
    )
    app = create_edge_app(settings, client=worker_client, token_provider=StaticToken())
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://edge") as client:
            rejected = await client.post("/telegram/webhook", json={"update_id": 1})
            accepted = await client.post(
                "/telegram/webhook",
                json={"update_id": 1},
                headers={"X-Telegram-Bot-Api-Secret-Token": "edge-webhook-secret"},
            )
            internal = await client.post("/internal/pubsub/disruptions", json={})
    await worker_client.aclose()

    assert rejected.status_code == 401
    assert accepted.status_code == 200
    assert internal.status_code == 404
    assert captured == {
        "path": "/internal/telegram/webhook",
        "authorization": "Bearer oidc-test-token",
        "telegram_secret": "edge-webhook-secret",
    }


async def test_public_edge_serves_secure_connection_page_without_worker_data() -> None:
    worker_client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(500)))
    settings = Settings(
        pubsub_transport="local",
        worker_base_url="https://private-worker.example",
    )
    app = create_edge_app(settings, client=worker_client, token_provider=StaticToken())
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://edge") as client:
            page = await client.get("/connections/gemini")
    await worker_client.aclose()

    assert page.status_code == 200
    assert page.headers["cache-control"] == "no-store"
    assert "Connect Gemini securely" in page.text


async def test_public_edge_forwards_calendar_callback_query_to_private_worker() -> None:
    captured: dict[str, object] = {}

    def worker(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.raw_path.decode()
        captured["authorization"] = request.headers.get("authorization")
        return httpx.Response(200, text="connected")

    worker_client = httpx.AsyncClient(transport=httpx.MockTransport(worker))
    settings = Settings(
        pubsub_transport="local",
        worker_base_url="https://private-worker.example",
    )
    app = create_edge_app(settings, client=worker_client, token_provider=StaticToken())
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://edge") as client:
            response = await client.get("/connections/calendar/callback?code=one&state=two")
    await worker_client.aclose()

    assert response.status_code == 200
    assert captured == {
        "path": "/connections/calendar/callback?code=one&state=two",
        "authorization": "Bearer oidc-test-token",
    }
