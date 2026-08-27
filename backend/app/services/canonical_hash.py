from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel


def _normalize(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _normalize(value.model_dump(mode="python"))
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _normalize(item) for key, item in sorted(value.items())}
    if isinstance(value, set | frozenset):
        return sorted(_normalize(item) for item in value)
    if isinstance(value, tuple):
        return [_normalize(item) for item in value]
    if isinstance(value, list):
        # Lists model ordered action plans, so their ordering is semantic and preserved.
        return [_normalize(item) for item in value]
    return value


def canonical_json(value: Any) -> str:
    """Stable JSON for identifiers and fingerprints; ordered lists remain ordered."""

    return json.dumps(_normalize(value), ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def grounded_signal_hash(signal: BaseModel) -> str:
    """Fingerprint a grounded fact without polling/delivery metadata.

    Search Watch observes the same public fact repeatedly.  Observation time and
    publication state describe the worker, not the fact itself; including either
    would turn every polling cycle into a new disruption event.
    """

    payload = signal.model_dump(mode="python")
    for field in ("observed_at", "source_updated_at", "published_at"):
        payload.pop(field, None)
    return canonical_hash(payload)


def semantic_effect_key(
    incident_id: str,
    provider: str,
    target_external_id: str,
    operation: str,
    desired_state: Mapping[str, object],
) -> str:
    desired_state_hash = canonical_hash(desired_state)
    return ":".join((incident_id, provider, target_external_id, operation, desired_state_hash))
