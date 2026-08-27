from __future__ import annotations

from copy import deepcopy
from typing import Any

SCHEMA_VERSION = 2


def upgrade_incident_document(document: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Return an idempotently upgraded incident document without performing I/O.

    Runtime readers stay compatible with schema-v1 documents during the staged migration.
    A separate explicitly authorized command will apply returned changes to Firestore.
    """

    upgraded = deepcopy(document)
    if int(upgraded.get("schema_version", 1)) >= SCHEMA_VERSION:
        return upgraded, False
    upgraded["schema_version"] = SCHEMA_VERSION
    upgraded.setdefault("workflow_cursor", None)
    upgraded.setdefault("current_plan_version", None)
    upgraded.setdefault("current_plan_hash", None)
    return upgraded, True
