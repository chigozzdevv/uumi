from collections.abc import Iterable
from typing import Any
from urllib.parse import urlparse

from contracts import (
    Connection,
    PlaybookAssignment,
    PlaybookVersion,
    RotationRun,
    Stage,
    ToolRequest,
)
from core.errors import CapabilityError

from broker.capability import CapabilityClaims, request_digest

READ_TOOLS = frozenset(
    {
        "provider.listCredentialMetadata",
        "provider.getCredentialStatus",
        "secretStore.getVersion",
        "runtime.inspectSecretBindings",
        "telemetry.queryHealth",
        "telemetry.queryCredentialUsage",
    }
)

STAGE_TOOLS: dict[Stage, frozenset[str]] = {
    Stage.TRIGGER: frozenset(),
    Stage.PREFLIGHT: frozenset(
        {
            "provider.listCredentialMetadata",
            "provider.getCredentialStatus",
            "runtime.inspectSecretBindings",
        }
    ),
    Stage.PLAYBOOK: frozenset({"provider.listCredentialMetadata", "runtime.inspectSecretBindings"}),
    Stage.CREATE: frozenset({"provider.createCredential", "provider.getCredentialStatus"}),
    Stage.STORE: frozenset({"secretStore.getVersion"}),
    Stage.DEPLOY: frozenset({"runtime.deployCandidate", "runtime.rollback"}),
    Stage.VERIFY: frozenset(
        {
            "provider.getCredentialStatus",
            "secretStore.getVersion",
            "runtime.inspectSecretBindings",
            "verification.run",
        }
    ),
    Stage.ROLLOUT: frozenset({"runtime.shiftTraffic", "runtime.rollback"}),
    Stage.OBSERVE: frozenset(
        {"telemetry.queryHealth", "telemetry.queryCredentialUsage", "runtime.rollback"}
    ),
    Stage.APPROVAL: frozenset(),
    Stage.REVOKE: frozenset(
        {
            "provider.revokeCredential",
            "provider.getCredentialStatus",
            "secretStore.disableVersion",
            "secretStore.destroyVersion",
            "verification.run",
        }
    ),
    Stage.COMPLETE: frozenset(),
}


def validate_request(
    request: ToolRequest,
    run: RotationRun,
    connection: Connection,
    assignment: PlaybookAssignment,
    version: PlaybookVersion,
) -> None:
    if request.organisation_id != run.organisation_id or request.run_id != run.id:
        raise CapabilityError("tool request does not belong to its run")
    if request.fencing_token != run.fencing_token or run.lease is None:
        raise CapabilityError("tool request does not hold the current run fence")
    if request.connection_id != connection.id or connection.organisation_id != run.organisation_id:
        raise CapabilityError("tool connection does not belong to the run organisation")
    if request.connection_id not in assignment.connection_ids:
        raise CapabilityError("tool connection is not assigned to the active playbook")
    if version.id != assignment.version_id or run.playbook_version != version.id:
        raise CapabilityError("tool request is not bound to the run playbook version")
    if run.dry_run_id is not None:
        if not assignment.dry_run_only or run.dry_run_playbook_id != assignment.playbook_id:
            raise CapabilityError("dry-run tool request escaped its isolated assignment")
    elif assignment.dry_run_only:
        raise CapabilityError("production tool request cannot use a dry-run assignment")
    if request.tool not in version.definition.allowed_tools:
        raise CapabilityError("tool is not allowed by the immutable playbook")
    if request.tool not in connection.capabilities:
        raise CapabilityError("connection does not declare the requested capability")
    if request.tool not in STAGE_TOOLS[run.stage]:
        raise CapabilityError(f"tool {request.tool} is not eligible in stage {run.stage.value}")
    resources = _resources(request.tool, request.payload)
    within_boundary = all(
        _allowed(resource, connection.allowed_resources) for resource in resources
    )
    if resources and not within_boundary:
        raise CapabilityError("tool parameters escape the connection resource boundary")


def validate_capability(
    request: ToolRequest,
    run: RotationRun,
    claims: CapabilityClaims,
) -> None:
    expected = (
        request.organisation_id,
        request.run_id,
        request.agent_id,
        request.tool,
        request.connection_id,
        run.stage,
        request.fencing_token,
        request_digest(request.tool, request.payload),
    )
    actual = (
        claims.organisation_id,
        claims.run_id,
        claims.agent_id,
        claims.tool,
        claims.connection_id,
        claims.stage,
        claims.fencing_token,
        claims.request_digest,
    )
    if actual != expected:
        raise CapabilityError("action capability does not bind the exact tool request")


def _resources(tool: str, payload: dict[str, Any]) -> tuple[str, ...]:
    keys = {"provider_id", "service", "version", "target", "resource"}
    if tool.startswith("secretStore."):
        keys.add("secret_resource")
    return tuple(value for key, value in payload.items() if key in keys and isinstance(value, str))


def _allowed(resource: str, patterns: Iterable[str]) -> bool:
    parsed = urlparse(resource)
    comparable = parsed.hostname or resource
    return any(
        comparable == pattern
        or comparable.startswith(pattern.rstrip("/") + "/")
        or (pattern.startswith("*.") and comparable.endswith(pattern[1:]))
        for pattern in patterns
    )
