import json
import logging
import re
from datetime import UTC, datetime

_REDACTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"https://api\.telegram\.org/bot[^/\s]+", re.IGNORECASE), "[TELEGRAM_API]"),
    (re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{20,}\b"), "[TELEGRAM_TOKEN]"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b"), "[API_KEY]"),
    (
        re.compile(r"authorization\s*[:=]\s*(?:bearer\s+)?[^\s,;}]+", re.IGNORECASE),
        "authorization=[REDACTED]",
    ),
    (re.compile(r"\b(?:a|d|c|s|f|r):[A-Za-z0-9_-]{8,}\b"), "[CALLBACK_TOKEN]"),
    (
        re.compile(r"\b(?:chat_id|telegram_user_id)\s*[:=]\s*[^\s,;}]+", re.IGNORECASE),
        "telegram_identity=[REDACTED]",
    ),
    (re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE), "[EMAIL]"),
)


def redact_log_text(value: str) -> str:
    redacted = value
    for pattern, replacement in _REDACTIONS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "event": redact_log_text(record.getMessage()),
            "logger": record.name,
        }
        for key in (
            "incident_id",
            "correlation_id",
            "event_id",
            "command_id",
            "action_id",
            "plan_version",
            "notification_id",
            "provider",
            "error_code",
            "error_class",
            "failure_stage",
            "workflow_transition",
            "result_class",
            "attempt",
            "latency_ms",
            "watchpoint_id",
        ):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = redact_log_text(self.formatException(record.exc_info))
        return json.dumps(payload, separators=(",", ":"))


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
