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
    capability: str = "general",
) -> bool:
    """Claim a bounded project budget for one autonomous capability.

    Planning, chat and background monitoring must not starve one another: a
    casual question cannot consume the slot needed for an imminent flight
    check.  Each capability keeps the same strict global and per-user cap.
    A failed second claim can leave one unused global slot, but it can never
    exceed that capability's cap.
    """

    # ``v2`` is an intentional bucket namespace.  The previous judge bucket
    # was consumed during repeated QA runs; rotating the namespace gives the
    # current release a clean, auditable daily budget without deleting data or
    # weakening the per-user/global caps.
    if not await repository.claim_telegram_rate_slot(
        telegram_user_id="judge-mode-global",
        update_kind=f"vertex-{capability}-global-v3",
        window_started_at=window_started_at,
        limit=global_limit,
    ):
        return False
    return await repository.claim_telegram_rate_slot(
        telegram_user_id=telegram_user_id or "anonymous",
        update_kind=f"vertex-{capability}-user-v3",
        window_started_at=window_started_at,
        limit=per_user_limit,
    )
