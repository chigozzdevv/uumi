import asyncio
from typing import Any, cast

import google.auth
import httpx
from google.auth.credentials import Credentials
from google.auth.transport.requests import Request

from connectors.base.errors import ConnectorError


class GoogleRestClient:
    def __init__(
        self,
        scopes: tuple[str, ...] = ("https://www.googleapis.com/auth/cloud-platform",),
        credentials: Credentials | None = None,
        client: httpx.AsyncClient | None = None,
        timeout: float = 60,
    ) -> None:
        if credentials is None:
            resolved, _ = google.auth.default(scopes=scopes)
            credentials = resolved
        self._credentials = credentials
        self._request = Request()
        self._client = client or httpx.AsyncClient(timeout=timeout)

    async def request(
        self,
        method: str,
        url: str,
        *,
        json: Any | None = None,
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        expected: frozenset[int] = frozenset({200}),
    ) -> dict[str, Any]:
        token = await self._token()
        request_headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            **(headers or {}),
        }
        response = await self._client.request(
            method,
            url,
            headers=request_headers,
            json=json,
            content=content,
            params=params,
        )
        if response.status_code not in expected:
            retryable = response.status_code in {408, 409, 429, 500, 502, 503, 504}
            raise ConnectorError(
                f"google-api-{response.status_code}",
                f"Google API returned HTTP {response.status_code}",
                retryable=retryable,
            )
        if not response.content:
            return {}
        value = response.json()
        if not isinstance(value, dict):
            raise ConnectorError("google-api-response", "Google API returned a non-object response")
        return value

    async def wait_operation(
        self,
        name: str,
        attempts: int = 60,
        base_url: str = "https://run.googleapis.com/v2",
    ) -> dict[str, Any]:
        for _ in range(attempts):
            operation = await self.request("GET", f"{base_url.rstrip('/')}/{name}")
            if operation.get("done") is True:
                if "error" in operation:
                    raise ConnectorError(
                        "google-operation-failed", "Google operation completed with an error"
                    )
                response = operation.get("response", {})
                return response if isinstance(response, dict) else {}
            await asyncio.sleep(1)
        raise ConnectorError("google-operation-timeout", "Google operation did not complete", True)

    async def close(self) -> None:
        await self._client.aclose()

    async def _token(self) -> str:
        if not self._credentials.valid or not self._credentials.token:
            await asyncio.to_thread(self._credentials.refresh, self._request)
        if not self._credentials.token:
            raise ConnectorError("google-authentication", "Google credentials returned no token")
        return cast(str, self._credentials.token)
