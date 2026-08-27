from __future__ import annotations

import hashlib
from typing import Any

import httpx


class GoogleGeminiKeyValidator:
    """Validate credentials without sending itinerary or user content."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def validate(self, api_key: str) -> bool:
        try:
            response = await self._client.get(
                "https://generativelanguage.googleapis.com/v1beta/models",
                headers={"x-goog-api-key": api_key},
                timeout=httpx.Timeout(10.0),
            )
        except httpx.RequestError:
            return False
        return response.status_code == 200


class GoogleSecretManagerStore:
    def __init__(self, project_id: str, secret_resource_name: str = "") -> None:
        self._parent = f"projects/{project_id}"
        # A deployed worker receives resource-level IAM only for this one BYOK
        # secret. Each traveler has an independent immutable Secret Manager
        # version, and Firestore stores only that exact version resource name.
        self._shared_secret = secret_resource_name
        self._client_instance: Any | None = None

    def _client(self) -> Any:
        if self._client_instance is None:
            from google.cloud import secretmanager

            self._client_instance = secretmanager.SecretManagerServiceAsyncClient()
        return self._client_instance

    async def put_user_secret(self, *, user_id: str, value: str) -> str:
        from google.api_core.exceptions import AlreadyExists

        if self._shared_secret:
            secret_name = self._shared_secret
        else:
            suffix = hashlib.sha256(user_id.encode()).hexdigest()[:24]
            secret_id = f"trip-agent-gemini-{suffix}"
            secret_name = f"{self._parent}/secrets/{secret_id}"
            try:
                await self._client().create_secret(
                    request={
                        "parent": self._parent,
                        "secret_id": secret_id,
                        "secret": {"replication": {"automatic": {}}},
                    }
                )
            except AlreadyExists:
                pass
        version = await self._client().add_secret_version(
            request={
                "parent": secret_name,
                "payload": {"data": value.encode("utf-8")},
            }
        )
        return str(version.name)

    async def delete_secret(self, *, resource_name: str) -> None:
        await self._client().destroy_secret_version(request={"name": resource_name})

    async def access_secret(self, *, resource_name: str) -> str:
        response = await self._client().access_secret_version(request={"name": resource_name})
        return str(response.payload.data.decode("utf-8"))
