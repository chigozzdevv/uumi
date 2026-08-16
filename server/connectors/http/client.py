import base64
import re
from typing import Any
from urllib.parse import quote, urljoin, urlsplit

import httpx
from contracts import Connection, HttpAuth, HttpAuthScheme, HttpOperation, HttpProviderApi

from connectors.base import ConnectorContext, ConnectorResponse, SecretValue
from connectors.base.errors import AmbiguousMutationError, ConnectorError
from connectors.secrets import SecretManagerConnector


class HttpProviderConnector:
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
        self._client = client

    async def execute(
        self,
        tool: str,
        payload: dict[str, Any],
        context: ConnectorContext,
    ) -> ConnectorResponse:
        api = _api(context.connection)
        headers = await self._headers(context.connection.auth_reference, api.auth)
        if tool == "provider.listCredentialMetadata":
            keys = await self._list(api, headers)
            return ConnectorResponse(result={"credentials": keys})
        if tool == "provider.getCredentialStatus":
            key_id = _string(payload, "provider_id")
            keys = await self._list(api, headers)
            field = api.list_credentials.provider_id_field
            match = next((key for key in keys if field and key.get(field) == key_id), None)
            return ConnectorResponse(result={"exists": match is not None, "credential": match})
        if tool == "provider.createCredential":
            return await self._create(api, payload, headers)
        if tool == "provider.revokeCredential":
            key_id = _string(payload, "provider_id")
            response = await self._call(
                api, api.revoke_credential, headers, {"provider_id": key_id}
            )
            _expected(response, set(api.revoke_credential.success_statuses))
            return ConnectorResponse(result={"provider_id": key_id, "revoked": True})
        raise ConnectorError("unsupported-tool", f"provider does not support {tool}")

    async def prepare(
        self,
        tool: str,
        payload: dict[str, Any],
        context: ConnectorContext,
    ) -> dict[str, str | int | bool | tuple[str, ...]]:
        if tool != "provider.createCredential":
            return {}
        api = _api(context.connection)
        headers = await self._headers(context.connection.auth_reference, api.auth)
        field = api.list_credentials.provider_id_field
        before = tuple(
            sorted(
                key_id
                for key in await self._list(api, headers)
                if field and isinstance(key_id := key.get(field), str)
            )
        )
        secret_resource = _string(payload, "secret_resource")
        secret_versions = tuple(
            sorted(
                name
                for item in await self._secrets.versions(secret_resource)
                if isinstance(name := item.get("name"), str)
            )
        )
        return {
            "name": _string(payload, "name"),
            "before_ids": before,
            "secret_resource": secret_resource,
            "before_secret_versions": secret_versions,
        }

    async def reconcile(
        self,
        tool: str,
        payload: dict[str, Any],
        state: dict[str, str | int | bool | tuple[str, ...]],
        context: ConnectorContext,
    ) -> ConnectorResponse | None:
        if tool != "provider.createCredential":
            return None
        api = _api(context.connection)
        name = _string(payload, "name")
        before = state.get("before_ids")
        secret_resource = state.get("secret_resource")
        before_versions = state.get("before_secret_versions")
        if (
            state.get("name") != name
            or not isinstance(before, tuple)
            or not isinstance(secret_resource, str)
            or not isinstance(before_versions, tuple)
        ):
            raise AmbiguousMutationError("provider reconciliation checkpoint is invalid")
        headers = await self._headers(context.connection.auth_reference, api.auth)
        after = await self._list(api, headers)
        id_field = api.list_credentials.provider_id_field
        name_field = api.list_credentials.name_field or api.create_credential.name_field
        candidates = [
            key
            for key in after
            if name_field
            and id_field
            and key.get(name_field) == name
            and isinstance(key.get(id_field), str)
            and key.get(id_field) not in before
        ]
        versions = await self._secrets.versions(secret_resource)
        version_candidates = [
            item
            for item in versions
            if isinstance(item.get("name"), str)
            and item.get("name") not in before_versions
            and item.get("state") == "ENABLED"
        ]
        if len(candidates) > 1 or len(version_candidates) > 1:
            raise AmbiguousMutationError(
                "stale provider create has multiple attributable cleanup candidates"
            )
        if candidates and id_field:
            key_id = candidates[0][id_field]
            assert isinstance(key_id, str)
            response = await self._call(
                api, api.revoke_credential, headers, {"provider_id": key_id}
            )
            _expected(response, set(api.revoke_credential.success_statuses) | {404})
        if version_candidates:
            version = version_candidates[0]["name"]
            assert isinstance(version, str)
            await self._secrets.disable(version)
        return None

    async def _create(
        self,
        api: HttpProviderApi,
        payload: dict[str, Any],
        headers: dict[str, str],
    ) -> ConnectorResponse:
        name = _string(payload, "name")
        id_field = api.create_credential.provider_id_field
        secret_field = api.create_credential.secret_field
        list_id = api.list_credentials.provider_id_field
        if id_field is None or secret_field is None or list_id is None:
            raise ConnectorError("invalid-provider-api", "create declaration is incomplete")
        before = {
            key_id
            for key in await self._list(api, headers)
            if isinstance(key_id := key.get(list_id), str)
        }
        try:
            response = await self._call(api, api.create_credential, headers, payload)
        except httpx.TimeoutException as error:
            await self._reconcile_ambiguous(api, name, before, headers)
            raise AmbiguousMutationError("provider create timed out and was reconciled") from error
        _expected(response, set(api.create_credential.success_statuses))
        value = _object(response.json(), "provider create")
        key_id = _dig(value, id_field)
        secret = _dig(value, secret_field)
        if not isinstance(key_id, str) or not isinstance(secret, str):
            raise ConnectorError(
                "invalid-provider-response", "provider returned no key ID or secret"
            )
        scopes = payload.get("scopes")
        return ConnectorResponse(
            result={
                "provider_id": key_id,
                "name": name,
                "scopes": scopes if isinstance(scopes, list) else [],
            },
            secret=SecretValue(secret.encode()),
        )

    async def _reconcile_ambiguous(
        self,
        api: HttpProviderApi,
        name: str,
        before: set[str],
        headers: dict[str, str],
    ) -> None:
        after = await self._list(api, headers)
        id_field = api.list_credentials.provider_id_field
        name_field = api.list_credentials.name_field or api.create_credential.name_field
        candidates = [
            key
            for key in after
            if name_field
            and id_field
            and key.get(name_field) == name
            and str(key.get(id_field)) not in before
        ]
        if len(candidates) != 1 or not id_field:
            raise AmbiguousMutationError(
                "provider create outcome could not be attributed to exactly one orphan"
            )
        key_id = candidates[0].get(id_field)
        if not isinstance(key_id, str):
            raise AmbiguousMutationError("provider orphan has no provider ID")
        response = await self._call(api, api.revoke_credential, headers, {"provider_id": key_id})
        _expected(response, set(api.revoke_credential.success_statuses) | {404})

    async def _list(self, api: HttpProviderApi, headers: dict[str, str]) -> list[dict[str, Any]]:
        response = await self._call(api, api.list_credentials, headers, {})
        _expected(response, set(api.list_credentials.success_statuses))
        value = response.json()
        items = value
        if api.list_credentials.list_items:
            items = (
                _dig(value, api.list_credentials.list_items) if isinstance(value, dict) else None
            )
        if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
            raise ConnectorError(
                "invalid-provider-response", "provider returned invalid key metadata"
            )
        return items

    async def _call(
        self,
        api: HttpProviderApi,
        operation: HttpOperation,
        headers: dict[str, str],
        payload: dict[str, Any],
    ) -> httpx.Response:
        path = _render_path(operation.path, payload)
        url = _operation_url(api, path)
        body = _render_body(operation.body, payload) if operation.body else None
        client = self._client or httpx.AsyncClient(timeout=30)
        close = self._client is None
        try:
            return await client.request(
                operation.method,
                url,
                headers=headers,
                params=operation.query or None,
                json=body,
            )
        finally:
            if close:
                await client.aclose()

    async def _headers(self, auth_reference: str | None, auth: HttpAuth) -> dict[str, str]:
        if auth_reference is None:
            raise ConnectorError(
                "missing-auth-reference", "provider connection is not authenticated"
            )
        with await self._secrets.access(auth_reference) as secret:
            token = secret.bytes().decode()
        if auth.scheme is HttpAuthScheme.BEARER:
            prefix = auth.prefix if auth.prefix is not None else "Bearer "
            return {auth.header: f"{prefix}{token}"}
        if auth.scheme is HttpAuthScheme.HEADER:
            prefix = auth.prefix or ""
            return {auth.header: f"{prefix}{token}"}
        encoded = base64.b64encode(token.encode()).decode()
        return {auth.header: f"Basic {encoded}"}


def _api(connection: Connection) -> HttpProviderApi:
    if connection.http is None:
        raise ConnectorError("missing-provider-api", "provider connection has no HTTP API")
    return connection.http


def _render_path(path: str, payload: dict[str, Any]) -> str:
    def replace(match: Any) -> str:
        key = match.group(1)
        value = payload.get(key)
        if not isinstance(value, str) or not value:
            raise ConnectorError("invalid-parameter", f"{key} is required")
        return quote(value, safe="")

    return _PATH_PARAMETER.sub(replace, path)


def _operation_url(api: HttpProviderApi, path: str) -> str:
    base = urlsplit(api.base_url)
    url = urljoin(api.base_url.rstrip("/") + "/", path.lstrip("/"))
    target = urlsplit(url)
    if (
        target.scheme != base.scheme
        or target.hostname != base.hostname
        or target.port != base.port
        or target.username is not None
        or target.password is not None
    ):
        raise ConnectorError(
            "invalid-provider-api", "provider operation resolved outside its configured origin"
        )
    return url


def _render_body(template: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    return {key: _render_value(value, payload) for key, value in template.items()}


def _render_value(value: Any, payload: dict[str, Any]) -> Any:
    if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
        return payload.get(value[2:-1])
    if isinstance(value, dict):
        return {key: _render_value(item, payload) for key, item in value.items()}
    if isinstance(value, list):
        return [_render_value(item, payload) for item in value]
    return value


def _dig(value: Any, path: str) -> Any:
    current = value
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConnectorError("invalid-provider-response", f"{label} returned a non-object")
    return value


def _expected(response: httpx.Response, statuses: set[int]) -> None:
    if response.status_code not in statuses:
        retryable = response.status_code in {408, 429, 500, 502, 503, 504}
        raise ConnectorError(
            "provider-api-error",
            f"provider returned HTTP {response.status_code}",
            retryable=retryable,
        )


def _string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ConnectorError("invalid-parameter", f"{key} is required")
    return value


_PATH_PARAMETER = re.compile(r"{([A-Za-z][A-Za-z0-9_]*)}")
