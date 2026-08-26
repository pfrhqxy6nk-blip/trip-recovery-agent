from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.services.ports import IncidentRepository, WorkflowCommandPublisher


@dataclass(frozen=True)
class OutboxDispatchResult:
    published: int
    pending: int


class DurableOutboxDispatcher:
    """Publish pending commands; command claiming makes redelivery harmless."""

    def __init__(
        self,
        repository: IncidentRepository,
        publisher: WorkflowCommandPublisher,
    ) -> None:
        self._repository = repository
        self._publisher = publisher

    async def dispatch_pending(self, *, now: datetime, limit: int = 100) -> OutboxDispatchResult:
        records = await self._repository.list_pending_outbox(limit=limit)
        published = 0
        for record in records:
            if record.command.not_before is not None and record.command.not_before > now:
                continue
            await self._publisher.publish_command(record.command)
            if not await self._repository.mark_outbox_published(
                outbox_id=record.outbox_id, published_at=now
            ):
                raise RuntimeError("published outbox record could not be acknowledged")
            published += 1
        remaining = await self._repository.list_pending_outbox(limit=limit)
        return OutboxDispatchResult(published=published, pending=len(remaining))
