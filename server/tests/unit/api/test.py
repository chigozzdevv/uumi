import itertools
from datetime import UTC, datetime, timedelta
from typing import cast

import httpx
import pytest
from api.app import create_app
from api.deps import ApiServices
from contracts import (
    Application,
    Approval,
    ApprovalDecision,
    ApprovalEvidenceKind,
    ApprovalEvidenceSnapshot,
    AuditEvent,
    ComputerUseActivity,
    ComputerUseActivityPhase,
    ComputerUseActivityStatus,
    Connection,
    ConnectionAuthorization,
    ConnectionInterface,
    ConnectionRole,
    ConnectionStatus,
    ConsumerBinding,
    ConsumerService,
    ControlVersion,
    CredentialGeneration,
    Environment,
    Incident,
    IncidentStatus,
    IngestionEvent,
    ManagedCredential,
    Playbook,
    PlaybookDraft,
    PlaybookEffect,
    PlaybookVersion,
    ProbeVersion,
    RotationHistory,
    RunStageActivity,
    SetupSession,
    SetupStatus,
    Severity,
    SourceResource,
    Stage,
    StageExecutionStatus,
)
from contracts.incident import Confidence
from core.approval import ApprovalService
from core.audit import AuditWriter
from core.auth import (
    AccessControl,
    AuthenticatedIdentity,
    PrincipalGrant,
    Role,
)
from core.errors import AuthenticationError, ResourceNotFoundError
from core.history import RunHistoryService
from core.incident import IncidentService
from core.inventory import InventoryService
from core.overview import OverviewService
from core.playbook import PlaybookService
from core.workflow import RunWorkflow
from fastapi import FastAPI
from testkit import MemoryRunRepository, make_control_version, make_http_provider_api

NOW = datetime(2026, 8, 13, 12, tzinfo=UTC)
IDENTITY = AuthenticatedIdentity(
    subject="107777777777777777777",
    issuer="https://accounts.google.com",
    email="workflow@example.iam.gserviceaccount.com",
)


class TokenVerifier:
    async def verify(self, token: str) -> AuthenticatedIdentity:
        if token != "valid-token":
            raise AuthenticationError("identity token is invalid")
        return IDENTITY


class History:
    async def get(self, organisation_id: str, run_id: str) -> RotationHistory:
        assert organisation_id == "org_one"
        return RotationHistory(
            run_id=run_id,
            stages=(
                RunStageActivity(
                    id="stage_one",
                    stage=Stage.PREFLIGHT,
                    status=StageExecutionStatus.SUCCEEDED,
                    checks=("credential-known",),
                    evidence_count=1,
                    started_at=NOW,
                    completed_at=NOW + timedelta(seconds=2),
                ),
            ),
            computer_use=(
                ComputerUseActivity(
                    id="activity_one",
                    organisation_id="org_one",
                    session_id="browser_one",
                    run_id=run_id,
                    step_id="step_one",
                    stage=Stage.CREATE,
                    turn=1,
                    phase=ComputerUseActivityPhase.INPUT,
                    status=ComputerUseActivityStatus.SENT,
                    effect=PlaybookEffect.CREATE_CREDENTIAL,
                    prompt="Create the replacement credential.",
                    instruction="Do not handle secrets.",
                    image_reference="gs://evidence/input#1",
                    image_digest="a" * 64,
                    recorded_at=NOW,
                ),
            ),
        )

    async def input_image(
        self, organisation_id: str, run_id: str, activity_id: str
    ) -> tuple[bytes, str]:
        assert (organisation_id, run_id, activity_id) == (
            "org_one",
            "run_one",
            "activity_one",
        )
        return b"\x89PNG\r\n\x1a\n", "image/png"

    async def approval_evidence(
        self, organisation_id: str, approval_id: str
    ) -> ApprovalEvidenceSnapshot:
        assert organisation_id == "org_one"
        return ApprovalEvidenceSnapshot(
            approval_id=approval_id,
            evidence_hash="c" * 64,
            kind=ApprovalEvidenceKind.VERIFICATION,
            status="passed",
            checks=("provider-valid", "store-valid", "deployment-valid"),
            evidence_count=3,
            recorded_at=NOW,
        )


class AccessRepository:
    def __init__(self, role: Role) -> None:
        self._role = role

    async def get(
        self,
        organisation_id: str,
        identity: AuthenticatedIdentity,
    ) -> PrincipalGrant | None:
        if organisation_id != "org_one" or identity != IDENTITY:
            return None
        return PrincipalGrant(subject=identity.subject, roles=frozenset({self._role}))


class IncidentRepository:
    def __init__(self, credential_id: str | None = None) -> None:
        self.incidents: tuple[Incident, ...] = (
            _incident("incident_one", IncidentStatus.NEW, NOW, credential_id),
            _incident("incident_two", IncidentStatus.ACTION, NOW + timedelta(minutes=5)),
        )

    async def list_incidents(self, organisation_id: str, limit: int) -> tuple[Incident, ...]:
        return self.incidents[:limit]

    async def count_incidents(
        self, organisation_id: str, statuses: frozenset[IncidentStatus]
    ) -> int:
        return sum(1 for incident in self.incidents if incident.status in statuses)

    async def ingest(self, incident: Incident, event: IngestionEvent) -> tuple[Incident, bool]:
        raise AssertionError("not used")

    async def get(self, organisation_id: str, incident_id: str) -> Incident:
        return next(item for item in self.incidents if item.id == incident_id)

    async def correlate(
        self,
        organisation_id: str,
        incident_id: str,
        expected_revision: int,
        candidates: tuple[object, ...],
        credential_id: str | None,
        updated_at: datetime,
    ) -> Incident:
        raise AssertionError("not used")

    async def link_run(
        self,
        organisation_id: str,
        incident_id: str,
        expected_revision: int,
        credential_id: str,
        run_id: str,
        updated_at: datetime,
    ) -> Incident:
        raise AssertionError("not used")

    async def advance_run(
        self,
        organisation_id: str,
        run_id: str,
        status: IncidentStatus,
        updated_at: datetime,
    ) -> tuple[Incident, ...]:
        raise AssertionError("not used")

    async def dismiss(
        self,
        organisation_id: str,
        incident_id: str,
        expected_revision: int,
        reason: str,
        updated_at: datetime,
    ) -> Incident:
        current = await self.get(organisation_id, incident_id)
        changed = current.model_copy(
            update={
                "status": IncidentStatus.DISMISSED,
                "dismissal_reason": reason,
                "updated_at": updated_at,
                "revision": current.revision + 1,
            }
        )
        self.incidents = tuple(
            changed if item.id == incident_id else item for item in self.incidents
        )
        return changed


class ApprovalRepository:
    def __init__(self, pending_run_id: str = "run_one") -> None:
        self.approvals: tuple[Approval, ...] = (
            _approval("approval_one", ApprovalDecision.PENDING, NOW, run_id=pending_run_id),
            _approval(
                "approval_two", ApprovalDecision.APPROVED, NOW + timedelta(minutes=5), decided=True
            ),
        )

    async def list_approvals(self, organisation_id: str, limit: int) -> tuple[Approval, ...]:
        return self.approvals[:limit]

    async def count_approvals(
        self,
        organisation_id: str,
        decisions: frozenset[ApprovalDecision],
        active_at: datetime | None = None,
    ) -> int:
        return sum(
            1
            for approval in self.approvals
            if approval.decision in decisions
            and (active_at is None or approval.expires_at > active_at)
        )

    async def create(self, approval: Approval, action: object) -> Approval:
        raise AssertionError("not used")

    async def decide(
        self,
        organisation_id: str,
        approval_id: str,
        expected_revision: int,
        decision: ApprovalDecision,
        actor_id: str,
        decided_at: datetime,
    ) -> Approval:
        current = next(item for item in self.approvals if item.id == approval_id)
        changed = current.model_copy(
            update={
                "decision": decision,
                "approver_id": actor_id,
                "decided_at": decided_at,
                "revision": current.revision + 1,
            }
        )
        self.approvals = tuple(
            changed if item.id == approval_id else item for item in self.approvals
        )
        return changed

    async def consume(
        self,
        organisation_id: str,
        approval_id: str,
        capability_hash: str,
        action_digest: str,
        plan_hash: str,
        evidence_hash: str,
        consumed_at: datetime,
    ) -> Approval:
        raise AssertionError("not used")


class PlaybookRepository:
    def __init__(self) -> None:
        self.playbooks: tuple[Playbook, ...] = (
            _playbook("playbook_one", NOW),
            _playbook("playbook_two", NOW + timedelta(minutes=5)),
        )

    async def list_playbooks(self, organisation_id: str, limit: int) -> tuple[Playbook, ...]:
        return self.playbooks[:limit]

    async def get(self, organisation_id: str, playbook_id: str) -> Playbook:
        return next(playbook for playbook in self.playbooks if playbook.id == playbook_id)

    async def replace(self, value: Playbook, expected_revision: int) -> Playbook:
        self.playbooks = tuple(
            value if playbook.id == value.id else playbook for playbook in self.playbooks
        )
        return value

    async def add_version(
        self,
        playbook_id: str,
        version_id: str,
        organisation_id: str,
        definition: PlaybookDraft,
        definition_digest: str,
        actor_id: str,
        created_at: datetime,
        source_ids: tuple[str, ...],
    ) -> tuple[Playbook, PlaybookVersion]:
        raise AssertionError("not used")

    async def get_version(
        self,
        organisation_id: str,
        playbook_id: str,
        version_id: str,
    ) -> PlaybookVersion:
        raise AssertionError("not used")

    async def publish(
        self,
        organisation_id: str,
        playbook_id: str,
        version_id: str,
        actor_id: str,
        published_at: datetime,
    ) -> PlaybookVersion:
        raise AssertionError("not used")


class AuditRepository:
    def __init__(self) -> None:
        self.events: tuple[AuditEvent, ...] = (
            _audit_event("audit_one", 0, "run.created", "0" * 64, NOW, run_id="run_one"),
            _audit_event(
                "audit_two",
                1,
                "approval.approved",
                "a" * 64,
                NOW + timedelta(minutes=1),
                run_id="run_one",
            ),
            _audit_event("audit_three", 2, "run.completed", "b" * 64, NOW + timedelta(minutes=2)),
        )

    async def list_events(self, organisation_id: str, limit: int) -> tuple[AuditEvent, ...]:
        return self.events[:limit]

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
            sequence=len(self.events),
            kind=kind,
            actor_id=actor_id,
            resource=resource,
            run_id=run_id,
            payload=payload,
            evidence_ids=evidence_ids,
            previous_hash=self.events[-1].event_hash if self.events else "0" * 64,
            event_hash="f" * 64,
            occurred_at=occurred_at,
            region=region,
        )
        self.events = (*self.events, event)
        return event


class InventoryRepository:
    def __init__(self) -> None:
        self.stored_connections: tuple[Connection, ...] = (
            Connection(
                id="connection_one",
                organisation_id="org_one",
                platform="sendgrid",
                display_name="SendGrid Admin",
                roles=frozenset({ConnectionRole.PROVIDER}),
                interface=ConnectionInterface.API,
                authorization=ConnectionAuthorization.API_KEY,
                authorization_reference=("projects/org-one/secrets/sendgrid-admin/versions/1"),
                capabilities=frozenset({"create", "revoke"}),
                allowed_resources=("sendgrid:*",),
                http=make_http_provider_api(),
                status=ConnectionStatus.READY,
                region="us-east1",
                created_at=NOW,
                updated_at=NOW,
            ),
        )
        self.stored_applications: tuple[Application, ...] = (
            Application(
                id="application_one",
                organisation_id="org_one",
                display_name="Acme Store",
                created_at=NOW,
                updated_at=NOW,
            ),
        )
        self.stored_environments: tuple[Environment, ...] = (
            Environment(
                id="environment_one",
                organisation_id="org_one",
                application_id="application_one",
                display_name="Production",
                production=True,
                region="us-east1",
                created_at=NOW,
                updated_at=NOW,
            ),
        )
        self.stored_credentials: tuple[ManagedCredential, ...] = (
            ManagedCredential(
                id="credential_one",
                organisation_id="org_one",
                connection_id="connection_one",
                secret_store_connection_id="connection_secret",
                secret_resource="projects/org-one/secrets/sendgrid",
                secret_reference="projects/org-one/secrets/sendgrid",
                provider="sendgrid",
                kind="api-key",
                display_name="production-password-emailer",
                control_version="control_version_one",
                created_at=NOW,
                updated_at=NOW,
            ),
        )
        self.stored_controls: tuple[ControlVersion, ...] = (
            make_control_version(
                version_id="control_version_one",
                credential_id="credential_one",
                now=NOW,
            ),
        )

    async def connections(self, organisation_id: str) -> tuple[Connection, ...]:
        return self.stored_connections

    async def applications(self, organisation_id: str) -> tuple[Application, ...]:
        return self.stored_applications

    async def environments(self, organisation_id: str) -> tuple[Environment, ...]:
        return self.stored_environments

    async def add_connection(self, value: Connection) -> Connection:
        raise AssertionError("not used")

    async def add_application(self, value: Application) -> Application:
        raise AssertionError("not used")

    async def add_environment(self, value: Environment) -> Environment:
        raise AssertionError("not used")

    async def add_service(self, value: ConsumerService) -> ConsumerService:
        raise AssertionError("not used")

    async def add_application_setup(
        self,
        application: Application,
        environment: Environment,
        service: ConsumerService,
    ) -> tuple[Application, Environment, ConsumerService]:
        raise AssertionError("not used")

    async def get_application(self, organisation_id: str, resource_id: str) -> Application:
        return next(item for item in self.stored_applications if item.id == resource_id)

    async def get_environment(self, organisation_id: str, resource_id: str) -> Environment:
        return next(item for item in self.stored_environments if item.id == resource_id)

    async def get_service(self, organisation_id: str, resource_id: str) -> ConsumerService:
        raise AssertionError("not used")

    async def get_connection(self, organisation_id: str, resource_id: str) -> Connection:
        return next(item for item in self.stored_connections if item.id == resource_id)

    async def get_credential(self, organisation_id: str, resource_id: str) -> ManagedCredential:
        return next(item for item in self.stored_credentials if item.id == resource_id)

    async def get_control_version(
        self, organisation_id: str, credential_id: str, version_id: str
    ) -> ControlVersion:
        return next(
            item
            for item in self.stored_controls
            if item.credential_id == credential_id and item.id == version_id
        )

    async def get_playbook_version(
        self,
        organisation_id: str,
        playbook_id: str,
        version_id: str,
    ) -> PlaybookVersion:
        raise ResourceNotFoundError("playbook version not found")

    async def replace_connection(self, value: Connection, expected_revision: int) -> Connection:
        self.stored_connections = tuple(
            value if item.id == value.id else item for item in self.stored_connections
        )
        return value

    async def replace_application(self, value: Application, expected_revision: int) -> Application:
        self.stored_applications = tuple(
            value if item.id == value.id else item for item in self.stored_applications
        )
        return value

    async def replace_environment(self, value: Environment, expected_revision: int) -> Environment:
        self.stored_environments = tuple(
            value if item.id == value.id else item for item in self.stored_environments
        )
        return value

    async def replace_service(
        self, value: ConsumerService, expected_revision: int
    ) -> ConsumerService:
        return value

    async def replace_credential(
        self, value: ManagedCredential, expected_revision: int
    ) -> ManagedCredential:
        self.stored_credentials = tuple(
            value if item.id == value.id else item for item in self.stored_credentials
        )
        return value

    async def attach_playbook(
        self,
        organisation_id: str,
        connection_id: str,
        expected_revision: int,
        playbook_id: str,
        version_id: str,
        updated_at: datetime,
    ) -> Connection:
        raise AssertionError("not used")

    async def update_authentication(
        self,
        organisation_id: str,
        connection_id: str,
        expected_revision: int,
        authorization_reference: str | None,
        status: ConnectionStatus,
        updated_at: datetime,
    ) -> Connection:
        raise AssertionError("not used")

    async def import_credential(
        self,
        credential: ManagedCredential,
        generation: CredentialGeneration,
        bindings: tuple[ConsumerBinding, ...],
        controls: ControlVersion,
        probes: tuple[ProbeVersion, ...],
        service_setup: tuple[Application, Environment, ConsumerService] | None = None,
    ) -> ManagedCredential:
        raise AssertionError("not used")

    async def replace_controls(
        self,
        credential: ManagedCredential,
        expected_revision: int,
        controls: ControlVersion,
    ) -> tuple[ManagedCredential, ControlVersion]:
        self.stored_credentials = tuple(
            credential if item.id == credential.id else item for item in self.stored_credentials
        )
        self.stored_controls = (*self.stored_controls, controls)
        return credential, controls

    async def archive_inventory(
        self,
        resources: tuple[
            Connection | Application | Environment | ConsumerService | ManagedCredential, ...
        ],
        bindings: tuple[ConsumerBinding, ...],
    ) -> None:
        for resource in resources:
            if isinstance(resource, Connection):
                self.stored_connections = tuple(
                    resource if item.id == resource.id else item for item in self.stored_connections
                )
            elif isinstance(resource, Application):
                self.stored_applications = tuple(
                    resource if item.id == resource.id else item
                    for item in self.stored_applications
                )
            elif isinstance(resource, Environment):
                self.stored_environments = tuple(
                    resource if item.id == resource.id else item
                    for item in self.stored_environments
                )
            elif isinstance(resource, ManagedCredential):
                self.stored_credentials = tuple(
                    resource if item.id == resource.id else item for item in self.stored_credentials
                )

    async def credentials(self, organisation_id: str) -> tuple[ManagedCredential, ...]:
        return self.stored_credentials

    async def count_credentials(self, organisation_id: str) -> int:
        return sum(
            credential.archived_at is None for credential in await self.credentials(organisation_id)
        )

    async def services(self, organisation_id: str) -> tuple[ConsumerService, ...]:
        return ()

    async def bindings(self, organisation_id: str) -> tuple[ConsumerBinding, ...]:
        return ()


def app(
    role: Role = Role.OPERATOR,
    *,
    incident_credential_id: str | None = None,
    approval_run_id: str = "run_one",
) -> FastAPI:
    sequence = itertools.count(1)
    repository = MemoryRunRepository()
    workflow = RunWorkflow(
        repository,
        clock=lambda: NOW,
        id_factory=lambda prefix: f"{prefix}_{next(sequence)}",
    )
    inventory = InventoryRepository()
    incidents = IncidentRepository(incident_credential_id)
    approvals = ApprovalRepository(approval_run_id)
    services = ApiServices(
        workflow=workflow,
        access=AccessControl(AccessRepository(role)),
        tokens=TokenVerifier(),
        inventory=InventoryService(inventory),
        playbooks=PlaybookService(PlaybookRepository(), clock=lambda: NOW),
        approvals=ApprovalService(approvals, clock=lambda: NOW),
        incidents=IncidentService(incidents, clock=lambda: NOW),
        audit=AuditWriter(AuditRepository(), "us-east1", lambda: NOW),
        overview=OverviewService(inventory, repository, incidents, approvals, lambda: NOW),
        browser_setup=BrowserSetup(),
        history=cast(RunHistoryService, History()),
    )
    return create_app(services)


class BrowserSetup:
    def __init__(self) -> None:
        self.setup_url = "https://uumi.example/browser/setup"
        self.gateway_url = "https://gateway.uumi.example"
        self.session: SetupSession | None = None
        self.token = "t" * 43

    async def begin(
        self,
        organisation_id: str,
        connection_id: str,
        subject: str,
        extra_domains: tuple[str, ...] = (),
    ) -> tuple[SetupSession, str]:
        del extra_domains
        secret_container = f"projects/project-one/secrets/uumi-browser-session-{organisation_id}"
        self.session = SetupSession(
            id="setup_browser",
            organisation_id=organisation_id,
            connection_id=connection_id,
            secret_container=secret_container,
            token_hash="a" * 64,
            subject=subject,
            allowed_domains=("*.vendor.example.com",),
            worker_instance="instances/setup",
            internal_address="10.0.0.2",
            status=SetupStatus.READY,
            created_at=NOW,
            expires_at=NOW + timedelta(minutes=30),
            updated_at=NOW,
        )
        return self.session, self.token

    async def get(self, organisation_id: str, setup_id: str) -> SetupSession:
        assert self.session is not None
        assert organisation_id == self.session.organisation_id
        assert setup_id == self.session.id
        return self.session

    async def reap_expired(self, organisation_id: str) -> tuple[SetupSession, ...]:
        del organisation_id
        return ()

    async def complete(
        self,
        organisation_id: str,
        setup_id: str,
        expected_revision: int,
        token: str,
        subject: str,
        actor_id: str | None = None,
    ) -> tuple[SetupSession, Connection, tuple[str, ...]]:
        session = await self.get(organisation_id, setup_id)
        assert expected_revision == session.revision
        assert token == self.token
        assert subject == IDENTITY.subject
        del actor_id
        connection = Connection(
            id=session.connection_id,
            organisation_id=organisation_id,
            platform="vendor",
            display_name="Vendor console",
            roles=frozenset({ConnectionRole.PROVIDER}),
            interface=ConnectionInterface.BROWSER,
            authorization=ConnectionAuthorization.BROWSER_SESSION,
            authorization_reference=f"{session.secret_container}/versions/2",
            capabilities=frozenset({"browser.execute"}),
            allowed_resources=("*.vendor.example.com",),
            playbook_id="playbook_vendor",
            playbook_version_id="playbook_vendor_v1",
            status=ConnectionStatus.READY,
            region="us-east1",
            created_at=NOW,
            updated_at=NOW,
        )
        self.session = session.model_copy(
            update={
                "status": SetupStatus.COMPLETE,
                "auth_reference": connection.authorization_reference,
                "revision": session.revision + 1,
            }
        )
        return self.session, connection, ()

    async def abort(
        self,
        organisation_id: str,
        setup_id: str,
        expected_revision: int,
        subject: str,
    ) -> SetupSession:
        session = await self.get(organisation_id, setup_id)
        assert expected_revision == session.revision
        assert subject == IDENTITY.subject
        self.session = session.model_copy(
            update={
                "status": SetupStatus.TERMINATED,
                "terminated_at": NOW,
                "revision": session.revision + 1,
            }
        )
        return self.session


def headers(key: str = "request-one") -> dict[str, str]:
    return {
        "Authorization": "Bearer valid-token",
        "Idempotency-Key": key,
    }


def create_body() -> dict[str, str]:
    return {
        "credential_id": "cred_one",
        "control_version": "policy_one",
        "source": "schedule",
        "event_id": "event-one",
        "reason": "routine rotation",
        "urgency": "routine",
        "received_at": NOW.isoformat(),
    }


def _incident(
    incident_id: str,
    incident_status: IncidentStatus,
    created_at: datetime,
    credential_id: str | None = None,
) -> Incident:
    return Incident(
        id=incident_id,
        organisation_id="org_one",
        event_id=f"event_{incident_id}",
        source="github_secret_scanning",
        source_event_id=f"alert-{incident_id}",
        severity=Severity.CRITICAL,
        confidence=Confidence.HIGH,
        status=incident_status,
        resource=SourceResource(repository="acme/store-api", provider="sendgrid"),
        credential_id=credential_id,
        created_at=created_at,
        updated_at=created_at,
    )


def _approval(
    approval_id: str,
    decision: ApprovalDecision,
    created_at: datetime,
    decided: bool = False,
    run_id: str = "run_one",
) -> Approval:
    return Approval(
        id=approval_id,
        organisation_id="org_one",
        run_id=run_id,
        action_id="action_one",
        action_digest="a" * 64,
        plan_hash="b" * 64,
        evidence_hash="c" * 64,
        generation_id="gen_one",
        requested_by="service_one",
        capability_hash="d" * 64,
        decision=decision,
        approver_id="approver_one" if decided else None,
        expires_at=created_at + timedelta(hours=1),
        created_at=created_at,
        decided_at=created_at if decided else None,
    )


def _playbook(playbook_id: str, created_at: datetime) -> Playbook:
    return Playbook(
        id=playbook_id,
        organisation_id="org_one",
        name=f"SendGrid Mail API Key Rotation {playbook_id}",
        platform="sendgrid",
        created_at=created_at,
        updated_at=created_at,
    )


def _audit_event(
    event_id: str,
    sequence: int,
    kind: str,
    previous_hash: str,
    occurred_at: datetime,
    run_id: str | None = None,
) -> AuditEvent:
    return AuditEvent(
        id=event_id,
        organisation_id="org_one",
        sequence=sequence,
        kind=kind,
        actor_id="service_one",
        resource="runs/run_one",
        run_id=run_id,
        previous_hash=previous_hash,
        event_hash="e" * 64,
        occurred_at=occurred_at,
        region="us-east1",
    )


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_logout_clears_browser_session_data_without_active_identity() -> None:
    transport = httpx.ASGITransport(app=app(), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/v1/auth/logout")

    assert response.status_code == 204
    assert response.headers["clear-site-data"] == '"cache", "cookies", "storage"'
    assert "__session=" in response.headers["set-cookie"]


@pytest.mark.anyio
async def test_run_routes_require_identity() -> None:
    transport = httpx.ASGITransport(app=app(), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/organisations/org_one/runs",
            headers={"Idempotency-Key": "request-one"},
            json=create_body(),
        )

    assert response.status_code == 401
    assert response.json()["code"] == "unauthenticated"


@pytest.mark.anyio
async def test_viewer_cannot_create_run() -> None:
    transport = httpx.ASGITransport(
        app=app(Role.VIEWER),
        raise_app_exceptions=False,
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/organisations/org_one/runs",
            headers=headers(),
            json=create_body(),
        )

    assert response.status_code == 403
    assert response.json()["code"] == "forbidden"


@pytest.mark.anyio
async def test_viewer_can_read_computer_use_history_and_exact_model_input() -> None:
    transport = httpx.ASGITransport(app=app(Role.VIEWER), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        history = await client.get(
            "/v1/organisations/org_one/runs/run_one/history",
            headers=headers(),
        )
        model_input = await client.get(
            "/v1/organisations/org_one/runs/run_one/computer-use/activity_one/image",
            headers=headers(),
        )

    assert history.status_code == 200
    assert history.json()["stages"][0]["checks"] == ["credential-known"]
    assert history.json()["computer_use"][0]["prompt"] == "Create the replacement credential."
    assert model_input.status_code == 200
    assert model_input.headers["cache-control"] == "private, no-store"
    assert model_input.headers["content-type"] == "image/png"


@pytest.mark.anyio
async def test_create_and_start_are_authenticated_and_idempotent() -> None:
    transport = httpx.ASGITransport(app=app(), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/v1/organisations/org_one/runs",
            headers=headers(),
            json=create_body(),
        )
        run = created.json()["run"]
        started = await client.post(
            f"/v1/organisations/org_one/runs/{run['id']}/start",
            headers=headers("request-start"),
            json={
                "expected_revision": run["revision"],
                "expires_at": (NOW + timedelta(hours=1)).isoformat(),
            },
        )
        duplicate = await client.post(
            f"/v1/organisations/org_one/runs/{run['id']}/start",
            headers=headers("request-start"),
            json={
                "expected_revision": run["revision"],
                "expires_at": (NOW + timedelta(hours=1)).isoformat(),
            },
        )

    assert created.status_code == 201
    assert run["trigger"]["actor_id"] == IDENTITY.actor_id
    assert started.status_code == 200
    assert started.json()["applied"] is True
    assert started.json()["run"]["lease"]["owner_id"] == IDENTITY.actor_id
    assert duplicate.status_code == 200
    assert duplicate.json()["applied"] is False


@pytest.mark.anyio
async def test_failed_run_can_be_cancelled_and_releases_its_credential() -> None:
    transport = httpx.ASGITransport(app=app(), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/v1/organisations/org_one/runs",
            headers=headers("request-failed-create"),
            json=create_body(),
        )
        run = created.json()["run"]
        started = await client.post(
            f"/v1/organisations/org_one/runs/{run['id']}/start",
            headers=headers("request-failed-start"),
            json={
                "expected_revision": run["revision"],
                "expires_at": (NOW + timedelta(hours=1)).isoformat(),
            },
        )
        running = started.json()["run"]
        failed = await client.post(
            f"/v1/organisations/org_one/runs/{run['id']}/fail",
            headers=headers("request-fail"),
            json={
                "expected_revision": running["revision"],
                "fencing_token": running["fencing_token"],
                "failure": {
                    "code": "provisioning-failed",
                    "message": "Browser provisioning failed.",
                    "retryable": False,
                },
            },
        )
        cancelled = await client.post(
            f"/v1/organisations/org_one/runs/{run['id']}/cancel",
            headers=headers("request-cancel-failed"),
            json={"expected_revision": failed.json()["run"]["revision"]},
        )
        replacement = await client.post(
            "/v1/organisations/org_one/runs",
            headers=headers("request-after-cancel"),
            json={**create_body(), "event_id": "event-after-cancel"},
        )

    assert failed.status_code == 200
    assert failed.json()["run"]["status"] == "failed"
    assert cancelled.status_code == 200
    assert cancelled.json()["run"]["status"] == "cancelled"
    assert replacement.status_code == 201


@pytest.mark.anyio
async def test_list_runs_orders_newest_first_and_filters_status() -> None:
    transport = httpx.ASGITransport(app=app(), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.post(
            "/v1/organisations/org_one/runs",
            headers=headers("request-first"),
            json=create_body(),
        )
        second_body = {**create_body(), "credential_id": "cred_two", "event_id": "event-two"}
        second = await client.post(
            "/v1/organisations/org_one/runs",
            headers=headers("request-second"),
            json=second_body,
        )
        listed = await client.get(
            "/v1/organisations/org_one/runs",
            headers=headers(),
        )
        pending_only = await client.get(
            "/v1/organisations/org_one/runs",
            headers=headers(),
            params=[("status", "pending")],
        )

    assert first.status_code == 201
    assert second.status_code == 201
    assert listed.status_code == 200
    assert [run["id"] for run in listed.json()] == [
        second.json()["run"]["id"],
        first.json()["run"]["id"],
    ]
    assert [run["id"] for run in pending_only.json()] == [
        second.json()["run"]["id"],
        first.json()["run"]["id"],
    ]


@pytest.mark.anyio
async def test_list_runs_respects_limit_and_organisation() -> None:
    transport = httpx.ASGITransport(app=app(), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post(
            "/v1/organisations/org_one/runs",
            headers=headers("request-first"),
            json=create_body(),
        )
        await client.post(
            "/v1/organisations/org_one/runs",
            headers=headers("request-second"),
            json={**create_body(), "credential_id": "cred_two", "event_id": "event-two"},
        )
        limited = await client.get(
            "/v1/organisations/org_one/runs",
            headers=headers(),
            params={"limit": 1},
        )
        foreign = await client.get(
            "/v1/organisations/org_two/runs",
            headers=headers(),
        )

    assert limited.status_code == 200
    assert len(limited.json()) == 1
    assert foreign.status_code == 403
    assert foreign.json()["code"] == "forbidden"


@pytest.mark.anyio
async def test_list_incidents_filters_status_newest_first() -> None:
    transport = httpx.ASGITransport(app=app(), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        listed = await client.get(
            "/v1/organisations/org_one/incidents",
            headers=headers(),
        )
        open_only = await client.get(
            "/v1/organisations/org_one/incidents",
            headers=headers(),
            params=[("status", "new")],
        )
        action_only = await client.get(
            "/v1/organisations/org_one/incidents",
            headers=headers(),
            params=[("status", "action-required")],
        )

    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == ["incident_two", "incident_one"]
    assert [item["id"] for item in open_only.json()] == ["incident_one"]
    assert [item["id"] for item in action_only.json()] == ["incident_two"]


@pytest.mark.anyio
async def test_administrator_dismisses_an_incident_with_a_reason() -> None:
    transport = httpx.ASGITransport(app=app(Role.ADMINISTRATOR), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        dismissed = await client.post(
            "/v1/organisations/org_one/incidents/incident_two/dismiss",
            headers=headers(),
            json={"expected_revision": 0, "reason": "False positive"},
        )

    assert dismissed.status_code == 200
    assert dismissed.json()["status"] == "dismissed"
    assert dismissed.json()["dismissal_reason"] == "False positive"


@pytest.mark.anyio
async def test_list_approvals_filters_decision_newest_first() -> None:
    transport = httpx.ASGITransport(app=app(), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        listed = await client.get(
            "/v1/organisations/org_one/approvals",
            headers=headers(),
        )
        pending = await client.get(
            "/v1/organisations/org_one/approvals",
            headers=headers(),
            params=[("decision", "pending")],
        )

    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == ["approval_two", "approval_one"]
    assert [item["id"] for item in pending.json()] == ["approval_one"]


@pytest.mark.anyio
async def test_approval_evidence_returns_the_bound_snapshot() -> None:
    transport = httpx.ASGITransport(app=app(), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        evidence = await client.get(
            "/v1/organisations/org_one/approvals/approval_one/evidence",
            headers=headers(),
        )

    assert evidence.status_code == 200
    assert evidence.json()["evidence_hash"] == "c" * 64
    assert evidence.json()["checks"] == [
        "provider-valid",
        "store-valid",
        "deployment-valid",
    ]


@pytest.mark.anyio
async def test_list_playbooks_newest_first() -> None:
    transport = httpx.ASGITransport(app=app(), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        playbooks = await client.get(
            "/v1/organisations/org_one/playbooks",
            headers=headers(),
        )

    assert playbooks.status_code == 200
    assert [item["id"] for item in playbooks.json()] == ["playbook_two", "playbook_one"]


@pytest.mark.anyio
async def test_list_inventory_collections() -> None:
    transport = httpx.ASGITransport(app=app(), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        connections = await client.get(
            "/v1/organisations/org_one/inventory/connections",
            headers=headers(),
        )
        applications = await client.get(
            "/v1/organisations/org_one/inventory/applications",
            headers=headers(),
        )
        environments = await client.get(
            "/v1/organisations/org_one/inventory/environments",
            headers=headers(),
        )
        services = await client.get(
            "/v1/organisations/org_one/inventory/services",
            headers=headers(),
        )
        credentials = await client.get(
            "/v1/organisations/org_one/inventory/credentials",
            headers=headers(),
        )
        credential = await client.get(
            "/v1/organisations/org_one/inventory/credentials/credential_one",
            headers=headers(),
        )

    assert connections.status_code == 200
    assert [item["id"] for item in connections.json()] == ["connection_one"]
    assert connections.json()[0]["authorization_reference"] == (
        "projects/org-one/secrets/sendgrid-admin/versions/1"
    )
    assert applications.status_code == 200
    assert [item["id"] for item in applications.json()] == ["application_one"]
    assert environments.status_code == 200
    assert [item["id"] for item in environments.json()] == ["environment_one"]
    assert services.status_code == 200
    assert services.json() == []
    assert credentials.status_code == 200
    assert [item["id"] for item in credentials.json()] == ["credential_one"]
    assert credential.status_code == 200
    assert credential.json()["display_name"] == "production-password-emailer"


@pytest.mark.anyio
async def test_credential_controls_are_versioned_atomically_and_previous_version_remains() -> None:
    transport = httpx.ASGITransport(
        app=app(Role.ADMINISTRATOR),
        raise_app_exceptions=False,
    )
    controls = {
        "automatic_triggers": ["expiry", "drift"],
        "rotate_before_expiry_seconds": 604800,
        "maximum_observation_seconds": 1800,
    }
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        previous = await client.get(
            "/v1/organisations/org_one/inventory/credentials/credential_one/controls/control_version_one",
            headers=headers(),
        )
        changed = await client.post(
            "/v1/organisations/org_one/inventory/credentials/credential_one/controls",
            headers=headers(),
            json={
                "expected_revision": 0,
                "version_id": "control_version_two",
                "controls": controls,
            },
        )
        stale = await client.post(
            "/v1/organisations/org_one/inventory/credentials/credential_one/controls",
            headers=headers(),
            json={
                "expected_revision": 0,
                "version_id": "control_version_three",
                "controls": controls,
            },
        )
        retained = await client.get(
            "/v1/organisations/org_one/inventory/credentials/credential_one/controls/control_version_one",
            headers=headers(),
        )

    assert previous.status_code == 200
    assert previous.json()["credential_id"] == "credential_one"
    assert changed.status_code == 201
    assert changed.json()["credential"]["control_version"] == "control_version_two"
    assert changed.json()["credential"]["revision"] == 1
    assert changed.json()["controls"]["number"] == 2
    assert stale.status_code == 409
    assert retained.status_code == 200


@pytest.mark.anyio
async def test_inventory_metadata_update_is_revision_fenced_and_archivable() -> None:
    transport = httpx.ASGITransport(
        app=app(Role.ADMINISTRATOR),
        raise_app_exceptions=False,
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        changed = await client.patch(
            "/v1/organisations/org_one/inventory/credentials/credential_one",
            headers=headers(),
            json={"expected_revision": 0, "display_name": "renamed-credential"},
        )
        stale = await client.patch(
            "/v1/organisations/org_one/inventory/credentials/credential_one",
            headers=headers(),
            json={"expected_revision": 0, "display_name": "stale-name"},
        )
        archived = await client.post(
            "/v1/organisations/org_one/inventory/credentials/credential_one/archive",
            headers=headers(),
            json={"expected_revision": 1},
        )
        blocked_application = await client.post(
            "/v1/organisations/org_one/inventory/applications/application_one/archive",
            headers=headers(),
            json={"expected_revision": 0},
        )
        listed = await client.get(
            "/v1/organisations/org_one/inventory/credentials",
            headers=headers(),
        )

    assert changed.status_code == 200
    assert changed.json()["display_name"] == "renamed-credential"
    assert changed.json()["revision"] == 1
    assert stale.status_code == 409
    assert archived.status_code == 200
    assert archived.json()["archived_at"] is not None
    assert blocked_application.status_code == 409
    assert listed.json() == []


@pytest.mark.anyio
async def test_credential_archive_stops_active_rotation_and_pending_approval() -> None:
    transport = httpx.ASGITransport(
        app=app(Role.ADMINISTRATOR, approval_run_id="run_1"),
        raise_app_exceptions=False,
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/v1/organisations/org_one/runs",
            headers=headers("create-active-run"),
            json={**create_body(), "credential_id": "credential_one"},
        )
        archived = await client.post(
            "/v1/organisations/org_one/inventory/credentials/credential_one/archive",
            headers=headers("archive-active-credential"),
            json={"expected_revision": 0, "cascade": True},
        )
        run = await client.get(
            f"/v1/organisations/org_one/runs/{created.json()['run']['id']}",
            headers=headers(),
        )
        approvals = await client.get(
            "/v1/organisations/org_one/approvals",
            headers=headers(),
        )
        credentials = await client.get(
            "/v1/organisations/org_one/inventory/credentials",
            headers=headers(),
        )

    assert created.status_code == 201
    assert archived.status_code == 200
    assert run.json()["status"] == "cancelled"
    pending = next(item for item in approvals.json() if item["id"] == "approval_one")
    assert pending["decision"] == "cancelled"
    assert credentials.json() == []


@pytest.mark.anyio
async def test_credential_archive_dismisses_open_incident() -> None:
    transport = httpx.ASGITransport(
        app=app(Role.ADMINISTRATOR, incident_credential_id="credential_one"),
        raise_app_exceptions=False,
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        archived = await client.post(
            "/v1/organisations/org_one/inventory/credentials/credential_one/archive",
            headers=headers("archive-incident-credential"),
            json={"expected_revision": 0, "cascade": True},
        )
        incident = await client.get(
            "/v1/organisations/org_one/incidents/incident_one",
            headers=headers(),
        )

    assert archived.status_code == 200
    assert incident.json()["status"] == "dismissed"
    assert incident.json()["dismissal_reason"] == "Credential removed from Uumi."


@pytest.mark.anyio
async def test_playbook_details_support_root_renames() -> None:
    transport = httpx.ASGITransport(
        app=app(Role.ADMINISTRATOR),
        raise_app_exceptions=False,
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        playbook_detail = await client.get(
            "/v1/organisations/org_one/playbooks/playbook_one",
            headers=headers(),
        )
        playbook = await client.patch(
            "/v1/organisations/org_one/playbooks/playbook_one",
            headers=headers(),
            json={"expected_revision": 0, "name": "Renamed playbook"},
        )

    assert playbook_detail.status_code == 200
    assert playbook_detail.json()["active_version"] is None
    assert playbook.status_code == 200
    assert playbook.json()["name"] == "Renamed playbook"


@pytest.mark.anyio
async def test_viewer_reads_lists_but_cannot_mutate() -> None:
    transport = httpx.ASGITransport(
        app=app(Role.VIEWER),
        raise_app_exceptions=False,
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        runs = await client.get("/v1/organisations/org_one/runs", headers=headers())
        incidents = await client.get("/v1/organisations/org_one/incidents", headers=headers())
        approvals = await client.get("/v1/organisations/org_one/approvals", headers=headers())
        playbooks = await client.get("/v1/organisations/org_one/playbooks", headers=headers())
        create = await client.post(
            "/v1/organisations/org_one/runs",
            headers=headers("request-denied"),
            json=create_body(),
        )

    assert runs.status_code == 200
    assert incidents.status_code == 200
    assert approvals.status_code == 200
    assert playbooks.status_code == 200
    assert create.status_code == 403


@pytest.mark.anyio
async def test_audit_search_orders_by_sequence_and_filters() -> None:
    transport = httpx.ASGITransport(
        app=app(Role.VIEWER),
        raise_app_exceptions=False,
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        listed = await client.get("/v1/organisations/org_one/audit", headers=headers())
        for_run = await client.get(
            "/v1/organisations/org_one/audit",
            headers=headers(),
            params={"run_id": "run_one"},
        )
        by_kind = await client.get(
            "/v1/organisations/org_one/audit",
            headers=headers(),
            params={"kind": "run.completed"},
        )

    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [
        "audit_three",
        "audit_two",
        "audit_one",
    ]
    assert [item["id"] for item in for_run.json()] == ["audit_two", "audit_one"]
    assert [item["id"] for item in by_kind.json()] == ["audit_three"]


@pytest.mark.anyio
async def test_audit_search_respects_limit() -> None:
    transport = httpx.ASGITransport(
        app=app(Role.VIEWER),
        raise_app_exceptions=False,
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        limited = await client.get(
            "/v1/organisations/org_one/audit",
            headers=headers(),
            params={"limit": 2},
        )

    assert limited.status_code == 200
    assert [item["id"] for item in limited.json()] == ["audit_three", "audit_two"]


@pytest.mark.anyio
async def test_operator_cannot_read_audit() -> None:
    transport = httpx.ASGITransport(
        app=app(Role.OPERATOR),
        raise_app_exceptions=False,
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1/organisations/org_one/audit", headers=headers())

    assert response.status_code == 403
    assert response.json()["code"] == "forbidden"


@pytest.mark.anyio
async def test_overview_counts_active_work() -> None:
    transport = httpx.ASGITransport(app=app(), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post(
            "/v1/organisations/org_one/runs",
            headers=headers("request-first"),
            json=create_body(),
        )
        await client.post(
            "/v1/organisations/org_one/runs",
            headers=headers("request-second"),
            json={**create_body(), "credential_id": "cred_two", "event_id": "event-two"},
        )
        summary = await client.get(
            "/v1/organisations/org_one/overview",
            headers=headers(),
        )
        foreign = await client.get(
            "/v1/organisations/org_two/overview",
            headers=headers(),
        )

    assert summary.status_code == 200
    assert summary.json() == {
        "credentials": 1,
        "rotations_in_progress": 2,
        "failed_rotations": 0,
        "open_incidents": 2,
        "pending_approvals": 1,
    }
    assert foreign.status_code == 403


@pytest.mark.anyio
async def test_viewer_reads_overview() -> None:
    transport = httpx.ASGITransport(
        app=app(Role.VIEWER),
        raise_app_exceptions=False,
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        summary = await client.get(
            "/v1/organisations/org_one/overview",
            headers=headers(),
        )

    assert summary.status_code == 200
    assert summary.json()["credentials"] == 1
    assert summary.json()["rotations_in_progress"] == 0
    assert summary.json()["open_incidents"] == 2
    assert summary.json()["pending_approvals"] == 1


@pytest.mark.anyio
async def test_overview_does_not_count_expired_pending_approvals() -> None:
    summary = await OverviewService(
        InventoryRepository(),
        MemoryRunRepository(),
        IncidentRepository(),
        ApprovalRepository(),
        lambda: NOW + timedelta(hours=2),
    ).summary("org_one")

    assert summary.pending_approvals == 0


@pytest.mark.anyio
async def test_administrator_can_run_browser_connection_setup() -> None:
    transport = httpx.ASGITransport(
        app=app(Role.ADMINISTRATOR),
        raise_app_exceptions=False,
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        begun = await client.post(
            "/v1/organisations/org_one/inventory/connections/connection_browser/setup",
            headers=headers(),
            json={"extra_domains": []},
        )
        setup_id = begun.json()["session"]["id"]
        fetched = await client.get(
            f"/v1/organisations/org_one/inventory/setups/{setup_id}",
            headers=headers(),
        )
        completed = await client.post(
            f"/v1/organisations/org_one/inventory/setups/{setup_id}/complete",
            headers=headers(),
            json={"expected_revision": 0, "token": "t" * 43},
        )

    assert begun.status_code == 201
    assert begun.json()["setup_url"] == "https://uumi.example/browser/setup"
    assert begun.json()["gateway_url"] == "https://gateway.uumi.example"
    assert fetched.status_code == 200
    assert completed.status_code == 200
    assert completed.json()["connection"]["status"] == "ready"
    assert completed.json()["session"]["status"] == "complete"


@pytest.mark.anyio
async def test_operator_cannot_begin_browser_setup() -> None:
    transport = httpx.ASGITransport(
        app=app(Role.OPERATOR),
        raise_app_exceptions=False,
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        begun = await client.post(
            "/v1/organisations/org_one/inventory/connections/connection_browser/setup",
            headers=headers(),
            json={"extra_domains": []},
        )

    assert begun.status_code == 403


@pytest.mark.anyio
async def test_setup_without_browser_runtime_is_a_conflict() -> None:
    sequence = itertools.count(1)
    repository = MemoryRunRepository()
    workflow = RunWorkflow(
        repository,
        clock=lambda: NOW,
        id_factory=lambda prefix: f"{prefix}_{next(sequence)}",
    )
    inventory = InventoryRepository()
    services = ApiServices(
        workflow=workflow,
        access=AccessControl(AccessRepository(Role.ADMINISTRATOR)),
        tokens=TokenVerifier(),
        inventory=InventoryService(inventory),
        playbooks=PlaybookService(PlaybookRepository(), clock=lambda: NOW),
        approvals=ApprovalService(ApprovalRepository(), clock=lambda: NOW),
        incidents=IncidentService(IncidentRepository(), clock=lambda: NOW),
        audit=AuditWriter(AuditRepository(), "us-east1", lambda: NOW),
        overview=OverviewService(
            inventory,
            repository,
            IncidentRepository(),
            ApprovalRepository(),
            lambda: NOW,
        ),
    )
    transport = httpx.ASGITransport(app=create_app(services), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        begun = await client.post(
            "/v1/organisations/org_one/inventory/connections/connection_browser/setup",
            headers=headers(),
            json={"extra_domains": []},
        )

    assert begun.status_code == 409
    assert begun.json()["code"] == "conflict"
