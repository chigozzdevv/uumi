import json
from typing import Any, Protocol
from urllib.parse import urlparse

from connectors.base import SecretValue
from contracts import Connection, ConnectionStatus
from core.errors import ResourceConflictError


class SecretReader(Protocol):
    async def access(self, reference: str) -> SecretValue: ...


class BrowserAuthBroker:
    def __init__(self, secrets: SecretReader) -> None:
        self._secrets = secrets

    async def storage_state(
        self,
        connection: Connection,
        allowed_domains: tuple[str, ...],
    ) -> dict[str, Any]:
        if connection.status is not ConnectionStatus.READY or connection.auth_reference is None:
            raise ResourceConflictError(
                "provider browser authentication requires a ready connection"
            )
        secret = await self._secrets.access(connection.auth_reference)
        try:
            try:
                state = json.loads(secret.bytes())
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise ResourceConflictError("browser authentication state is invalid") from error
        finally:
            secret.clear()
        if not isinstance(state, dict):
            raise ResourceConflictError("browser authentication state must be an object")
        cookies = state.get("cookies", [])
        origins = state.get("origins", [])
        if not isinstance(cookies, list) or not isinstance(origins, list):
            raise ResourceConflictError("browser authentication state has invalid collections")
        for cookie in cookies:
            if not isinstance(cookie, dict) or not _allowed_cookie(cookie, allowed_domains):
                raise ResourceConflictError(
                    "browser authentication cookie is outside the allowlist"
                )
        for origin in origins:
            if not isinstance(origin, dict) or not _allowed_origin(origin, allowed_domains):
                raise ResourceConflictError(
                    "browser authentication origin is outside the allowlist"
                )
        return state


def _allowed_cookie(cookie: dict[str, Any], allowed: tuple[str, ...]) -> bool:
    domain = cookie.get("domain")
    name = cookie.get("name")
    value = cookie.get("value")
    if not all(isinstance(item, str) and item for item in (domain, name, value)):
        return False
    assert isinstance(domain, str)
    return _allowed_domain(domain.lstrip(".").lower(), allowed)


def _allowed_origin(origin: dict[str, Any], allowed: tuple[str, ...]) -> bool:
    value = origin.get("origin")
    storage = origin.get("localStorage", [])
    if not isinstance(value, str) or not isinstance(storage, list):
        return False
    parsed = urlparse(value)
    return parsed.scheme == "https" and bool(
        parsed.hostname and _allowed_domain(parsed.hostname.lower(), allowed)
    )


def _allowed_domain(hostname: str, allowed: tuple[str, ...]) -> bool:
    for pattern in allowed:
        expected = pattern.lower().rstrip(".")
        if expected.startswith("*."):
            suffix = expected[2:]
            if hostname.endswith("." + suffix) and hostname != suffix:
                return True
        elif hostname == expected:
            return True
    return False
