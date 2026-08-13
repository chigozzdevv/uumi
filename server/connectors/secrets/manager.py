import base64
import binascii
import hashlib
from typing import Any

from connectors.base import ConnectorContext, ConnectorResponse, SecretValue
from connectors.base.errors import ConnectorError
from connectors.google import GoogleRestClient


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
        del context
        version = _version(payload)
        if tool == "secretStore.getVersion":
            response = await self._client.request(
                "GET", f"https://secretmanager.googleapis.com/v1/{version}"
            )
            return ConnectorResponse(result=_metadata(response))
        if tool == "secretStore.disableVersion":
            response = await self._client.request(
                "POST",
                f"https://secretmanager.googleapis.com/v1/{version}:disable",
                json={},
            )
            return ConnectorResponse(result=_metadata(response))
        if tool == "secretStore.destroyVersion":
            response = await self._client.request(
                "POST",
                f"https://secretmanager.googleapis.com/v1/{version}:destroy",
                json={},
            )
            return ConnectorResponse(result=_metadata(response))
        raise ConnectorError("unsupported-tool", f"Secret Manager does not support {tool}")

    async def add_version(self, secret: str, value: SecretValue) -> dict[str, Any]:
        response = await self._client.request(
            "POST",
            f"https://secretmanager.googleapis.com/v1/{secret}:addVersion",
            json={"payload": {"data": base64.b64encode(value.bytes()).decode()}},
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


def _version(payload: dict[str, Any]) -> str:
    value = payload.get("version")
    if not isinstance(value, str) or not value.startswith("projects/") or "/versions/" not in value:
        raise ConnectorError("invalid-secret-version", "a full Secret Manager version is required")
    return value


def _metadata(response: dict[str, Any]) -> dict[str, Any]:
    allowed = {"name", "state", "createTime", "destroyTime", "etag"}
    return {key: value for key, value in response.items() if key in allowed}
