import base64
import binascii
import hashlib
from typing import TYPE_CHECKING, Any

from connectors.base import ConnectorContext, ConnectorResponse, SecretValue
from connectors.base.errors import ConnectorError
from connectors.google import GoogleRestClient

if TYPE_CHECKING:
    from contracts import Connection


class SecretManagerConnector:
    tools = frozenset(
        {
            "secretStore.getVersion",
            "secretStore.disableVersion",
            "secretStore.destroyVersion",
        }
    )

    def __init__(self, client: GoogleRestClient) -> None:
        self._client = client

    async def execute(
        self,
        tool: str,
        payload: dict[str, Any],
        context: ConnectorContext,
    ) -> ConnectorResponse:
        connection = context.connection
        version = _version(payload)
        if tool == "secretStore.getVersion":
            response = await self._client.request(
                "GET",
                f"https://secretmanager.googleapis.com/v1/{version}",
                connection=connection,
            )
            return ConnectorResponse(result=_metadata(response))
        if tool == "secretStore.disableVersion":
            response = await self._client.request(
                "POST",
                f"https://secretmanager.googleapis.com/v1/{version}:disable",
                json={},
                connection=connection,
            )
            return ConnectorResponse(result=_metadata(response))
        if tool == "secretStore.destroyVersion":
            response = await self._client.request(
                "POST",
                f"https://secretmanager.googleapis.com/v1/{version}:destroy",
                json={},
                connection=connection,
            )
            return ConnectorResponse(result=_metadata(response))
        raise ConnectorError("unsupported-tool", f"Secret Manager does not support {tool}")

    async def add_version(self, secret: str, value: SecretValue) -> dict[str, Any]:
        return await self._add_version(secret, value)

    async def add_version_for(
        self,
        connection: "Connection",
        secret: str,
        value: SecretValue,
    ) -> dict[str, Any]:
        return await self._add_version(secret, value, connection)

    async def _add_version(
        self,
        secret: str,
        value: SecretValue,
        connection: "Connection | None" = None,
    ) -> dict[str, Any]:
        response = await self._client.request(
            "POST",
            f"https://secretmanager.googleapis.com/v1/{secret}:addVersion",
            json={"payload": {"data": base64.b64encode(value.bytes()).decode()}},
            connection=connection,
        )
        name = response.get("name")
        if not isinstance(name, str):
            raise ConnectorError("secret-write-failed", "Secret Manager returned no version name")
        checksum = hashlib.sha256(value.bytes()).hexdigest()
        return {"secret_reference": name, "fingerprint": checksum, **_metadata(response)}

    async def access(self, version: str) -> SecretValue:
        response = await self._client.request(
            "GET", f"https://secretmanager.googleapis.com/v1/{version}:access"
        )
        payload = response.get("payload")
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, str):
            raise ConnectorError("secret-read-failed", "Secret Manager returned no secret payload")
        try:
            decoded = base64.b64decode(data, validate=True)
        except (ValueError, binascii.Error) as error:
            raise ConnectorError(
                "secret-read-failed", "Secret Manager returned invalid secret data"
            ) from error
        return SecretValue(decoded)

    async def versions(self, secret: str) -> tuple[dict[str, Any], ...]:
        return await self._versions(secret)

    async def versions_for(
        self,
        connection: "Connection",
        secret: str,
    ) -> tuple[dict[str, Any], ...]:
        return await self._versions(secret, connection)

    async def _versions(
        self,
        secret: str,
        connection: "Connection | None" = None,
    ) -> tuple[dict[str, Any], ...]:
        if not secret.startswith("projects/") or "/secrets/" not in secret:
            raise ConnectorError(
                "invalid-secret-resource", "a full Secret Manager secret is required"
            )
        response = await self._client.request(
            "GET",
            f"https://secretmanager.googleapis.com/v1/{secret}/versions",
            params={"pageSize": "100"},
            connection=connection,
        )
        values = response.get("versions", [])
        if not isinstance(values, list) or not all(isinstance(item, dict) for item in values):
            raise ConnectorError(
                "secret-list-failed", "Secret Manager returned invalid version metadata"
            )
        return tuple(_metadata(item) for item in values)

    async def disable(self, version: str) -> dict[str, Any]:
        return await self._disable(version)

    async def disable_for(
        self,
        connection: "Connection",
        version: str,
    ) -> dict[str, Any]:
        return await self._disable(version, connection)

    async def _disable(
        self,
        version: str,
        connection: "Connection | None" = None,
    ) -> dict[str, Any]:
        if not version.startswith("projects/") or "/versions/" not in version:
            raise ConnectorError(
                "invalid-secret-version", "a full Secret Manager version is required"
            )
        response = await self._client.request(
            "POST",
            f"https://secretmanager.googleapis.com/v1/{version}:disable",
            json={},
            connection=connection,
        )
        return _metadata(response)


def _version(payload: dict[str, Any]) -> str:
    value = payload.get("version")
    if not isinstance(value, str) or not value.startswith("projects/") or "/versions/" not in value:
        raise ConnectorError("invalid-secret-version", "a full Secret Manager version is required")
    return value


def _metadata(response: dict[str, Any]) -> dict[str, Any]:
    allowed = {"name", "state", "createTime", "destroyTime", "etag"}
    return {key: value for key, value in response.items() if key in allowed}
