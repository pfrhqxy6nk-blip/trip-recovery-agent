from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parents[2]


def _read(name: str) -> str:
    return ROOT.joinpath("infra", "cloudrun", name).read_text()


def test_root_dockerfile_uses_role_selecting_runtime() -> None:
    source = ROOT.joinpath("Dockerfile").read_text()

    assert "PYTHONPATH=/app/backend" in source
    assert "uvicorn app.runtime:app" in source
    assert "uvicorn app.main:app" not in source


def test_worker_deploy_keeps_autonomous_runtime_and_secret_contract() -> None:
    source = _read("deploy-trip-watch-worker.sh")

    # A fresh worker revision must be able to deliver Telegram notifications and
    # resume approved workflows without putting credential values in env vars.
    assert "APP_ROLE=worker" in source
    assert "ENABLE_TRIP_WATCH=${WATCH_ENABLED}" in source
    assert "ENABLE_JUDGE_MODE=${JUDGE_ENABLED}" in source
    assert "GEMINI_MODEL_ID=${GEMINI_MODEL_ID}" in source
    assert "--update-secrets=" in source
    assert "TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN_SECRET}:latest" in source
    assert "TELEGRAM_WEBHOOK_SECRET=${TELEGRAM_WEBHOOK_SECRET_SECRET}:latest" in source
    assert "APPROVAL_CALLBACK_SIGNING_KEY=${APPROVAL_CALLBACK_SIGNING_KEY_SECRET}:latest" in source
    assert "--no-allow-unauthenticated" in source
    assert "MAX_INSTANCES must be 1" in source
    assert "per-user Vertex budget cannot exceed the global budget" in source


def test_m7_edge_worker_deploy_does_not_regress_to_chat_only_worker() -> None:
    deploy_script = ROOT.joinpath("infra", "cloudrun", "deploy-m7.sh")
    source = deploy_script.read_text()

    assert deploy_script.stat().st_mode & 0o111
    assert "GEMINI_MODEL_ID:?set GEMINI_MODEL_ID" in source
    assert "ENABLE_TRIP_WATCH=${WATCH_ENABLED}" in source
    assert "ENABLE_JUDGE_MODE=${JUDGE_ENABLED}" in source
    assert "PUBSUB_COMMAND_TOPIC_ID=${COMMAND_TOPIC_ID}" in source
    assert "APP_ROLE=edge" in source
    assert "APP_ROLE=worker" in source
    assert "--no-allow-unauthenticated" in source
    assert "--allow-unauthenticated" in source
    assert "MAX_INSTANCES must be 1" in source
    assert "per-user Vertex budget cannot exceed the global budget" in source
    # Google integrations are opt-in; Telegram/Trip Watch can roll out before
    # OAuth consent and provider reread verification are complete.
    assert 'CALENDAR_ENABLED="${ENABLE_CALENDAR_CONNECTIONS:-false}"' in source
    assert 'GMAIL_ENABLED="${ENABLE_GMAIL_CONNECTIONS:-false}"' in source
    assert "ENABLE_CALENDAR_ACTIONS=true requires ENABLE_CALENDAR_CONNECTIONS=true" in source
    assert "ENABLE_GMAIL_DRAFTS=true requires ENABLE_GMAIL_CONNECTIONS=true" in source
    assert "ENABLE_CALENDAR_CONNECTIONS=${CALENDAR_ENABLED}" in source
    assert "ENABLE_GMAIL_CONNECTIONS=${GMAIL_ENABLED}" in source
    assert "ENABLE_CALENDAR_CONNECTIONS=true,ENABLE_CALENDAR_ACTIONS=true" not in source
    assert "ENABLE_GMAIL_CONNECTIONS=true,ENABLE_GMAIL_DRAFTS=true" not in source


def test_trip_watch_scheduler_uses_valid_cron_and_custom_service_account_name() -> None:
    source = _read("configure-trip-watch.sh")

    assert 'SCHEDULE="${SCHEDULE:-*/30 * * * *}"' in source
    assert 'SCHEDULER_SERVICE_ACCOUNT_NAME="${SCHEDULER_SERVICE_ACCOUNT%@*}"' in source
    assert 'gcloud iam service-accounts create "${SCHEDULER_SERVICE_ACCOUNT_NAME}"' in source
