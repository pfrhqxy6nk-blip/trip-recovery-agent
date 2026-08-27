from app.migrations.backfill_schema_v2 import SCHEMA_VERSION, upgrade_incident_document


def test_schema_v1_incident_upgrade_is_idempotent_and_preserves_payload() -> None:
    source = {"incident_id": "incident-1", "status": "PLANNING", "version": 3}

    upgraded, changed = upgrade_incident_document(source)
    second, changed_again = upgrade_incident_document(upgraded)

    assert changed is True
    assert source == {"incident_id": "incident-1", "status": "PLANNING", "version": 3}
    assert upgraded["schema_version"] == SCHEMA_VERSION
    assert upgraded["workflow_cursor"] is None
    assert upgraded["current_plan_version"] is None
    assert second == upgraded
    assert changed_again is False
