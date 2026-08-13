from datetime import UTC, datetime, timedelta

import httpx
import pytest
from broker import ConnectorRegistry
from connectors import ConnectorContext
from connectors.google import GoogleRestClient
from contracts import (
    Connection,
    ConnectionKind,
    ConnectionStatus,
    Evidence,
    ProbeDefinition,
    ProbeKind,
    RunStatus,
    Stage,
    VerificationStatus,
)
from google.oauth2.credentials import Credentials
from testkit import make_run
from verifier import ProbeExecutor

NOW = datetime(2026, 8, 13, 12, tzinfo=UTC)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class EvidenceSink:
    def __init__(self) -> None:
        self.values: list[bytes] = []

    async def store(
        self,
        organisation_id: str,
        run_id: str,
        kind: str,
        content: bytes,
        content_type: str,
        now: datetime,
    ) -> Evidence:
        self.values.append(content)
        return Evidence(
            id=f"evidence_{len(self.values)}",
            organisation_id=organisation_id,
            kind=kind,
            resource=f"gs://evidence/{run_id}/{len(self.values)}",
            digest=__import__("hashlib").sha256(content).hexdigest(),
            content_type=content_type,
            size=len(content),
            created_at=now,
            region="us-east1",
        )


@pytest.mark.anyio
async def test_http_probe_requires_exact_generation_telemetry() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"x-firekey-generation-id": "generation_new"},
            json={"healthy": True, "service": {"ready": True}},
        )

    sink = EvidenceSink()
    executor = ProbeExecutor(
        sink,  # type: ignore[arg-type]
        _google(),
        ConnectorRegistry(),
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    definition = ProbeDefinition(
        id="probe_one",
        organisation_id="org_one",
        kind=ProbeKind.HTTP,
        connection_id="telemetry_one",
        target="https://service.example.com/health",
        expected_generation_id="generation_new",
        required_fields={"healthy": True, "service.ready": True},
    )

    result = await executor.execute(definition, _connection(), _context(), lambda: NOW)

    assert result.status is VerificationStatus.PASSED
    assert result.generation_id == "generation_new"
    assert result.checks == frozenset(
        {"http-status-matched", "response-fields-matched", "generation-identified"}
    )
    assert b"healthy" not in sink.values[0]


@pytest.mark.anyio
async def test_http_probe_fails_closed_on_generation_mismatch() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"x-firekey-generation-id": "generation_old"},
            json={"healthy": True},
        )

    sink = EvidenceSink()
    executor = ProbeExecutor(
        sink,  # type: ignore[arg-type]
        _google(),
        ConnectorRegistry(),
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    definition = ProbeDefinition(
        id="probe_one",
        organisation_id="org_one",
        kind=ProbeKind.HTTP,
        connection_id="telemetry_one",
        target="https://service.example.com/health",
        expected_generation_id="generation_new",
    )

    result = await executor.execute(definition, _connection(), _context(), lambda: NOW)

    assert result.status is VerificationStatus.FAILED
    assert result.error is not None and "generation" in result.error


def _google() -> GoogleRestClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    return GoogleRestClient(
        credentials=Credentials(token="token"),  # type: ignore[no-untyped-call]
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def _connection() -> Connection:
    return Connection(
        id="telemetry_one",
        organisation_id="org_one",
        kind=ConnectionKind.TELEMETRY,
        provider="google-cloud-logging",
        display_name="Logging",
        auth_reference="projects/project-one/serviceAccounts/verifier",
        capabilities=frozenset({"telemetry.queryHealth"}),
        allowed_resources=("service.example.com",),
        status=ConnectionStatus.READY,
        region="us-east1",
        created_at=NOW,
        updated_at=NOW,
    )


def _context() -> ConnectorContext:
    run = make_run(NOW).model_copy(
        update={
            "status": RunStatus.RUNNING,
            "stage": Stage.VERIFY,
            "fencing_token": 1,
            "lease": {
                "owner_id": "verifier_one",
                "fencing_token": 1,
                "expires_at": NOW + timedelta(minutes=5),
            },
        }
    )
    return ConnectorContext(
        request_id="verification_one",
        agent_id="verifier_one",
        connection=_connection(),
        run=run,
        now=NOW,
        idempotency_key="verification_one",
    )
