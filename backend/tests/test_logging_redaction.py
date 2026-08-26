import json
import logging

from app.logging import JsonFormatter


def test_structured_logging_redacts_credentials_identity_and_callback_data() -> None:
    record = logging.LogRecord(
        name="app.test",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg=(
            "authorization=Bearer secret-access-token "
            "https://api.telegram.org/botsecret-value/sendMessage "
            "a:opaqueCallbackToken123 chat_id=998877 user@example.com"
        ),
        args=(),
        exc_info=None,
    )
    record.incident_id = "incident-safe"
    record.chat_id = "must-not-be-whitelisted"

    payload = json.loads(JsonFormatter().format(record))
    rendered = json.dumps(payload)

    assert payload["incident_id"] == "incident-safe"
    assert "secret-access-token" not in rendered
    assert "secret-value" not in rendered
    assert "opaqueCallbackToken123" not in rendered
    assert "998877" not in rendered
    assert "user@example.com" not in rendered
    assert "must-not-be-whitelisted" not in rendered
