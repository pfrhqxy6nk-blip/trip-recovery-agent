from __future__ import annotations

import base64
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.commands import WorkflowCommand
from app.models.domain import DisruptionEvent


class PubSubMessage(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    data: str
    message_id: str | None = Field(default=None, alias="messageId")
    attributes: dict[str, str] = Field(default_factory=dict)

    def decode_event(self) -> DisruptionEvent:
        try:
            raw = base64.b64decode(self.data, validate=True)
            payload: Any = json.loads(raw)
        except (ValueError, json.JSONDecodeError) as exc:
            raise ValueError("invalid Pub/Sub message data") from exc
        return DisruptionEvent.model_validate(payload)

    def decode_command(self) -> WorkflowCommand:
        try:
            raw = base64.b64decode(self.data, validate=True)
            payload: Any = json.loads(raw)
        except (ValueError, json.JSONDecodeError) as exc:
            raise ValueError("invalid Pub/Sub message data") from exc
        return WorkflowCommand.model_validate(payload)


class PubSubEnvelope(BaseModel):
    model_config = ConfigDict(extra="ignore")

    message: PubSubMessage
    subscription: str | None = None
