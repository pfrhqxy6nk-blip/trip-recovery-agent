from app.config import Settings
from app.main import create_app
from app.runtime import create_runtime_app
from fastapi import FastAPI
from pytest import MonkeyPatch


def _paths(application: FastAPI) -> set[tuple[str, frozenset[str]]]:
    schema_paths = application.openapi()["paths"]
    return {
        (path, frozenset(method.upper() for method in operations))
        for path, operations in schema_paths.items()
    }


def test_worker_role_does_not_load_public_telegram_or_connection_page() -> None:
    application = create_app(Settings(pubsub_transport="local", app_role="worker"))
    paths = _paths(application)

    assert ("/internal/pubsub/disruptions", frozenset({"POST"})) in paths
    assert ("/internal/telegram/webhook", frozenset({"POST"})) in paths
    assert ("/connections/gemini/complete", frozenset({"POST"})) in paths
    assert all(path != "/telegram/webhook" for path, _ in paths)
    assert all(path != "/connections/gemini" for path, _ in paths)


def test_edge_role_loads_only_minimal_public_product_routes() -> None:
    application = create_runtime_app(
        Settings(
            pubsub_transport="local",
            app_role="edge",
            worker_base_url="https://private-worker.example",
            telegram_webhook_secret="edge-webhook-secret",
        )
    )
    product_paths = {
        path
        for path, _ in _paths(application)
        if path not in {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}
    }

    assert product_paths == {
        "/healthz",
        "/telegram/webhook",
        "/connections/gemini",
        "/connections/gemini/complete",
        "/connections/calendar/callback",
        "/connections/gmail/callback",
    }


def test_edge_role_requires_private_worker_and_webhook_secret() -> None:
    try:
        Settings(pubsub_transport="local", app_role="edge")
    except ValueError as exc:
        assert "WORKER_BASE_URL" in str(exc)
    else:
        raise AssertionError("edge role accepted without a worker URL")


def test_all_role_is_rejected_for_google_transport() -> None:
    try:
        Settings(pubsub_transport="google", app_role="all", process_events_inline=False)
    except ValueError as exc:
        assert "APP_ROLE=all is local-only" in str(exc)
    else:
        raise AssertionError("APP_ROLE=all accepted for Google transport")


def test_all_role_is_rejected_inside_cloud_run(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("K_SERVICE", "trip-recovery-agent")
    try:
        Settings(pubsub_transport="local", app_role="all", process_events_inline=True)
    except ValueError as exc:
        assert "APP_ROLE=all is local-only" in str(exc)
    else:
        raise AssertionError("APP_ROLE=all accepted inside Cloud Run")
