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
            "secretStore.testConsumerAccess",
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
        if tool == "secretStore.testConsumerAccess":
            identity = payload.get("consumer_identity")
            if not isinstance(identity, str) or not identity:
                raise ConnectorError("invalid-parameter", "consumer_identity is required")
            consumer = connection.model_copy(
                update={"authorization_reference": f"workload-identity://{identity}"}
            )
            token, expires_at = await self._client.mint_access_token_for(consumer)
            try:
                secret = await self._access(version, connection, token)
                try:
                    if not secret.bytes():
                        raise ConnectorError(
                            "secret-read-failed", "Secret Manager returned an empty secret"
                        )
                finally:
                    secret.clear()
            finally:
                token.clear()
            return ConnectorResponse(
                result={
                    "accessible": True,
                    "consumer_identity": identity,
                    "authorization_expires_at": expires_at.isoformat(),
                }
            )
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
        access_token: SecretValue | None = None,
    ) -> dict[str, Any]:
        return await self._add_version(secret, value, connection, access_token)

    async def _add_version(
        self,
        secret: str,
        value: SecretValue,
        connection: "Connection | None" = None,
        access_token: SecretValue | None = None,
    ) -> dict[str, Any]:
        response = await self._client.request(
            "POST",
            f"https://secretmanager.googleapis.com/v1/{secret}:addVersion",
            json={"payload": {"data": base64.b64encode(value.bytes()).decode()}},
            connection=connection,
            access_token=access_token,
        )
        name = response.get("name")
        if not isinstance(name, str):
            raise ConnectorError("secret-write-failed", "Secret Manager returned no version name")
        checksum = hashlib.sha256(value.bytes()).hexdigest()
        return {"secret_reference": name, "fingerprint": checksum, **_metadata(response)}

    async def access(self, version: str) -> SecretValue:
        return await self._access(version)

    async def access_for(self, connection: "Connection", version: str) -> SecretValue:
        return await self._access(version, connection)

    async def _access(
        self,
        version: str,
        connection: "Connection | None" = None,
        access_token: SecretValue | None = None,
    ) -> SecretValue:
        response = await self._client.request(
            "GET",
            f"https://secretmanager.googleapis.com/v1/{version}:access",
            connection=connection,
            access_token=access_token,
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

    async def resources_for(
        self,
        connection: "Connection",
    ) -> tuple[dict[str, Any], ...]:
        parents = tuple(
            dict.fromkeys(
                boundary.partition("/secrets")[0]
                for boundary in connection.allowed_resources
                if boundary.startswith("projects/") and "/secrets" in boundary
            )
        )
        values: list[dict[str, Any]] = []
        for parent in parents:
            token: str | None = None
            while True:
                params = {"pageSize": "100"}
                if token:
                    params["pageToken"] = token
                response = await self._client.request(
                    "GET",
                    f"https://secretmanager.googleapis.com/v1/{parent}/secrets",
                    params=params,
                    connection=connection,
                )
                resources = response.get("secrets", [])
                if not isinstance(resources, list) or not all(
                    isinstance(item, dict) for item in resources
                ):
                    raise ConnectorError(
                        "secret-list-failed", "Secret Manager returned invalid secret metadata"
                    )
                for item in resources:
                    resource = _secret_resource(item, parent)
                    if resource is not None and any(
                        resource["name"] == boundary
                        or resource["name"].startswith(boundary.rstrip("/") + "/")
                        for boundary in connection.allowed_resources
                    ):
                        values.append(resource)
                next_token = response.get("nextPageToken")
                if not isinstance(next_token, str) or not next_token:
                    break
                token = next_token
        return tuple(values)

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
        normalized = tuple(_secret_version(item, secret) for item in values)
        if any(item is None for item in normalized):
            raise ConnectorError(
                "secret-list-failed", "Secret Manager returned invalid version metadata"
            )
        return tuple(_metadata(item) for item in normalized if item is not None)

    async def disable(self, version: str) -> dict[str, Any]:
        return await self._disable(version)

    async def disable_for(
        self,
        connection: "Connection",
        version: str,
        access_token: SecretValue | None = None,
    ) -> dict[str, Any]:
        return await self._disable(version, connection, access_token)

    async def _disable(
        self,
        version: str,
        connection: "Connection | None" = None,
        access_token: SecretValue | None = None,
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
            access_token=access_token,
        )
        return _metadata(response)


def _version(payload: dict[str, Any]) -> str:
    value = payload.get("version")
    if not isinstance(value, str) or not value.startswith("projects/") or "/versions/" not in value:
        raise ConnectorError("invalid-secret-version", "a full Secret Manager version is required")
    return value


def _secret_resource(item: dict[str, Any], parent: str) -> dict[str, Any] | None:
    name = item.get("name")
    if not isinstance(name, str):
        return None
    parts = name.split("/")
    if (
        len(parts) != 4
        or parts[0] != "projects"
        or not parts[1]
        or parts[2] != "secrets"
        or not parts[3]
    ):
        return None
    return {**item, "name": f"{parent}/secrets/{parts[3]}"}


def _secret_version(item: dict[str, Any], secret: str) -> dict[str, Any] | None:
    name = item.get("name")
    secret_name = secret.rsplit("/", 1)[-1]
    if not isinstance(name, str):
        return None
    parts = name.split("/")
    if (
        len(parts) != 6
        or parts[0] != "projects"
        or not parts[1]
        or parts[2] != "secrets"
        or parts[3] != secret_name
        or parts[4] != "versions"
        or not parts[5]
    ):
        return None
    return {**item, "name": f"{secret}/versions/{parts[5]}"}


def _metadata(response: dict[str, Any]) -> dict[str, Any]:
    allowed = {"name", "state", "createTime", "destroyTime", "etag"}
    return {key: value for key, value in response.items() if key in allowed}
