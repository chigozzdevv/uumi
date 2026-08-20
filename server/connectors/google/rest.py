import asyncio
import re
from typing import Any, cast

import google.auth
import httpx
from contracts import Connection, ConnectionAuthorization, ConnectionInterface
from google.auth import impersonated_credentials
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
        self._scopes = scopes
        self._connection_credentials: dict[str, Credentials] = {}
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
        connection: Connection | None = None,
    ) -> dict[str, Any]:
        response = await self.response(
            method,
            url,
            json=json,
            content=content,
            headers=headers,
            params=params,
            expected=expected,
            connection=connection,
        )
        if not response.content:
            return {}
        value = response.json()
        if not isinstance(value, dict):
            raise ConnectorError("google-api-response", "Google API returned a non-object response")
        return value

    async def response(
        self,
        method: str,
        url: str,
        *,
        json: Any | None = None,
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        expected: frozenset[int] = frozenset({200}),
        connection: Connection | None = None,
    ) -> httpx.Response:
        token = await self._token(self._credentials_for(connection))
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
        return response

    async def wait_operation(
        self,
        name: str,
        attempts: int = 60,
        base_url: str = "https://run.googleapis.com/v2",
        connection: Connection | None = None,
    ) -> dict[str, Any]:
        for _ in range(attempts):
            operation = await self.request(
                "GET",
                f"{base_url.rstrip('/')}/{name}",
                connection=connection,
            )
            if operation.get("done") is True or operation.get("status") == "DONE":
                if "error" in operation:
                    raise ConnectorError(
                        "google-operation-failed", "Google operation completed with an error"
                    )
                response = operation.get("response")
                if response is None:
                    return operation
                return response if isinstance(response, dict) else {}
            await asyncio.sleep(1)
        raise ConnectorError("google-operation-timeout", "Google operation did not complete", True)

    async def close(self) -> None:
        await self._client.aclose()

    def _credentials_for(self, connection: Connection | None) -> Credentials:
        if connection is None:
            return self._credentials
        if (
            connection.interface is not ConnectionInterface.API
            or connection.authorization is not ConnectionAuthorization.WORKLOAD_IDENTITY
            or connection.authorization_reference is None
        ):
            raise ConnectorError(
                "google-authorization",
                "Google connections require workload-identity authorization",
            )
        principal = _target_principal(connection.authorization_reference)
        current = self._connection_credentials.get(principal)
        if current is None:
            current = impersonated_credentials.Credentials(  # type: ignore[no-untyped-call]
                source_credentials=self._credentials,
                target_principal=principal,
                target_scopes=self._scopes,
                lifetime=3600,
            )
            self._connection_credentials[principal] = current
        return current

    async def _token(self, credentials: Credentials) -> str:
        if not credentials.valid or not credentials.token:
            await asyncio.to_thread(credentials.refresh, self._request)
        if not credentials.token:
            raise ConnectorError("google-authentication", "Google credentials returned no token")
        return cast(str, credentials.token)


def _target_principal(reference: str) -> str:
    if reference.startswith("workload-identity://"):
        principal = reference.removeprefix("workload-identity://")
    elif "/serviceAccounts/" in reference:
        principal = reference.rsplit("/serviceAccounts/", 1)[1]
    else:
        raise ConnectorError(
            "google-authorization-reference",
            "workload identity must reference a service account",
        )
    if (
        re.fullmatch(
            r"[a-z][a-z0-9-]{4,28}[a-z0-9]@[a-z][a-z0-9-]{4,28}[a-z0-9]"
            r"\.iam\.gserviceaccount\.com",
            principal,
        )
        is None
    ):
        raise ConnectorError(
            "google-authorization-reference",
            "workload identity service account is invalid",
        )
    return principal
