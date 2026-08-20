from datetime import UTC, datetime

from contracts import (
    Connection,
    ConnectionInterface,
    ConnectionRole,
    ConnectionStatus,
    ExecutionMethod,
)

from core.errors import PlaybookError, ResourceConflictError


def require_ready_browser_connections(connections: tuple[Connection, ...]) -> None:
    for connection in connections:
        if connection.interface is ConnectionInterface.BROWSER and (
            connection.status is not ConnectionStatus.READY
            or connection.authorization_reference is None
            or _authorization_expired(connection)
        ):
            raise ResourceConflictError("connection still needs login")


def validate_assignment_connections(
    execution: ExecutionMethod,
    connections: tuple[Connection, ...],
    allowed_domains: tuple[str, ...] = (),
    platform: str | None = None,
) -> None:
    browsers = tuple(
        connection
        for connection in connections
        if connection.interface is ConnectionInterface.BROWSER
        and ConnectionRole.PROVIDER in connection.roles
    )
    if platform is not None and any(
        connection.platform != platform
        for connection in connections
        if ConnectionRole.PROVIDER in connection.roles
    ):
        raise PlaybookError("provider connection platform does not match the playbook")
    if execution is ExecutionMethod.COMPUTER:
        if len(browsers) != 1:
            raise PlaybookError("computer-use assignment requires exactly one browser connection")
        if (
            browsers[0].status is not ConnectionStatus.READY
            or browsers[0].authorization_reference is None
            or _authorization_expired(browsers[0])
        ):
            raise PlaybookError("computer-use assignment requires a ready browser session")
        if allowed_domains and any(
            not _domain_covered(domain, browsers[0].allowed_resources) for domain in allowed_domains
        ):
            raise PlaybookError(
                "computer-use playbook domains are not covered by the browser connection"
            )
    else:
        if any(connection.interface is ConnectionInterface.BROWSER for connection in connections):
            raise PlaybookError("provider-api assignment cannot include a browser connection")
        if not any(
            connection.interface is ConnectionInterface.API
            and ConnectionRole.PROVIDER in connection.roles
            for connection in connections
        ):
            raise PlaybookError("provider-api assignment requires an API provider connection")
    if any(
        connection.status is not ConnectionStatus.READY or _authorization_expired(connection)
        for connection in connections
    ):
        raise PlaybookError("assignment requires ready connections")
    roles = frozenset(role for connection in connections for role in connection.roles)
    required = frozenset(
        {ConnectionRole.PROVIDER, ConnectionRole.SECRET_STORE, ConnectionRole.RUNTIME}
    )
    if not required.issubset(roles):
        missing = ", ".join(sorted(role.value for role in required.difference(roles)))
        raise PlaybookError(f"assignment is missing required connection roles: {missing}")


def _authorization_expired(connection: Connection) -> bool:
    return (
        connection.authorization_expires_at is not None
        and connection.authorization_expires_at <= datetime.now(UTC)
    )


def _domain_covered(need: str, allowed: tuple[str, ...]) -> bool:
    needed = need.lower().rstrip(".")
    allowed_norm = tuple(item.lower().rstrip(".") for item in allowed)
    if needed in allowed_norm:
        return True
    host = needed[2:] if needed.startswith("*.") else needed
    if _host_allowed(host, allowed):
        return True
    if not needed.startswith("*."):
        return False
    suffix = needed[2:]
    for pattern in allowed_norm:
        if pattern.startswith("*."):
            parent = pattern[2:]
            if suffix == parent or suffix.endswith("." + parent):
                return True
        elif suffix == pattern or suffix.endswith("." + pattern):
            return True
    return False


def _host_allowed(hostname: str, allowed: tuple[str, ...]) -> bool:
    for pattern in allowed:
        expected = pattern.lower().rstrip(".")
        if expected.startswith("*."):
            suffix = expected[2:]
            if hostname.endswith("." + suffix) and hostname != suffix:
                return True
        elif hostname == expected:
            return True
    return False
