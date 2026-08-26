from __future__ import annotations

from app.models.recovery import PlannedAction
from app.providers.demo import PersistentDemoProvider
from app.services.action_executor import ProviderActionError
from app.services.ports import IncidentRepository


class JudgeOnlyDemoProvider:
    """Allow deterministic provider effects only for the internal judge replay.

    The demo provider is useful for proving the durable state machine, but it must
    never make a real Telegram user's flight look rebooked.  The incident's
    immutable external id is the authority for this boundary; action labels are
    deliberately not trusted because they are persisted input.
    """

    def __init__(self, repository: IncidentRepository) -> None:
        self._repository = repository
        self._delegate = PersistentDemoProvider(repository)

    async def _assert_judge_incident(self, action: PlannedAction) -> None:
        incident = await self._repository.get_incident(action.incident_id)
        if incident is None or not incident.external_event_id.startswith("telegram-demo:"):
            error_code = (
                "duffel_order_change_not_configured"
                if action.provider == "duffel"
                else "live_action_provider_not_configured"
            )
            raise ProviderActionError(error_code=error_code, retryable=False)

    async def apply(self, action: PlannedAction) -> str:
        await self._assert_judge_incident(action)
        return await self._delegate.apply(action)

    async def verify(self, action: PlannedAction) -> bool:
        incident = await self._repository.get_incident(action.incident_id)
        if incident is None or not incident.external_event_id.startswith("telegram-demo:"):
            return False
        return await self._delegate.verify(action)
