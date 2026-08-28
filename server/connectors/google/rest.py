import asyncio
import json as jsonlib
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any, cast

import google.auth
import httpx
from contracts import Connection, ConnectionAuthorization, ConnectionInterface
from google.auth import impersonated_credentials
from google.auth.credentials import Credentials
from google.auth.transport.requests import Request

from connectors.base.errors import ConnectorError
from connectors.base.result import SecretValue


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
        access_token: SecretValue | None = None,
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
            access_token=access_token,
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
        access_token: SecretValue | None = None,
    ) -> httpx.Response:
        if access_token is not None and connection is None:
            raise ConnectorError(
                "google-authorization", "ephemeral access must bind a declared connection"
            )
        token = (
            access_token.bytes().decode("utf-8", errors="strict")
            if access_token is not None
            else await self._token(self._credentials_for(connection))
        )
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
                safe_detail=_safe_error_detail(response),
            )
        return response

    async def stream(
        self,
        method: str,
        url: str,
        *,
        json: Any | None = None,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        expected: frozenset[int] = frozenset({200}),
        connection: Connection | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        token = await self._token(self._credentials_for(connection))
        request_headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "text/event-stream",
            **(headers or {}),
        }
        async with self._client.stream(
            method,
            url,
            headers=request_headers,
            json=json,
            params=params,
        ) as response:
            if response.status_code not in expected:
                retryable = response.status_code in {408, 409, 429, 500, 502, 503, 504}
                raise ConnectorError(
                    f"google-api-{response.status_code}",
                    f"Google API returned HTTP {response.status_code}",
                    retryable=retryable,
                )
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line.removeprefix("data:").strip()
                if not payload or payload == "[DONE]":
                    continue
                try:
                    value = jsonlib.loads(payload)
                except jsonlib.JSONDecodeError as error:
                    raise ConnectorError(
                        "google-api-stream", "Google API returned invalid stream data"
                    ) from error
                if not isinstance(value, dict):
                    raise ConnectorError(
                        "google-api-stream", "Google API returned a non-object stream event"
                    )
                yield value

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

    async def mint_access_token_for(
        self,
        connection: Connection,
        lifetime_seconds: int = 600,
    ) -> tuple[SecretValue, datetime]:
        if not 300 <= lifetime_seconds <= 900:
            raise ConnectorError(
                "google-token-lifetime",
                "ephemeral connection tokens must live between five and fifteen minutes",
            )
        if (
            connection.interface is not ConnectionInterface.API
            or connection.authorization is not ConnectionAuthorization.WORKLOAD_IDENTITY
            or connection.authorization_reference is None
        ):
            raise ConnectorError(
                "google-authorization",
                "ephemeral access requires a workload-identity connection",
            )
        principal = _target_principal(connection.authorization_reference)
        response = await self.request(
            "POST",
            f"https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/{principal}:generateAccessToken",
            json={"scope": list(self._scopes), "lifetime": f"{lifetime_seconds}s"},
        )
        token = response.get("accessToken")
        raw_expiry = response.get("expireTime")
        if not isinstance(token, str) or not token or not isinstance(raw_expiry, str):
            raise ConnectorError(
                "google-token-response", "IAM Credentials returned no ephemeral access token"
            )
        try:
            expires_at = datetime.fromisoformat(raw_expiry.replace("Z", "+00:00"))
        except ValueError as error:
            raise ConnectorError(
                "google-token-response", "IAM Credentials returned an invalid token expiry"
            ) from error
        if expires_at.tzinfo is None or expires_at <= datetime.now(UTC):
            raise ConnectorError(
                "google-token-response", "IAM Credentials returned an expired access token"
            )
        return SecretValue(token.encode()), expires_at

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


def _safe_error_detail(response: httpx.Response) -> str | None:
    try:
        payload = response.json()
    except (ValueError, jsonlib.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    if not isinstance(error, dict):
        return None
    values: list[str] = []
    status = error.get("status")
    if isinstance(status, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{1,63}", status):
        values.append(status.lower().replace("_", "-"))
    message = error.get("message")
    if isinstance(message, str) and re.search(
        r"(?:^|\n)Error Details:\s*Rate exceeded\.?\s*$", message, re.IGNORECASE
    ):
        values.append("rate-exceeded")
    details = error.get("details")
    if isinstance(details, list):
        for detail in details:
            if not isinstance(detail, dict):
                continue
            kind = detail.get("@type")
            if kind == "type.googleapis.com/google.rpc.ErrorInfo":
                reason = detail.get("reason")
                if isinstance(reason, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{1,63}", reason):
                    values.append(reason.lower().replace("_", "-"))
            elif kind == "type.googleapis.com/google.rpc.BadRequest":
                violations = detail.get("fieldViolations")
                if isinstance(violations, list):
                    for violation in violations:
                        field = violation.get("field") if isinstance(violation, dict) else None
                        if isinstance(field, str) and re.fullmatch(
                            r"[A-Za-z0-9_.\[\]-]{1,128}", field
                        ):
                            values.append(f"field-{field}")
                            break
    unique = list(dict.fromkeys(values))
    return ".".join(unique[:3]) or None
