from typing import Any

import pytest
from app.config import Settings


def _calendar_settings(**overrides: object) -> Settings:
    # Pydantic's generated ``__init__`` overloads do not accept a heterogeneous
    # ``dict[str, object]`` under strict mypy, even though this is exactly the
    # supported runtime API for settings constructed from test values. Keep the
    # test fixture typed at the boundary instead of weakening application types.
    values: dict[str, Any] = {
        "pubsub_transport": "local",
        "enable_calendar_connections": True,
        "calendar_client_id": "calendar-client",
        "calendar_client_secret_resource_name": (
            "projects/tripagent-505715/secrets/trip-agent-calendar-oauth-client/versions/latest"
        ),
        "calendar_oauth_signing_key": "a" * 32,
        "calendar_redirect_uri": "https://edge.example.test/connections/calendar/callback",
    }
    values.update(overrides)
    return Settings(**values)


def test_google_oauth_connections_require_dedicated_refresh_token_secret() -> None:
    with pytest.raises(ValueError, match="refresh-token secret resource"):
        _calendar_settings()


def test_google_oauth_connections_accept_in_project_refresh_token_secret() -> None:
    settings = _calendar_settings(
        oauth_refresh_tokens_secret_resource_name=(
            "projects/tripagent-505715/secrets/trip-agent-oauth-refresh-tokens"
        )
    )

    assert settings.oauth_refresh_tokens_secret_resource_name.endswith(
        "trip-agent-oauth-refresh-tokens"
    )


def test_google_oauth_rejects_a_refresh_token_secret_from_another_project() -> None:
    with pytest.raises(ValueError, match="refresh-token secret resource"):
        _calendar_settings(
            oauth_refresh_tokens_secret_resource_name=(
                "projects/another-project/secrets/trip-agent-oauth-refresh-tokens"
            )
        )


def test_google_oauth_rejects_an_unversioned_client_secret_resource() -> None:
    with pytest.raises(ValueError, match="versioned Calendar client secret"):
        _calendar_settings(
            calendar_client_secret_resource_name=(
                "projects/tripagent-505715/secrets/trip-agent-calendar-oauth-client"
            ),
            oauth_refresh_tokens_secret_resource_name=(
                "projects/tripagent-505715/secrets/trip-agent-oauth-refresh-tokens"
            ),
        )
