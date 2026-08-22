import base64
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from broker import (
    BrokerService,
    CapabilityClaims,
    CapabilitySigner,
    CapabilityVerifier,
    ConnectorRegistry,
)
from broker.capability import request_digest
from broker.server import server as mcp_server
from broker.validate import validate_request
from connectors import ConnectorContext, ConnectorResponse, SecretValue
from connectors.base.errors import AmbiguousMutationError
from connectors.google import GoogleRestClient
from connectors.secrets import SecretManagerConnector
from contracts import (
    Approval,
    AuditEvent,
    Connection,
    ConnectionAuthorization,
    ConnectionInterface,
    ConnectionRole,
    ConnectionStatus,
    ConsumerBinding,
    ControlVersion,
    Lease,
    ManagedCredential,
    ProtectedAction,
    RunStatus,
    Stage,
    ToolAttempt,
    ToolAttemptStatus,
    ToolRequest,
    ToolResult,
)
from core.audit import AuditWriter
from core.errors import CapabilityError
from google.oauth2.credentials import Credentials
from testkit import make_control_version, make_run

NOW = datetime(2026, 8, 13, 12, tzinfo=UTC)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class Provider:
    tools = frozenset({"provider.createCredential", "provider.revokeCredential"})

    def __init__(self) -> None:
        self.creations = 0

    async def execute(
        self, tool: str, payload: dict[str, Any], context: ConnectorContext
    ) -> ConnectorResponse:
        del payload, context
        if tool == "provider.createCredential":
            self.creations += 1
            return ConnectorResponse(
                result={"provider_id": "provider-key-one"},
                secret=SecretValue(b"one-time-secret"),
            )
        return ConnectorResponse(result={"revoked": True})

    async def prepare(
        self, tool: str, payload: dict[str, Any], context: ConnectorContext
    ) -> dict[str, str | int | bool | tuple[str, ...]]:
        del tool, payload, context
        return {"baseline": ()}

    async def reconcile(
        self,
        tool: str,
        payload: dict[str, Any],
        state: dict[str, str | int | bool | tuple[str, ...]],
        context: ConnectorContext,
    ) -> ConnectorResponse | None:
        del tool, payload, context
        assert state.get("baseline") == ()
        return None


class Repository:
    def __init__(self) -> None:
        self.results: dict[str, tuple[str, ToolAttempt]] = {}
        self.run_value = make_run(NOW).model_copy(
            update={
                "status": RunStatus.RUNNING,
                "stage": Stage.CREATE,
                "lease": Lease(
                    owner_id="worker_one",
                    fencing_token=1,
                    expires_at=NOW + timedelta(minutes=5),
                ),
                "fencing_token": 1,
            }
        )
        self.provider = _connection(
            "provider_one", ConnectionRole.PROVIDER, "provider", Provider.tools
        )
        self.sink = _connection(
            "sink_one",
            ConnectionRole.SECRET_STORE,
            "google-secret-manager",
            SecretManagerConnector.tools,
        )
        self.credential_value = ManagedCredential(
            id=self.run_value.credential_id,
            organisation_id="org_one",
            connection_id="provider_one",
            secret_store_connection_id="sink_one",
            secret_resource="projects/project-one/secrets/credential",
            secret_reference="projects/project-one/secrets/credential/versions/3",
            provider="provider",
            kind="api-key",
            display_name="Production credential",
            control_version=self.run_value.control_version,
            created_at=NOW,
            updated_at=NOW,
        )
        controls = make_control_version(now=NOW)
        definition = controls.definition.model_copy(
            update={
                "allowed_tools": frozenset(
                    {"provider.createCredential", "provider.revokeCredential", "verification.run"}
                )
            }
        )
        self.control_value = controls.model_copy(update={"definition": definition})

    async def attempt(self, request: ToolRequest, request_hash: str) -> ToolAttempt | None:
        value = self.results.get(request.id)
        if value is None:
            return None
        assert value[0] == request_hash
        return value[1]

    async def begin(
        self,
        request: ToolRequest,
        request_hash: str,
        reconciliation: dict[str, str | int | bool | tuple[str, ...]],
        now: datetime,
        lease_expires_at: datetime,
    ) -> None:
        assert request.id not in self.results
        self.results[request.id] = (
            request_hash,
            ToolAttempt(
                id=request.id,
                organisation_id=request.organisation_id,
                run_id=request.run_id,
                request_digest=request_hash,
                tool=request.tool,
                status=ToolAttemptStatus.RUNNING,
                reconciliation=reconciliation,
                started_at=now,
                lease_expires_at=lease_expires_at,
            ),
        )

    async def reclaim(
        self,
        request: ToolRequest,
        request_hash: str,
        expected_expiry: datetime,
        now: datetime,
        lease_expires_at: datetime,
    ) -> None:
        attempt = self.results[request.id][1]
        assert attempt.lease_expires_at == expected_expiry
        assert attempt.lease_expires_at <= now
        self.results[request.id] = (
            request_hash,
            attempt.model_copy(update={"lease_expires_at": lease_expires_at}),
        )

    async def checkpoint(
        self,
        request: ToolRequest,
        request_hash: str,
        result: ToolResult,
    ) -> None:
        attempt = self.results[request.id][1]
        self.results[request.id] = (
            request_hash,
            attempt.model_copy(update={"checkpoint": result}),
        )

    async def finish(
        self,
        request: ToolRequest,
        request_hash: str,
        result: ToolResult,
        now: datetime,
    ) -> None:
        attempt = self.results[request.id][1]
        self.results[request.id] = (
            request_hash,
            attempt.model_copy(
                update={
                    "status": (
                        ToolAttemptStatus.SUCCEEDED
                        if result.succeeded
                        else ToolAttemptStatus.FAILED
                    ),
                    "result": result,
                    "checkpoint": None,
                    "completed_at": now,
                }
            ),
        )

    async def run(self, organisation_id: str, run_id: str) -> Any:
        assert (organisation_id, run_id) == ("org_one", self.run_value.id)
        return self.run_value

    async def connection(self, organisation_id: str, connection_id: str) -> Connection:
        assert organisation_id == "org_one"
        return {"provider_one": self.provider, "sink_one": self.sink}[connection_id]

    async def credential(self, organisation_id: str, credential_id: str) -> ManagedCredential:
        assert credential_id == self.credential_value.id
        return self.credential_value

    async def bindings(
        self, organisation_id: str, credential_id: str
    ) -> tuple[ConsumerBinding, ...]:
        return ()

    async def controls(
        self, organisation_id: str, credential_id: str, version_id: str
    ) -> ControlVersion:
        assert credential_id == self.control_value.credential_id
        assert version_id == self.control_value.id
        return self.control_value

    async def approval(self, organisation_id: str, approval_id: str) -> Approval:
        raise AssertionError("automatic creation requires no approval")

    async def action(self, organisation_id: str, action_id: str) -> ProtectedAction:
        raise AssertionError("automatic creation requires no approval")


class EvidenceSink:
    async def store(self, *_: Any, **__: Any) -> Any:
        raise AssertionError("provider returned no evidence payload")


class Audits:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    async def list_events(self, organisation_id: str, limit: int) -> tuple[AuditEvent, ...]:
        return tuple(self.events)[:limit]

    async def append(
        self,
        event_id: str,
        organisation_id: str,
        kind: str,
        actor_id: str,
        resource: str,
        run_id: str | None,
        payload: dict[str, str | int | float | bool | None],
        evidence_ids: tuple[str, ...],
        occurred_at: datetime,
        region: str,
    ) -> AuditEvent:
        event = AuditEvent(
            id=event_id,
            organisation_id=organisation_id,
            sequence=0,
            kind=kind,
            actor_id=actor_id,
            resource=resource,
            run_id=run_id,
            payload=payload,
            evidence_ids=evidence_ids,
            previous_hash="0" * 64,
            event_hash="a" * 64,
            occurred_at=occurred_at,
            region=region,
        )
        self.events.append(event)
        return event


@pytest.mark.anyio
async def test_broker_stores_one_time_secret_and_deduplicates_provider_call() -> None:
    stored: dict[str, Any] = {}

    def google_handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"versions": []})
        stored.update(__import__("json").loads(request.content))
        return httpx.Response(
            200,
            json={
                "name": "projects/project-one/secrets/credential/versions/4",
                "state": "ENABLED",
            },
        )

    google = GoogleRestClient(
        credentials=Credentials(token="token"),  # type: ignore[no-untyped-call]
        client=httpx.AsyncClient(transport=httpx.MockTransport(google_handler)),
    )
    google._connection_credentials["firekey@project-one.iam.gserviceaccount.com"] = Credentials(
        token="token"
    )  # type: ignore[no-untyped-call]
    provider = Provider()
    registry = ConnectorRegistry()
    registry.register(ConnectionRole.PROVIDER, ConnectionInterface.API, "provider", provider)
    registry.register(
        ConnectionRole.SECRET_STORE,
        ConnectionInterface.API,
        "google-secret-manager",
        SecretManagerConnector(google),
    )
    repository = Repository()
    signer = CapabilitySigner(b"s" * 32)
    audits = Audits()
    broker = BrokerService(
        repository,
        registry,
        CapabilityVerifier(signer.public_key),
        EvidenceSink(),
        AuditWriter(audits, "us-east1", lambda: NOW),
        lambda: NOW,
    )
    request = ToolRequest(
        id="request_one",
        organisation_id="org_one",
        run_id=repository.run_value.id,
        agent_id="operator_one",
        tool="provider.createCredential",
        connection_id="provider_one",
        payload={
            "name": "firekey-run-one",
            "sink_connection_id": "sink_one",
            "secret_resource": "projects/project-one/secrets/credential",
        },
        fencing_token=1,
    )
    request_hash = request_digest(request.tool, request.payload)
    token = signer.mint(
        CapabilityClaims(
            organisation_id="org_one",
            run_id=repository.run_value.id,
            agent_id="operator_one",
            tool=request.tool,
            connection_id="provider_one",
            stage=Stage.CREATE,
            fencing_token=1,
            request_digest=request_hash,
            action_digest=request_hash,
            expires_at=int((NOW + timedelta(minutes=1)).timestamp()),
            nonce="nonce_one",
        )
    )

    first = await broker.execute(request, token)
    repeated = await broker.execute(request, token)

    assert first == repeated
    assert first.succeeded is True
    assert provider.creations == 1
    assert base64.b64decode(stored["payload"]["data"]) == b"one-time-secret"
    assert "one-time-secret" not in first.model_dump_json()
    assert first.result["secret_reference"].endswith("/versions/4")
    assert audits.events[0].kind == "tool.succeeded"
    await google.close()


@pytest.mark.anyio
async def test_broker_recovers_expired_create_before_retrying() -> None:
    stored: dict[str, Any] = {}

    def google_handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"versions": []})
        stored.update(__import__("json").loads(request.content))
        return httpx.Response(
            200,
            json={
                "name": "projects/project-one/secrets/credential/versions/5",
                "state": "ENABLED",
            },
        )

    google = GoogleRestClient(
        credentials=Credentials(token="token"),  # type: ignore[no-untyped-call]
        client=httpx.AsyncClient(transport=httpx.MockTransport(google_handler)),
    )
    google._connection_credentials["firekey@project-one.iam.gserviceaccount.com"] = Credentials(
        token="token"
    )  # type: ignore[no-untyped-call]
    provider = Provider()
    registry = ConnectorRegistry()
    registry.register(ConnectionRole.PROVIDER, ConnectionInterface.API, "provider", provider)
    registry.register(
        ConnectionRole.SECRET_STORE,
        ConnectionInterface.API,
        "google-secret-manager",
        SecretManagerConnector(google),
    )
    repository = Repository()
    signer = CapabilitySigner(b"s" * 32)
    request = ToolRequest(
        id="request_stale",
        organisation_id="org_one",
        run_id=repository.run_value.id,
        agent_id="operator_one",
        tool="provider.createCredential",
        connection_id="provider_one",
        payload={
            "name": "firekey-run-one",
            "sink_connection_id": "sink_one",
            "secret_resource": "projects/project-one/secrets/credential",
        },
        fencing_token=1,
    )
    request_hash = request_digest(request.tool, request.payload)
    token = signer.mint(
        CapabilityClaims(
            organisation_id="org_one",
            run_id=repository.run_value.id,
            agent_id="operator_one",
            tool=request.tool,
            connection_id="provider_one",
            stage=Stage.CREATE,
            fencing_token=1,
            request_digest=request_hash,
            action_digest=request_hash,
            expires_at=int((NOW + timedelta(minutes=1)).timestamp()),
            nonce="nonce_stale",
        )
    )
    await repository.begin(
        request,
        request_hash,
        {
            "baseline": (),
            "secret_resource": "projects/project-one/secrets/credential",
            "before_secret_versions": (),
        },
        NOW - timedelta(minutes=5),
        NOW - timedelta(minutes=1),
    )
    broker = BrokerService(
        repository,
        registry,
        CapabilityVerifier(signer.public_key),
        EvidenceSink(),
        AuditWriter(Audits(), "us-east1", lambda: NOW),
        lambda: NOW,
    )

    result = await broker.execute(request, token)

    assert result.succeeded is True
    assert provider.creations == 1
    assert base64.b64decode(stored["payload"]["data"]) == b"one-time-secret"
    await google.close()


@pytest.mark.anyio
async def test_broker_leaves_ambiguous_secret_write_reconcilable() -> None:
    writes = 0
    orphan_exists = False
    disabled: list[str] = []

    def google_handler(request: httpx.Request) -> httpx.Response:
        nonlocal orphan_exists, writes
        if request.method == "GET":
            versions = (
                [
                    {
                        "name": "projects/project-one/secrets/credential/versions/6",
                        "state": "ENABLED",
                    }
                ]
                if orphan_exists
                else []
            )
            return httpx.Response(200, json={"versions": versions})
        if request.url.path.endswith(":disable"):
            disabled.append(request.url.path)
            orphan_exists = False
            return httpx.Response(
                200,
                json={
                    "name": "projects/project-one/secrets/credential/versions/6",
                    "state": "DISABLED",
                },
            )
        writes += 1
        if writes == 1:
            orphan_exists = True
            raise httpx.ReadTimeout("addVersion response was lost", request=request)
        return httpx.Response(
            200,
            json={
                "name": "projects/project-one/secrets/credential/versions/6",
                "state": "ENABLED",
            },
        )

    google = GoogleRestClient(
        credentials=Credentials(token="token"),  # type: ignore[no-untyped-call]
        client=httpx.AsyncClient(transport=httpx.MockTransport(google_handler)),
    )
    google._connection_credentials["firekey@project-one.iam.gserviceaccount.com"] = Credentials(
        token="token"
    )  # type: ignore[no-untyped-call]
    provider = Provider()
    registry = ConnectorRegistry()
    registry.register(ConnectionRole.PROVIDER, ConnectionInterface.API, "provider", provider)
    registry.register(
        ConnectionRole.SECRET_STORE,
        ConnectionInterface.API,
        "google-secret-manager",
        SecretManagerConnector(google),
    )
    repository = Repository()
    signer = CapabilitySigner(b"s" * 32)
    request = ToolRequest(
        id="request_ambiguous",
        organisation_id="org_one",
        run_id=repository.run_value.id,
        agent_id="operator_one",
        tool="provider.createCredential",
        connection_id="provider_one",
        payload={
            "name": "firekey-run-one",
            "sink_connection_id": "sink_one",
            "secret_resource": "projects/project-one/secrets/credential",
        },
        fencing_token=1,
    )
    request_hash = request_digest(request.tool, request.payload)
    token = signer.mint(
        CapabilityClaims(
            organisation_id="org_one",
            run_id=repository.run_value.id,
            agent_id="operator_one",
            tool=request.tool,
            connection_id="provider_one",
            stage=Stage.CREATE,
            fencing_token=1,
            request_digest=request_hash,
            action_digest=request_hash,
            expires_at=int((NOW + timedelta(minutes=10)).timestamp()),
            nonce="nonce_ambiguous",
        )
    )
    broker = BrokerService(
        repository,
        registry,
        CapabilityVerifier(signer.public_key),
        EvidenceSink(),
        AuditWriter(Audits(), "us-east1", lambda: NOW),
        lambda: NOW,
    )

    with pytest.raises(AmbiguousMutationError):
        await broker.execute(request, token)

    attempt = repository.results[request.id][1]
    assert attempt.status is ToolAttemptStatus.RUNNING
    assert attempt.result is None
    repository.results[request.id] = (
        request_hash,
        attempt.model_copy(update={"lease_expires_at": NOW + timedelta(minutes=1)}),
    )
    later = NOW + timedelta(minutes=2)
    retry = BrokerService(
        repository,
        registry,
        CapabilityVerifier(signer.public_key),
        EvidenceSink(),
        AuditWriter(Audits(), "us-east1", lambda: later),
        lambda: later,
    )

    result = await retry.execute(request, token)

    assert result.succeeded
    assert provider.creations == 2
    assert writes == 2
    assert disabled == ["/v1/projects/project-one/secrets/credential/versions/6:disable"]
    await google.close()


def _connection(
    connection_id: str,
    role: ConnectionRole,
    platform: str,
    tools: frozenset[str],
) -> Connection:
    resources = (
        "projects/project-one/secrets/credential"
        if role is ConnectionRole.SECRET_STORE
        else "provider-key-one"
    )
    from testkit import make_http_provider_api

    return Connection(
        id=connection_id,
        organisation_id="org_one",
        platform=platform,
        display_name=platform,
        roles=frozenset({role}),
        interface=ConnectionInterface.API,
        authorization=(
            ConnectionAuthorization.WORKLOAD_IDENTITY
            if role is ConnectionRole.SECRET_STORE
            else ConnectionAuthorization.API_KEY
        ),
        authorization_reference=(
            "workload-identity://firekey@project-one.iam.gserviceaccount.com"
            if role is ConnectionRole.SECRET_STORE
            else "projects/project-one/secrets/auth/versions/1"
        ),
        capabilities=tools,
        allowed_resources=(resources,),
        http=make_http_provider_api() if role is ConnectionRole.PROVIDER else None,
        status=ConnectionStatus.READY,
        region="us-east1",
        created_at=NOW,
        updated_at=NOW,
    )


def test_mcp_broker_exposes_only_capability_scoped_connector_tools() -> None:
    tools = {tool.name: tool for tool in mcp_server._tool_manager.list_tools()}

    assert set(tools) == {
        "provider.listCredentialMetadata",
        "provider.getCredentialStatus",
        "provider.createCredential",
        "provider.revokeCredential",
        "secretStore.getVersion",
        "secretStore.testConsumerAccess",
        "secretStore.disableVersion",
        "secretStore.destroyVersion",
        "runtime.inspectSecretBindings",
        "runtime.deployCandidate",
        "runtime.shiftTraffic",
        "runtime.rollback",
    }
    assert all("capability" not in str(tool.parameters).lower() for tool in tools.values())
    revoke = tools["provider.revokeCredential"].annotations
    status = tools["provider.getCredentialStatus"].annotations
    assert revoke is not None and revoke.destructive_hint is True
    assert status is not None and status.read_only_hint is True


def test_provider_resource_boundary_uses_connection_platform_and_credential_id() -> None:
    repository = Repository()
    run = repository.run_value.model_copy(update={"stage": Stage.REVOKE})
    connection = repository.provider.model_copy(
        update={"allowed_resources": ("provider:credentials:*",)}
    )
    request = ToolRequest(
        id="request_revoke",
        organisation_id="org_one",
        run_id=run.id,
        agent_id="operator_one",
        tool="provider.revokeCredential",
        connection_id=connection.id,
        payload={"provider_id": "provider-key-one"},
        fencing_token=run.fencing_token,
    )

    validate_request(
        request,
        run,
        connection,
        repository.credential_value,
        (),
        repository.control_value,
        NOW,
    )

    with pytest.raises(CapabilityError, match="resource boundary"):
        validate_request(
            request,
            run,
            connection.model_copy(
                update={"allowed_resources": ("provider:credentials:another-key",)}
            ),
            repository.credential_value,
            (),
            repository.control_value,
            NOW,
        )
