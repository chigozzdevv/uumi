import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, cast

from agents.continuity import AgentContinuityService
from agents.fleet import AgentFleetService
from agents.runtime import AgentRuntimeService
from agents.storage import AgentRepository
from broker import CapabilitySigner
from browser.access import BrowserAccessService
from browser.service import BrowserService
from browser.storage import FirestoreBrowserRepository
from connectors.google import GoogleRestClient
from connectors.secrets import SecretManagerConnector
from connectors.storage import GcsUploadConnector
from connectors.video import VideoIntelligenceConnector
from core.approval import ApprovalService
from core.audit import AuditWriter
from core.auth import (
    AccessControl,
    AuthenticatedIdentity,
    FirestoreAccessRepository,
    GoogleTokenVerifier,
    IdentityTokenVerifier,
)
from core.config import Settings
from core.errors import AuthenticationError
from core.incident import IncidentService
from core.inventory import InventoryService
from core.notification import NotificationService
from core.playbook import PlaybookService, WalkthroughService
from core.policy import PolicyService
from core.storage import (
    FirestoreApprovalRepository,
    FirestoreAuditRepository,
    FirestoreCatalog,
    FirestoreIncidentRepository,
    FirestoreInventoryRepository,
    FirestoreNotificationRepository,
    FirestorePlaybookRepository,
    FirestorePolicyRepository,
    FirestoreProbeRepository,
    FirestoreRunRepository,
    FirestoreWalkthroughRepository,
)
from core.verification import ProbeService
from core.workflow import RunWorkflow
from fastapi import Depends, Header, Request
from google.cloud.firestore_v1 import AsyncClient


@dataclass(frozen=True, slots=True)
class ApiServices:
    workflow: RunWorkflow
    access: AccessControl
    tokens: IdentityTokenVerifier
    inventory: InventoryService | None = None
    playbooks: PlaybookService | None = None
    approvals: ApprovalService | None = None
    incidents: IncidentService | None = None
    browsers: BrowserAccessService | None = None
    agents: AgentRuntimeService | None = None
    agent_repository: AgentRepository | None = None
    agent_continuity: AgentContinuityService | None = None
    walkthroughs: WalkthroughService | None = None
    policies: PolicyService | None = None
    probes: ProbeService | None = None
    notifications: NotificationService | None = None
    audit: AuditWriter | None = None


def build_services(settings: Settings | None = None) -> ApiServices:
    configured = settings or Settings()
    client = AsyncClient(
        project=configured.project_id,
        database=configured.firestore_database,
    )
    google = GoogleRestClient()
    secret_manager = SecretManagerConnector(google)

    async def load_signer() -> CapabilitySigner:
        secret = await secret_manager.access(configured.capability_secret)
        try:
            return CapabilitySigner(secret.bytes())
        finally:
            secret.clear()

    workflow = RunWorkflow(FirestoreRunRepository(client))
    agent_repository = AgentRepository(client)
    continuity = AgentContinuityService(
        agent_repository,
        google,
        configured.project_id,
        configured.firestore_database,
        _now,
    )
    notifications = NotificationService(FirestoreNotificationRepository(client), _now)
    audit = AuditWriter(FirestoreAuditRepository(client), configured.region, _now)
    return ApiServices(
        workflow=workflow,
        access=AccessControl(FirestoreAccessRepository(client)),
        tokens=GoogleTokenVerifier(configured.oidc_audience),
        inventory=InventoryService(FirestoreInventoryRepository(client)),
        playbooks=PlaybookService(FirestorePlaybookRepository(client), _now, workflow),
        approvals=ApprovalService(FirestoreApprovalRepository(client), _now, notifications, audit),
        incidents=IncidentService(
            FirestoreIncidentRepository(client),
            _now,
            FirestoreInventoryRepository(client),
            workflow,
            notifications,
            audit,
        ),
        browsers=BrowserAccessService(
            FirestoreCatalog(client),
            BrowserService(FirestoreBrowserRepository(client), _now),
            load_signer,
            configured.browser_gateway_url,
            _now,
        ),
        agents=AgentRuntimeService(
            AgentFleetService(agent_repository),
            continuity,
            google,
            configured.project_id,
            _now,
        ),
        agent_repository=agent_repository,
        agent_continuity=continuity,
        walkthroughs=WalkthroughService(
            FirestoreWalkthroughRepository(client),
            GcsUploadConnector(google, configured.walkthrough_bucket),
            VideoIntelligenceConnector(google),
            configured.walkthrough_bucket,
            _now,
        ),
        policies=PolicyService(FirestorePolicyRepository(client), _now),
        probes=ProbeService(FirestoreProbeRepository(client), _now),
        notifications=notifications,
        audit=audit,
    )


def services(request: Request) -> ApiServices:
    return cast(ApiServices, request.app.state.services)


async def authenticated_identity(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> AuthenticatedIdentity:
    if authorization is None:
        raise AuthenticationError("bearer identity token is required")
    scheme, separator, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not separator or not token:
        raise AuthenticationError("authorization must use a bearer token")
    return await services(request).tokens.verify(token)


def idempotency_key(
    value: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=8, max_length=256),
    ],
) -> str:
    return value


def command_id(
    identity: AuthenticatedIdentity,
    organisation_id: str,
    key: str,
) -> str:
    payload = f"{identity.subject}\0{organisation_id}\0{key}".encode()
    return f"cmd_{hashlib.sha256(payload).hexdigest()[:40]}"


def required[T](value: T | None, name: str) -> T:
    if value is None:
        raise RuntimeError(f"API service {name} is not configured")
    return value


def _now() -> datetime:
    return datetime.now(UTC)


Identity = Annotated[AuthenticatedIdentity, Depends(authenticated_identity)]
IdempotencyKey = Annotated[str, Depends(idempotency_key)]
