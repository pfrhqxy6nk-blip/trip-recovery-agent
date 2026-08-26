from datetime import UTC, datetime

import pytest
from app.services.judge_quota import claim_judge_vertex_slot
from app.services.memory import InMemoryIncidentRepository


@pytest.mark.asyncio
async def test_judge_quota_caps_one_user_before_project_pool() -> None:
    repository = InMemoryIncidentRepository()
    day = datetime(2026, 8, 24, tzinfo=UTC)

    assert await claim_judge_vertex_slot(
        repository,
        telegram_user_id="101",
        window_started_at=day,
        global_limit=4,
        per_user_limit=2,
    )
    assert await claim_judge_vertex_slot(
        repository,
        telegram_user_id="101",
        window_started_at=day,
        global_limit=4,
        per_user_limit=2,
    )
    assert not await claim_judge_vertex_slot(
        repository,
        telegram_user_id="101",
        window_started_at=day,
        global_limit=4,
        per_user_limit=2,
    )

    # A second traveler can still use the remaining project budget.
    assert await claim_judge_vertex_slot(
        repository,
        telegram_user_id="202",
        window_started_at=day,
        global_limit=4,
        per_user_limit=2,
    )


@pytest.mark.asyncio
async def test_judge_quota_caps_project_even_with_many_users() -> None:
    repository = InMemoryIncidentRepository()
    day = datetime(2026, 8, 24, tzinfo=UTC)

    for user_id in ("101", "202"):
        assert await claim_judge_vertex_slot(
            repository,
            telegram_user_id=user_id,
            window_started_at=day,
            global_limit=2,
            per_user_limit=2,
        )
    assert not await claim_judge_vertex_slot(
        repository,
        telegram_user_id="303",
        window_started_at=day,
        global_limit=2,
        per_user_limit=2,
    )
