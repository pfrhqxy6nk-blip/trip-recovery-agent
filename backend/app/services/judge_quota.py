from __future__ import annotations

from datetime import datetime

from app.services.ports import IncidentRepository


async def claim_judge_vertex_slot(
    repository: IncidentRepository,
    *,
    telegram_user_id: str,
    window_started_at: datetime,
    global_limit: int,
    per_user_limit: int,
) -> bool:
    """Claim the shared project budget and a smaller traveler budget.

    The two buckets intentionally use the same update kind.  A failed second
    claim can leave one unused global slot, but it can never exceed the global
    cap and is safer than allowing one public user to drain the whole pool.
    """

    if not await repository.claim_telegram_rate_slot(
        telegram_user_id="judge-mode-global",
        update_kind="vertex-global",
        window_started_at=window_started_at,
        limit=global_limit,
    ):
        return False
    return await repository.claim_telegram_rate_slot(
        telegram_user_id=telegram_user_id or "anonymous",
        update_kind="vertex-global-user",
        window_started_at=window_started_at,
        limit=per_user_limit,
    )
