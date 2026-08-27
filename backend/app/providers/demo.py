from __future__ import annotations

from app.models.recovery import PlannedAction
from app.services.ports import IncidentRepository


class PersistentDemoProvider:
    """A deterministic provider whose state lives in the repository, not process memory."""

    def __init__(self, repository: IncidentRepository) -> None:
        self._repository = repository

    async def apply(self, action: PlannedAction) -> str:
        resource_id = self._resource_id(action)
        await self._repository.apply_demo_provider_effect(
            resource_id=resource_id,
            effect_key=action.effect_key,
            desired_state=action.desired_state,
        )
        return resource_id

    async def verify(self, action: PlannedAction) -> bool:
        state = await self._repository.get_demo_provider_state(self._resource_id(action))
        return state == {"effect_key": action.effect_key, "desired_state": action.desired_state}

    @staticmethod
    def _resource_id(action: PlannedAction) -> str:
        return f"{action.provider}:{action.target_external_id}"
