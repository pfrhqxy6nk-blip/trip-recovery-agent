from datetime import UTC, datetime, timedelta, timezone

from app.services.canonical_hash import canonical_hash, canonical_json, semantic_effect_key


def test_canonical_hash_is_stable_for_mapping_order_and_timezone() -> None:
    first = {"when": datetime(2026, 8, 16, 10, 0, tzinfo=UTC), "target": "booking-1"}
    second = {
        "target": "booking-1",
        "when": datetime(2026, 8, 16, 12, 0, tzinfo=timezone(timedelta(hours=2))),
    }

    assert canonical_hash(first) == canonical_hash(second)
    assert canonical_json({"b": 1, "a": 2}) == canonical_json({"a": 2, "b": 1})


def test_ordered_actions_remain_semantic() -> None:
    assert canonical_hash(["transfer", "flight"]) != canonical_hash(["flight", "transfer"])


def test_effect_key_is_stable_across_plan_versions() -> None:
    first = semantic_effect_key("incident-1", "calendar", "event-1", "update", {"start": "10:00"})
    second = semantic_effect_key("incident-1", "calendar", "event-1", "update", {"start": "10:00"})

    assert first == second
