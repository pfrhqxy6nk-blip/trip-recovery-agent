from __future__ import annotations

import asyncio
from typing import cast


class CloudRunIdTokenProvider:
    """Obtain a short-lived OIDC token for a private Cloud Run audience."""

    def __init__(self, audience: str) -> None:
        self._audience = audience

    async def token(self) -> str:
        def fetch() -> str:
            from google.auth.transport.requests import Request
            from google.oauth2.id_token import fetch_id_token

            return cast(
                str,
                fetch_id_token(Request(), self._audience),  # type: ignore[no-untyped-call]
            )

        return await asyncio.to_thread(fetch)
