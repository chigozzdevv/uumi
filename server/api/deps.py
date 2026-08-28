import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, cast

from agents.armor import ModelArmorGuard
from agents.continuity import AgentContinuityService
from agents.fleet import AgentFleetService
from agents.runtime import AgentRuntimeService
from agents.storage import AgentRepository
from broker import CapabilitySigner, GcsEvidenceSink
from browser.access import BrowserAccessService
from browser.compute import BrowserVmManager
from browser.secret import BrowserSecretAccessService
from browser.service import BrowserService
from browser.setup import BrowserSetupApi, BrowserSetupService, WorkflowRunResumer
from browser.storage import FirestoreBrowserRepository
from connectors.cloudrun import CloudRunConnector
from connectors.github import GitHubOnboardingConnector
from connectors.google import GoogleRestClient
from connectors.googlecloud import GoogleCloudOnboardingConnector
from connectors.http import HttpProviderConnector
from connectors.secrets import SecretManagerConnector
from connectors.storage import GcsUploadConnector
from connectors.video import VideoIntelligenceConnector
from core.account import AccountService, FirestoreAccountRepository
from core.approval import ApprovalService
from core.audit import AuditWriter
from core.auth import (
    AccessControl,
    AuthenticatedIdentity,
    CompositeTokenVerifier,
    FirebaseTokenVerifier,
    GoogleTokenVerifier,
    IdentityTokenVerifier,
)
from core.config import Settings
from core.errors import AuthenticationError
from core.github import FirestoreGitHubRepository, GitHubOnboardingService
from core.googlecloud import (
    FirestoreGoogleCloudRepository,
    GoogleCloudBrokerValidator,
    GoogleCloudOnboardingService,
)
from core.history import RunHistoryService
from core.incident import IncidentService
from core.inventory import InventoryService
from core.notification import EmailDeliveryConfiguration, NotificationService
from core.overview import OverviewService
from core.playbook import PlaybookService, WalkthroughService
from core.storage import (
    FirestoreApprovalRepository,
    FirestoreAuditRepository,
    FirestoreCatalog,
    FirestoreIncidentRepository,
    FirestoreInventoryRepository,
    FirestoreNotificationRepository,
    FirestorePlaybookRepository,
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
    probes: ProbeService | None = None
    notifications: NotificationService | None = None
    audit: AuditWriter | None = None
    overview: OverviewService | None = None
    browser_setup: BrowserSetupApi | None = None
    github: GitHubOnboardingService | None = None
    google_cloud: GoogleCloudOnboardingService | None = None
    history: RunHistoryService | None = None
    accounts: AccountService | None = None


def build_services(settings: Settings | None = None) -> ApiServices:
    configured = settings or Settings()
    client = AsyncClient(
        project=configured.project_id,
        database=configured.firestore_database,
    )
    account_repository = FirestoreAccountRepository(client, _now)
    google = GoogleRestClient()
    catalog = FirestoreCatalog(client)
    secret_manager = SecretManagerConnector(google)
    provider_connector = HttpProviderConnector(secret_manager)

    async def load_signer() -> CapabilitySigner:
        secret = await secret_manager.access(configured.capability_secret)
        try:
            return CapabilitySigner(secret.bytes())
        finally:
            secret.clear()

    runs = FirestoreRunRepository(client)
    inventory_repository = FirestoreInventoryRepository(client)
    incident_repository = FirestoreIncidentRepository(client)
    approval_repository = FirestoreApprovalRepository(client)
    workflow = RunWorkflow(runs)
    agent_repository = AgentRepository(client)
    continuity = AgentContinuityService(
        agent_repository,
        google,
        configured.project_id,
        configured.firestore_database,
        _now,
    )
    email_delivery = (
        EmailDeliveryConfiguration(
            configured.notification_email_secret_version,
            configured.notification_email_sender,
        )
        if configured.notification_email_secret_version and configured.notification_email_sender
        else None
    )
    notifications = NotificationService(
        FirestoreNotificationRepository(client),
        _now,
        email_delivery,
    )
    audit = AuditWriter(FirestoreAuditRepository(client), configured.region, _now)
    evidence = (
        GcsEvidenceSink(
            google,
            client,
            configured.evidence_bucket,
            configured.region,
        )
        if configured.evidence_bucket
        else None
    )
    guard = (
        ModelArmorGuard(
            google,
            configured.model_armor_template,
            evidence,
            _now,
            configured.model_armor_response_template or configured.model_armor_template,
        )
        if evidence is not None and configured.model_armor_template
        else None
    )
    browser_setup = None
    github = None
    google_cloud = None
    if all(
        (
            configured.browser_zone,
            configured.browser_template,
            configured.browser_worker_image,
            configured.capability_public_key,
            configured.evidence_bucket,
            configured.model_armor_template,
        )
    ):
        browser_setup = BrowserSetupService(
            catalog,
            inventory_repository,
            BrowserVmManager(
                google,
                configured.project_id,
                configured.browser_zone,
                configured.browser_template,
                configured.capability_public_key,
                configured.evidence_bucket,
                configured.region,
                configured.browser_worker_image,
                configured.model_armor_template,
                configured.model_armor_response_template or configured.model_armor_template,
            ),
            secret_manager,
            configured.browser_gateway_url,
            configured.project_id,
            _now,
            runs=WorkflowRunResumer(workflow, _now),
        )
    if all(
        (
            configured.github_app_slug,
            configured.github_client_id,
            configured.github_client_secret,
            configured.github_callback_url,
        )
    ):
        github = GitHubOnboardingService(
            FirestoreGitHubRepository(client),
            GitHubOnboardingConnector(
                configured.github_app_slug,
                configured.github_client_id,
                configured.github_client_secret,
                configured.github_callback_url,
                secret_manager,
            ),
            configured.github_app_slug,
            configured.github_client_id,
            configured.github_callback_url,
            _now,
        )
    if all(
        (
            configured.google_cloud_client_id,
            configured.google_cloud_client_secret,
            configured.google_cloud_callback_url,
        )
    ):
        google_cloud = GoogleCloudOnboardingService(
            FirestoreGoogleCloudRepository(client),
            GoogleCloudOnboardingConnector(
                configured.google_cloud_client_id,
                configured.google_cloud_client_secret,
                configured.google_cloud_callback_url,
                secret_manager,
            ),
            configured.google_cloud_client_id,
            configured.google_cloud_callback_url,
            _now,
            inventory_repository,
            GoogleCloudBrokerValidator(configured.broker_url),
            configured.broker_service_account,
        )
    return ApiServices(
        workflow=workflow,
        access=AccessControl(account_repository),
        tokens=CompositeTokenVerifier(
            (
                FirebaseTokenVerifier(configured.project_id),
                GoogleTokenVerifier(configured.oidc_audience),
            )
        ),
        inventory=InventoryService(
            inventory_repository,
            _now,
            provider_metadata=provider_connector,
            credential_verifier=provider_connector,
            secret_metadata=secret_manager,
            runtime_metadata=CloudRunConnector(google),
        ),
        playbooks=PlaybookService(
            FirestorePlaybookRepository(client),
            _now,
            inventory_repository,
        ),
        approvals=ApprovalService(approval_repository, _now, notifications, audit),
        incidents=IncidentService(
            incident_repository,
            _now,
            inventory_repository,
            workflow,
            notifications,
            audit,
        ),
        browsers=BrowserAccessService(
            catalog,
            BrowserService(FirestoreBrowserRepository(client), _now),
            load_signer,
            configured.browser_gateway_url,
            _now,
            BrowserSecretAccessService(catalog, google, load_signer, _now),
        ),
        agents=AgentRuntimeService(
            AgentFleetService(agent_repository),
            continuity,
            google,
            guard,
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
        probes=ProbeService(FirestoreProbeRepository(client), _now),
        notifications=notifications,
        audit=audit,
        overview=OverviewService(
            inventory_repository,
            runs,
            incident_repository,
            approval_repository,
            _now,
        ),
        browser_setup=browser_setup,
        github=github,
        google_cloud=google_cloud,
        history=RunHistoryService(catalog, evidence),
        accounts=AccountService(account_repository, _now),
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
