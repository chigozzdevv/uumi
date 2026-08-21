import base64
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from broker import ConnectorRegistry
from connectors import ConnectorContext
from connectors.google import GoogleRestClient
from connectors.http import HttpProviderConnector
from connectors.secrets import SecretManagerConnector
from contracts import (
    Connection,
    ConnectionAuthorization,
    ConnectionInterface,
    ConnectionRole,
    ConnectionStatus,
    DownstreamConfirmation,
    Evidence,
    GenerationBinding,
    ProbeDefinition,
    ProbeKind,
    RunStatus,
    Stage,
    TelemetryThresholds,
    VerificationStatus,
)
from google.oauth2.credentials import Credentials
from testkit import make_http_provider_api, make_run
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
        generation_binding=GenerationBinding.TARGET,
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
        generation_binding=GenerationBinding.TARGET,
    )

    result = await executor.execute(definition, _connection(), _context(), lambda: NOW)

    assert result.status is VerificationStatus.FAILED
    assert result.error is not None and "generation" in result.error


@pytest.mark.anyio
async def test_email_probe_requires_confirmed_downstream_result() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if request.url.path == "/password-reset":
            return httpx.Response(
                202,
                headers={"x-firekey-generation-id": "generation_new"},
                json={"receipt": {"id": "message_one"}},
            )
        return httpx.Response(200, json={"delivered": True, "template": "password-reset"})

    sink = EvidenceSink()
    executor = ProbeExecutor(
        sink,  # type: ignore[arg-type]
        _google(),
        ConnectorRegistry(),
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    definition = ProbeDefinition(
        id="probe_email",
        organisation_id="org_one",
        kind=ProbeKind.EMAIL,
        connection_id="telemetry_one",
        target="https://candidate.example.com/password-reset",
        method="POST",
        expected_status=(202,),
        expected_generation_id="generation_new",
        generation_binding=GenerationBinding.TARGET,
        confirmation=DownstreamConfirmation(
            target="https://inbox.example.com/messages/{correlation_id}",
            required_fields={"delivered": True, "template": "password-reset"},
            correlation_field="receipt.id",
        ),
    )

    result = await executor.execute(definition, _connection(), _context(), lambda: NOW)

    assert result.status is VerificationStatus.PASSED
    assert result.observations["downstream_confirmed"] is True
    assert "downstream-result-confirmed" in result.checks
    assert calls == 2


@pytest.mark.anyio
async def test_telemetry_probe_binds_generation_and_thresholds() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(__import__("json").loads(request.content))
        return httpx.Response(
            200,
            json={
                "entries": [
                    {
                        "insertId": "entry_one",
                        "severity": "INFO",
                        "jsonPayload": {
                            "firekey.credential_generation": "generation_new",
                            "authentication_failure": False,
                        },
                    }
                ]
            },
        )

    sink = EvidenceSink()
    google = GoogleRestClient(
        credentials=Credentials(token="token"),  # type: ignore[no-untyped-call]
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    google._connection_credentials["verifier@project-one.iam.gserviceaccount.com"] = Credentials(
        token="token"
    )  # type: ignore[no-untyped-call]
    executor = ProbeExecutor(sink, google, ConnectorRegistry())  # type: ignore[arg-type]
    definition = ProbeDefinition(
        id="probe_telemetry",
        organisation_id="org_one",
        kind=ProbeKind.TELEMETRY,
        connection_id="telemetry_one",
        target="projects/project-one",
        expected_generation_id="generation_new",
        generation_binding=GenerationBinding.TARGET,
        telemetry=TelemetryThresholds(minimum_count=1, window_seconds=300),
        headers={"x-firekey-log-filter": 'resource.type="cloud_run_revision"'},
    )

    result = await executor.execute(definition, _connection(), _context(), lambda: NOW)

    assert result.status is VerificationStatus.PASSED
    assert result.generation_id == "generation_new"
    assert "firekey.credential_generation" in str(captured["filter"])
    assert "telemetry-generation-bound" in result.checks


@pytest.mark.anyio
async def test_negative_telemetry_probe_only_passes_when_old_generation_has_no_use() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"entries": []})

    sink = EvidenceSink()
    google = GoogleRestClient(
        credentials=Credentials(token="token"),  # type: ignore[no-untyped-call]
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    google._connection_credentials["verifier@project-one.iam.gserviceaccount.com"] = Credentials(
        token="token"
    )  # type: ignore[no-untyped-call]
    executor = ProbeExecutor(sink, google, ConnectorRegistry())  # type: ignore[arg-type]
    definition = ProbeDefinition(
        id="probe_old_use",
        organisation_id="org_one",
        kind=ProbeKind.TELEMETRY,
        connection_id="telemetry_one",
        target="projects/project-one",
        expected_generation_id="generation_old",
        generation_binding=GenerationBinding.CURRENT,
        telemetry=TelemetryThresholds(minimum_count=1, window_seconds=300),
        negative=True,
    )

    result = await executor.execute(definition, _connection(), _context(), lambda: NOW)

    assert result.status is VerificationStatus.PASSED
    assert "telemetry-no-old-use" in result.checks


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("negative", "status", "check"),
    [
        (False, 200, "credential-accepted"),
        (True, 401, "credential-rejected"),
    ],
)
async def test_credential_probe_authenticates_the_bound_secret_without_persisting_it(
    negative: bool,
    status: int,
    check: str,
) -> None:
    def google_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/versions/1:access")
        return httpx.Response(
            200,
            json={"payload": {"data": base64.b64encode(b"workload-secret").decode()}},
        )

    def provider_handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer workload-secret"
        return httpx.Response(status)

    google = GoogleRestClient(
        credentials=Credentials(token="token"),  # type: ignore[no-untyped-call]
        client=httpx.AsyncClient(transport=httpx.MockTransport(google_handler)),
    )
    secret_credentials = Credentials(token="token")  # type: ignore[no-untyped-call]
    google._connection_credentials["secret-reader@project-one.iam.gserviceaccount.com"] = (
        secret_credentials
    )
    secrets = SecretManagerConnector(google)
    registry = ConnectorRegistry()
    registry.register(
        ConnectionRole.PROVIDER,
        ConnectionInterface.API,
        "vendor",
        HttpProviderConnector(
            secrets,
            httpx.AsyncClient(transport=httpx.MockTransport(provider_handler)),
        ),
    )
    sink = EvidenceSink()
    executor = ProbeExecutor(sink, google, registry)  # type: ignore[arg-type]
    definition = ProbeDefinition(
        id="probe_credential",
        organisation_id="org_one",
        kind=ProbeKind.CREDENTIAL,
        connection_id="provider_one",
        target="provider-key-one",
        secret_reference="projects/project-one/secrets/workload/versions/1",
        secret_connection_id="secret_one",
        expected_generation_id="generation_old" if negative else "generation_new",
        generation_binding=(GenerationBinding.CURRENT if negative else GenerationBinding.TARGET),
        negative=negative,
    )

    result = await executor.execute(
        definition,
        _provider_connection(),
        _context(),
        lambda: NOW,
        _secret_connection(),
    )

    assert result.status is VerificationStatus.PASSED
    assert result.checks == frozenset({check})
    assert all(b"workload-secret" not in value for value in sink.values)
    await google.close()


def _google() -> GoogleRestClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    google = GoogleRestClient(
        credentials=Credentials(token="token"),  # type: ignore[no-untyped-call]
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    google._connection_credentials["verifier@project-one.iam.gserviceaccount.com"] = Credentials(
        token="token"
    )  # type: ignore[no-untyped-call]
    return google


def _connection() -> Connection:
    return Connection(
        id="telemetry_one",
        organisation_id="org_one",
        platform="google-cloud-logging",
        display_name="Logging",
        roles=frozenset({ConnectionRole.TELEMETRY}),
        interface=ConnectionInterface.API,
        authorization=ConnectionAuthorization.WORKLOAD_IDENTITY,
        authorization_reference=(
            "workload-identity://verifier@project-one.iam.gserviceaccount.com"
        ),
        capabilities=frozenset({"telemetry.queryHealth"}),
        allowed_resources=("service.example.com",),
        status=ConnectionStatus.READY,
        region="us-east1",
        created_at=NOW,
        updated_at=NOW,
    )


def _provider_connection() -> Connection:
    return Connection(
        id="provider_one",
        organisation_id="org_one",
        platform="vendor",
        display_name="Vendor",
        roles=frozenset({ConnectionRole.PROVIDER}),
        interface=ConnectionInterface.API,
        authorization=ConnectionAuthorization.API_KEY,
        authorization_reference="projects/project-one/secrets/admin/versions/1",
        capabilities=frozenset({"provider.testCredential"}),
        allowed_resources=("vendor-account-one",),
        http=make_http_provider_api("https://api.vendor.example/v1"),
        status=ConnectionStatus.READY,
        region="us-east1",
        created_at=NOW,
        updated_at=NOW,
    )


def _secret_connection() -> Connection:
    return Connection(
        id="secret_one",
        organisation_id="org_one",
        platform="google-secret-manager",
        display_name="Secret Manager",
        roles=frozenset({ConnectionRole.SECRET_STORE}),
        interface=ConnectionInterface.API,
        authorization=ConnectionAuthorization.WORKLOAD_IDENTITY,
        authorization_reference=(
            "workload-identity://secret-reader@project-one.iam.gserviceaccount.com"
        ),
        capabilities=frozenset({"secretStore.getVersion"}),
        allowed_resources=("projects/project-one/secrets/workload",),
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
