from __future__ import annotations

import asyncio

from app.models.commands import WorkflowCommand
from app.models.domain import DisruptionEvent


class GooglePubSubPublisher:
    def __init__(self, project_id: str, topic_id: str) -> None:
        from google.cloud import pubsub_v1  # type: ignore[attr-defined]

        self._client = pubsub_v1.PublisherClient()
        self._topic_path = self._client.topic_path(project_id, topic_id)

    async def publish(self, event: DisruptionEvent) -> str:
        payload = event.model_dump_json().encode("utf-8")
        future = self._client.publish(
            self._topic_path,
            payload,
            event_id=event.event_id,
            trip_id=event.trip_id,
            event_type=event.type,
        )
        return await asyncio.to_thread(future.result)


class GooglePubSubWorkflowCommandPublisher:
    """Publishes durable recovery continuations to their own Pub/Sub topic."""

    def __init__(self, project_id: str, topic_id: str) -> None:
        from google.cloud import pubsub_v1  # type: ignore[attr-defined]

        self._client = pubsub_v1.PublisherClient()
        self._topic_path = self._client.topic_path(project_id, topic_id)

    async def publish_command(self, command: WorkflowCommand) -> str:
        payload = command.model_dump_json().encode("utf-8")
        future = self._client.publish(
            self._topic_path,
            payload,
            command_id=command.command_id,
            command_type=command.type.value,
            incident_id=command.incident_id,
        )
        return await asyncio.to_thread(future.result)
