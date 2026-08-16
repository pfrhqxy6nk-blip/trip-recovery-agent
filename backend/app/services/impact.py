from __future__ import annotations

from collections import defaultdict, deque

from app.models.domain import (
    BufferViolation,
    Dependency,
    DeterministicImpact,
    DisruptionEvent,
    Trip,
)
from app.models.enums import DependencyType


class ImpactCalculationError(ValueError):
    pass


class DeterministicImpactEngine:
    """Authoritative graph and time calculation; it contains no model calls."""

    version = "impact-engine-v1"

    def calculate(self, event: DisruptionEvent, trip: Trip) -> DeterministicImpact:
        if event.trip_id != trip.trip_id:
            raise ImpactCalculationError("event trip does not match loaded trip")

        items = {item.item_id: item for item in trip.items}
        disrupted = next(
            (item for item in trip.items if item.external_id == event.flight), None
        )
        if disrupted is None:
            raise ImpactCalculationError(f"flight {event.flight!r} is not in trip")

        delta = int((event.new_arrival - event.old_arrival).total_seconds() // 60)
        outgoing: dict[str, list[Dependency]] = defaultdict(list)
        for dependency in trip.dependencies:
            outgoing[dependency.from_item_id].append(dependency)

        violations: list[BufferViolation] = []
        first_violating_targets: list[str] = []
        directly_affected_dependencies: list[str] = []

        for dependency in outgoing.get(disrupted.item_id, []):
            target = items.get(dependency.to_item_id)
            if target is None:
                raise ImpactCalculationError(
                    f"dependency {dependency.dependency_id!r} targets a missing item"
                )
            directly_affected_dependencies.append(dependency.dependency_id)
            available = int((target.start_at - event.new_arrival).total_seconds() // 60)
            if (
                dependency.type == DependencyType.CONNECTION
                and available < dependency.min_buffer_minutes
            ):
                violations.append(
                    BufferViolation(
                        dependency_id=dependency.dependency_id,
                        available_minutes=available,
                        required_minutes=dependency.min_buffer_minutes,
                    )
                )
                first_violating_targets.append(dependency.to_item_id)

        affected_items: list[str] = []
        affected_dependencies: list[str] = []
        seen_items: set[str] = set()
        seen_dependencies: set[str] = set()
        queue = deque(first_violating_targets)

        while queue:
            item_id = queue.popleft()
            if item_id in seen_items:
                continue
            seen_items.add(item_id)
            affected_items.append(item_id)
            for dependency in outgoing.get(item_id, []):
                if dependency.dependency_id not in seen_dependencies:
                    seen_dependencies.add(dependency.dependency_id)
                    affected_dependencies.append(dependency.dependency_id)
                queue.append(dependency.to_item_id)

        if violations:
            violating_ids = [violation.dependency_id for violation in violations]
            affected_dependencies = violating_ids + [
                dep_id for dep_id in affected_dependencies if dep_id not in violating_ids
            ]

        return DeterministicImpact(
            disrupted_item_id=disrupted.item_id,
            arrival_delta_minutes=delta,
            connection_feasible=not violations,
            affected_item_ids=affected_items,
            affected_dependency_ids=affected_dependencies,
            buffer_violations=violations,
            engine_version=self.version,
        )
