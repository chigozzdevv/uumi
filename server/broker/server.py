from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from connectors.cloudrun import CloudRunConnector
from connectors.google import GoogleRestClient
from connectors.secrets import SecretManagerConnector
from connectors.sendgrid import SendGridConnector
from contracts import ConnectionKind, ToolRequest, ToolResult
from core.audit import AuditWriter
from core.storage import FirestoreAuditRepository
from google.cloud.firestore_v1 import AsyncClient
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

from broker.capability import CapabilityVerifier
from broker.config import BrokerSettings
from broker.evidence import GcsEvidenceSink
from broker.service import BrokerService, ConnectorRegistry
from broker.storage import FirestoreBrokerRepository


class BrokerCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,127}$")
    organisation_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,127}$")
    run_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,127}$")
    agent_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,127}$")
    connection_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,127}$")
    payload: dict[str, Any] = Field(default_factory=dict)
    fencing_token: int = Field(gt=0)


@dataclass(frozen=True, slots=True)
class BrokerRuntime:
    service: BrokerService
    google: GoogleRestClient


@asynccontextmanager
async def lifespan(_: MCPServer[Any]) -> Any:
    settings = BrokerSettings()
    google = GoogleRestClient()
    firestore = AsyncClient(project=settings.project_id, database=settings.firestore_database)
    secrets = SecretManagerConnector(google)
    signer = CapabilityVerifier.decode(settings.capability_public_key)
    connectors = ConnectorRegistry()
    connectors.register(ConnectionKind.SECRET, "google-secret-manager", secrets)
    connectors.register(ConnectionKind.PROVIDER, "sendgrid", SendGridConnector(secrets))
    connectors.register(ConnectionKind.RUNTIME, "cloud-run", CloudRunConnector(google))
    service = BrokerService(
        FirestoreBrokerRepository(firestore),
        connectors,
        signer,
        GcsEvidenceSink(google, firestore, settings.evidence_bucket, settings.region),
        AuditWriter(
            FirestoreAuditRepository(firestore), settings.region, lambda: datetime.now(UTC)
        ),
        lambda: datetime.now(UTC),
        settings.attempt_lease_seconds,
    )
    try:
        yield BrokerRuntime(service, google)
    finally:
        await google.close()


server: MCPServer[BrokerRuntime] = MCPServer(
    "FireKey Tool Broker",
    description="Capability-scoped credential, secret-store, and runtime operations.",
    instructions=(
        "All calls are bound to an immutable playbook, connection boundary, run lease, and "
        "fencing token. Mutation capabilities are injected by Agent Gateway in an HTTP header."
    ),
    version="0.1.0",
    lifespan=lifespan,
)


def _read(title: str) -> ToolAnnotations:
    return ToolAnnotations(
        title=title,
        read_only_hint=True,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=True,
    )


def _mutation(title: str, destructive: bool = False) -> ToolAnnotations:
    return ToolAnnotations(
        title=title,
        read_only_hint=False,
        destructive_hint=destructive,
        idempotent_hint=True,
        open_world_hint=True,
    )


@server.tool(
    name="provider.listCredentialMetadata",
    description="List provider credential metadata without returning secret values.",
    annotations=_read("List provider credential metadata"),
    structured_output=True,
)
async def provider_list(call: BrokerCall, ctx: Context[BrokerRuntime, Any]) -> dict[str, Any]:
    return await _execute("provider.listCredentialMetadata", call, ctx)


@server.tool(
    name="provider.getCredentialStatus",
    description="Get deterministic provider credential status without accessing its value.",
    annotations=_read("Get provider credential status"),
    structured_output=True,
)
async def provider_status(call: BrokerCall, ctx: Context[BrokerRuntime, Any]) -> dict[str, Any]:
    return await _execute("provider.getCredentialStatus", call, ctx)


@server.tool(
    name="provider.createCredential",
    description=(
        "Create one provider credential and transfer its one-time value directly to Secret Manager."
    ),
    annotations=_mutation("Create provider credential"),
    structured_output=True,
)
async def provider_create(call: BrokerCall, ctx: Context[BrokerRuntime, Any]) -> dict[str, Any]:
    return await _execute("provider.createCredential", call, ctx)


@server.tool(
    name="provider.revokeCredential",
    description=(
        "Revoke one provider credential after protected approval and deterministic verification."
    ),
    annotations=_mutation("Revoke provider credential", destructive=True),
    structured_output=True,
)
async def provider_revoke(call: BrokerCall, ctx: Context[BrokerRuntime, Any]) -> dict[str, Any]:
    return await _execute("provider.revokeCredential", call, ctx)


@server.tool(
    name="secretStore.getVersion",
    description="Read Secret Manager version metadata, never the secret payload.",
    annotations=_read("Get secret version metadata"),
    structured_output=True,
)
async def secret_status(call: BrokerCall, ctx: Context[BrokerRuntime, Any]) -> dict[str, Any]:
    return await _execute("secretStore.getVersion", call, ctx)


@server.tool(
    name="secretStore.disableVersion",
    description="Disable exactly one assigned Secret Manager version.",
    annotations=_mutation("Disable secret version", destructive=True),
    structured_output=True,
)
async def secret_disable(call: BrokerCall, ctx: Context[BrokerRuntime, Any]) -> dict[str, Any]:
    return await _execute("secretStore.disableVersion", call, ctx)


@server.tool(
    name="secretStore.destroyVersion",
    description="Destroy exactly one assigned Secret Manager version after protected approval.",
    annotations=_mutation("Destroy secret version", destructive=True),
    structured_output=True,
)
async def secret_destroy(call: BrokerCall, ctx: Context[BrokerRuntime, Any]) -> dict[str, Any]:
    return await _execute("secretStore.destroyVersion", call, ctx)


@server.tool(
    name="runtime.inspectSecretBindings",
    description="Inspect Cloud Run revision and secret bindings without reading secret values.",
    annotations=_read("Inspect runtime secret bindings"),
    structured_output=True,
)
async def runtime_inspect(call: BrokerCall, ctx: Context[BrokerRuntime, Any]) -> dict[str, Any]:
    return await _execute("runtime.inspectSecretBindings", call, ctx)


@server.tool(
    name="runtime.deployCandidate",
    description="Deploy a zero-traffic Cloud Run candidate pinned to one secret generation.",
    annotations=_mutation("Deploy runtime candidate"),
    structured_output=True,
)
async def runtime_deploy(call: BrokerCall, ctx: Context[BrokerRuntime, Any]) -> dict[str, Any]:
    return await _execute("runtime.deployCandidate", call, ctx)


@server.tool(
    name="runtime.shiftTraffic",
    description="Shift an exact percentage of Cloud Run traffic to the verified candidate.",
    annotations=_mutation("Shift runtime traffic"),
    structured_output=True,
)
async def runtime_traffic(call: BrokerCall, ctx: Context[BrokerRuntime, Any]) -> dict[str, Any]:
    return await _execute("runtime.shiftTraffic", call, ctx)


@server.tool(
    name="runtime.rollback",
    description="Return all Cloud Run traffic to the pinned rollback revision.",
    annotations=_mutation("Rollback runtime traffic"),
    structured_output=True,
)
async def runtime_rollback(call: BrokerCall, ctx: Context[BrokerRuntime, Any]) -> dict[str, Any]:
    return await _execute("runtime.rollback", call, ctx)


async def _execute(tool: str, call: BrokerCall, ctx: Context[BrokerRuntime, Any]) -> dict[str, Any]:
    request = ToolRequest(tool=tool, **call.model_dump())
    capability = _header(ctx, "x-firekey-capability")
    result = await ctx.request_context.lifespan_context.service.execute(request, capability)
    return _safe_result(result)


def _header(ctx: Context[BrokerRuntime, Any], name: str) -> str | None:
    headers = ctx.headers or {}
    for key, value in headers.items():
        if key.lower() == name:
            return value
    return None


def _safe_result(result: ToolResult) -> dict[str, Any]:
    return result.model_dump(mode="json")
