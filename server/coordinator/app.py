from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from time import monotonic
from typing import Annotated

from agents.continuity import AgentContinuityService
from agents.fleet import AgentFleetService
from agents.runtime import AgentRuntimeService
from agents.storage import AgentRepository
from broker import CapabilitySigner, ConnectorRegistry
from broker.evidence import GcsEvidenceSink
from browser.compute import BrowserVmManager
from browser.service import BrowserService
from browser.storage import FirestoreBrowserRepository
from connectors.cloudrun import CloudRunConnector
from connectors.google import GoogleRestClient
from connectors.http import HttpProviderConnector
from connectors.secrets import SecretManagerConnector
from contracts import (
    ConnectionInterface,
    ConnectionRole,
    StageExecutionRequest,
    StageExecutionResult,
    StageExecutionStatus,
)
from core.audit import AuditWriter
from core.auth import (
    AccessControl,
    AuthenticatedIdentity,
    FirestoreAccessRepository,
    GoogleTokenVerifier,
    Permission,
)
from core.generation import GenerationService
from core.incident import IncidentService
from core.notification import NotificationService
from core.storage import (
    FirestoreAuditRepository,
    FirestoreCatalog,
    FirestoreGenerationRepository,
    FirestoreIncidentRepository,
    FirestoreNotificationRepository,
)
from fastapi import Depends, FastAPI, Header, Request
from google.cloud.firestore_v1 import AsyncClient
from telemetry import instrument, record
from verifier import ProbeExecutor, VerificationService
from verifier.storage import FirestoreVerificationRepository

from coordinator.broker import McpBrokerClient
from coordinator.browser import BrowserStepExecutor
from coordinator.config import CoordinatorSettings
from coordinator.service import StageCoordinator


class Runtime:
    def __init__(
        self,
        coordinator: StageCoordinator,
        access: AccessControl,
        identities: GoogleTokenVerifier,
        google: GoogleRestClient,
        firestore: AsyncClient,
    ) -> None:
        self.coordinator = coordinator
        self.access = access
        self.identities = identities
        self.google = google
        self.firestore = firestore

    async def close(self) -> None:
        self.firestore.close()  # type: ignore[no-untyped-call]
        await self.google.close()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings = CoordinatorSettings()
    firestore = AsyncClient(project=settings.project_id, database=settings.firestore_database)
    catalog = FirestoreCatalog(firestore)
    google = GoogleRestClient()
    secrets = SecretManagerConnector(google)
    with await secrets.access(settings.capability_secret) as secret:
        signer = CapabilitySigner(secret.bytes())
    evidence = GcsEvidenceSink(google, firestore, settings.evidence_bucket, settings.region)
    connectors = ConnectorRegistry()
    connectors.register(
        ConnectionRole.SECRET_STORE,
        ConnectionInterface.API,
        "google-secret-manager",
        secrets,
    )
    connectors.register(
        ConnectionRole.PROVIDER,
        ConnectionInterface.API,
        "*",
        HttpProviderConnector(secrets),
    )
    connectors.register(
        ConnectionRole.RUNTIME,
        ConnectionInterface.API,
        "cloud-run",
        CloudRunConnector(google),
    )
    agent_repository = AgentRepository(firestore)
    fleet = AgentFleetService(agent_repository)
    continuity = AgentContinuityService(
        agent_repository,
        google,
        settings.project_id,
        settings.firestore_database,
        _now,
    )
    agent_runtime = AgentRuntimeService(fleet, continuity, google, settings.project_id, _now)
    browser_service = BrowserService(FirestoreBrowserRepository(firestore), _now)
    browser = BrowserStepExecutor(
        catalog,
        browser_service,
        BrowserVmManager(
            google,
            settings.project_id,
            settings.zone,
            settings.browser_template,
            signer.encoded_public_key,
            settings.evidence_bucket,
            settings.region,
            settings.browser_image,
        ),
        signer,
    )
    verifier = VerificationService(
        FirestoreVerificationRepository(firestore),
        ProbeExecutor(evidence, google, connectors),
        _now,
    )
    coordinator = StageCoordinator(
        catalog,
        McpBrokerClient(settings.broker_url, signer, catalog),
        browser,
        agent_runtime,
        verifier,
        GenerationService(FirestoreGenerationRepository(firestore), _now),
        IncidentService(
            FirestoreIncidentRepository(firestore),
            _now,
            audit=AuditWriter(FirestoreAuditRepository(firestore), settings.region, _now),
        ),
        evidence,
        AuditWriter(FirestoreAuditRepository(firestore), settings.region, _now),
        _now,
        notifications=NotificationService(FirestoreNotificationRepository(firestore), _now),
    )
    app.state.runtime = Runtime(
        coordinator,
        AccessControl(FirestoreAccessRepository(firestore)),
        GoogleTokenVerifier(settings.oidc_audience),
        google,
        firestore,
    )
    try:
        yield
    finally:
        await app.state.runtime.close()


app = FastAPI(title="FireKey Stage Coordinator", docs_url=None, lifespan=lifespan)
instrument(app, "firekey-coordinator")


async def identity(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> AuthenticatedIdentity:
    if authorization is None:
        raise ValueError("bearer identity token is required")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise ValueError("authorization must use a bearer token")
    runtime: Runtime = request.app.state.runtime
    return await runtime.identities.verify(token)


@app.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/stages/execute", response_model=StageExecutionResult)
async def execute(
    body: StageExecutionRequest,
    actor: Annotated[AuthenticatedIdentity, Depends(identity)],
    request: Request,
) -> StageExecutionResult:
    started = monotonic()
    runtime: Runtime = request.app.state.runtime
    await runtime.access.require(actor, body.organisation_id, Permission.RUN_WRITE)
    result = await runtime.coordinator.execute(body)
    record(
        "stage.execute",
        (
            "paused"
            if result.status is StageExecutionStatus.PAUSED
            else "succeeded"
            if result.status in {StageExecutionStatus.SUCCEEDED, StageExecutionStatus.RECOVERED}
            else "failed"
        ),
        monotonic() - started,
        stage=result.stage.value,
    )
    return result


def _now() -> datetime:
    return datetime.now(UTC)
