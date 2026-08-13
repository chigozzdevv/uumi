from typing import Any

import httpx

from connectors.base import ConnectorContext, ConnectorResponse, SecretValue
from connectors.base.errors import AmbiguousMutationError, ConnectorError
from connectors.secrets import SecretManagerConnector


class SendGridConnector:
    tools = frozenset(
        {
            "provider.listCredentialMetadata",
            "provider.createCredential",
            "provider.getCredentialStatus",
            "provider.revokeCredential",
        }
    )

    def __init__(
        self,
        secrets: SecretManagerConnector,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._secrets = secrets
        self._client = client or httpx.AsyncClient(
            base_url="https://api.sendgrid.com/v3",
            timeout=30,
        )

    async def execute(
        self,
        tool: str,
        payload: dict[str, Any],
        context: ConnectorContext,
    ) -> ConnectorResponse:
        auth_reference = context.connection.auth_reference
        if auth_reference is None:
            raise ConnectorError(
                "missing-auth-reference", "SendGrid connection is not authenticated"
            )
        with await self._secrets.access(auth_reference) as admin_key:
            headers = {"Authorization": f"Bearer {admin_key.bytes().decode()}"}
            if tool == "provider.listCredentialMetadata":
                keys = await self._list(headers)
                return ConnectorResponse(result={"credentials": keys})
            if tool == "provider.getCredentialStatus":
                key_id = _string(payload, "provider_id")
                keys = await self._list(headers)
                match = next((key for key in keys if key.get("api_key_id") == key_id), None)
                return ConnectorResponse(result={"exists": match is not None, "credential": match})
            if tool == "provider.createCredential":
                return await self._create(payload, headers)
            if tool == "provider.revokeCredential":
                key_id = _string(payload, "provider_id")
                response = await self._client.delete(f"/api_keys/{key_id}", headers=headers)
                _expected(response, {204})
                return ConnectorResponse(result={"provider_id": key_id, "revoked": True})
        raise ConnectorError("unsupported-tool", f"SendGrid does not support {tool}")

    async def _create(
        self,
        payload: dict[str, Any],
        headers: dict[str, str],
    ) -> ConnectorResponse:
        name = _string(payload, "name")
        scopes = payload.get("scopes")
        if (
            not isinstance(scopes, list)
            or not scopes
            or not all(isinstance(scope, str) and scope for scope in scopes)
        ):
            raise ConnectorError("invalid-scopes", "SendGrid credential scopes are required")
        before = {
            key_id
            for key in await self._list(headers)
            if isinstance(key_id := key.get("api_key_id"), str)
        }
        try:
            response = await self._client.post(
                "/api_keys",
                headers=headers,
                json={"name": name, "scopes": scopes},
            )
        except httpx.TimeoutException as error:
            await self._reconcile_ambiguous(name, before, headers)
            raise AmbiguousMutationError("SendGrid create timed out and was reconciled") from error
        _expected(response, {201})
        value = response.json()
        key_id = value.get("api_key_id")
        api_key = value.get("api_key")
        if not isinstance(key_id, str) or not isinstance(api_key, str):
            raise ConnectorError(
                "invalid-provider-response", "SendGrid returned no key ID or secret"
            )
        return ConnectorResponse(
            result={"provider_id": key_id, "name": name, "scopes": scopes},
            secret=SecretValue(api_key.encode()),
        )

    async def _reconcile_ambiguous(
        self,
        name: str,
        before: set[str],
        headers: dict[str, str],
    ) -> None:
        after = await self._list(headers)
        candidates = [
            key
            for key in after
            if key.get("name") == name and str(key.get("api_key_id")) not in before
        ]
        if len(candidates) != 1:
            raise AmbiguousMutationError(
                "SendGrid create outcome could not be attributed to exactly one orphan"
            )
        key_id = candidates[0].get("api_key_id")
        if not isinstance(key_id, str):
            raise AmbiguousMutationError("SendGrid orphan has no provider ID")
        response = await self._client.delete(f"/api_keys/{key_id}", headers=headers)
        _expected(response, {204})

    async def _list(self, headers: dict[str, str]) -> list[dict[str, Any]]:
        response = await self._client.get("/api_keys", headers=headers, params={"limit": "500"})
        _expected(response, {200})
        value = response.json()
        result = value.get("result") if isinstance(value, dict) else None
        if not isinstance(result, list) or not all(isinstance(item, dict) for item in result):
            raise ConnectorError(
                "invalid-provider-response", "SendGrid returned invalid key metadata"
            )
        return result


def _expected(response: httpx.Response, statuses: set[int]) -> None:
    if response.status_code not in statuses:
        retryable = response.status_code in {408, 429, 500, 502, 503, 504}
        raise ConnectorError(
            "sendgrid-api-error",
            f"SendGrid returned HTTP {response.status_code}",
            retryable=retryable,
        )


def _string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ConnectorError("invalid-parameter", f"{key} is required")
    return value
