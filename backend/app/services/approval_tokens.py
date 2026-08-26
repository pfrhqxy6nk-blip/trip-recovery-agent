from __future__ import annotations

import hashlib
import hmac
import secrets
from base64 import urlsafe_b64encode


def new_callback_token() -> str:
    """Return a short opaque token that fits Telegram callback_data with a prefix."""

    return secrets.token_urlsafe(18)


def callback_token_hash(token: str) -> str:
    if not token:
        raise ValueError("callback token must not be empty")
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class ApprovalTokenManager:
    """Derive restart-stable opaque callbacks without persisting the raw token."""

    def __init__(self, signing_key: str) -> None:
        if len(signing_key.encode("utf-8")) < 32:
            raise ValueError("approval callback signing key must be at least 32 bytes")
        self._key = signing_key.encode("utf-8")

    def token_for(self, *, approval_id: str, plan_hash: str) -> str:
        payload = f"v1:{approval_id}:{plan_hash}".encode()
        digest = hmac.digest(self._key, payload, "sha256")[:18]
        return urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
