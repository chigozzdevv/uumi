import itertools
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from api.app import create_app
from api.deps import ApiServices
from contracts import (
    Application,
    Approval,
    ApprovalDecision,
    AuditEvent,
    Connection,
    ConnectionKind,
    ConnectionStatus,
    ConsumerBinding,
    ConsumerService,
    CredentialGeneration,
    Environment,
    Incident,
    IncidentStatus,
    IngestionEvent,
    ManagedCredential,
    Playbook,
    PlaybookAssignment,
    PlaybookDraft,
    PlaybookVersion,
    Policy,
    PolicyVersion,
    SetupSession,
    SetupStatus,
    Severity,
    SourceResource,
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
from core.errors import AuthenticationError
from core.incident import IncidentService
from core.inventory import InventoryService
from core.overview import OverviewService
from core.playbook import PlaybookService
from core.policy import PolicyService
from core.workflow import RunWorkflow
from fastapi import FastAPI
from testkit import MemoryRunRepository, make_http_provider_api

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
    def __init__(self) -> None:
        self.incidents = (
            _incident("incident_one", IncidentStatus.NEW, NOW),
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
        raise AssertionError("not used")

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


class ApprovalRepository:
    def __init__(self) -> None:
        self.approvals = (
            _approval("approval_one", ApprovalDecision.PENDING, NOW),
            _approval(
                "approval_two", ApprovalDecision.APPROVED, NOW + timedelta(minutes=5), decided=True
            ),
        )

    async def list_approvals(self, organisation_id: str, limit: int) -> tuple[Approval, ...]:
        return self.approvals[:limit]

    async def count_approvals(
        self, organisation_id: str, decisions: frozenset[ApprovalDecision]
    ) -> int:
        return sum(1 for approval in self.approvals if approval.decision in decisions)

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
        raise AssertionError("not used")

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


class PolicyRepository:
    def __init__(self) -> None:
        self.policies = (
            _policy_record("policy_one", NOW),
            _policy_record("policy_two", NOW + timedelta(minutes=5)),
        )

    async def list_policies(self, organisation_id: str, limit: int) -> tuple[Policy, ...]:
        return self.policies[:limit]

    async def create(self, policy: Policy) -> Policy:
        raise AssertionError("not used")

    async def create_version(
        self,
        organisation_id: str,
        policy_id: str,
        factory: object,
    ) -> PolicyVersion:
        raise AssertionError("not used")

    async def activate(
        self,
        organisation_id: str,
        policy_id: str,
        version_id: str,
        actor_id: str,
        now: datetime,
    ) -> PolicyVersion:
        raise AssertionError("not used")


class PlaybookRepository:
    def __init__(self) -> None:
        self.playbooks = (
            _playbook("playbook_one", NOW),
            _playbook("playbook_two", NOW + timedelta(minutes=5)),
        )

    async def list_playbooks(self, organisation_id: str, limit: int) -> tuple[Playbook, ...]:
        return self.playbooks[:limit]

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

    async def get_dryrun(self, organisation_id: str, playbook_id: str, dryrun_id: str) -> None:
        raise AssertionError("not used")

    async def validate_dryrun(
        self,
        organisation_id: str,
        playbook_id: str,
        version_id: str,
        environment_id: str,
        credential_id: str,
    ) -> None:
        raise AssertionError("not used")

    async def activate(
        self,
        organisation_id: str,
        playbook_id: str,
        version_id: str,
        dryrun_id: str,
        actor_id: str,
        activated_at: datetime,
    ) -> PlaybookVersion:
        raise AssertionError("not used")

    async def assign(self, assignment: PlaybookAssignment) -> PlaybookAssignment:
        raise AssertionError("not used")

    async def get_assignment(
        self, organisation_id: str, credential_id: str
    ) -> PlaybookAssignment | None:
        return None


class AuditRepository:
    def __init__(self) -> None:
        self.events = (
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
        raise AssertionError("not used")


class InventoryRepository:
    def __init__(self) -> None:
        self.stored_connections = (
            Connection(
                id="connection_one",
                organisation_id="org_one",
                kind=ConnectionKind.PROVIDER,
                provider="sendgrid",
                display_name="SendGrid Admin",
                auth_reference="projects/org-one/secrets/sendgrid-admin/versions/1",
                capabilities=frozenset({"create", "revoke"}),
                allowed_resources=("sendgrid:*",),
                http=make_http_provider_api(),
                status=ConnectionStatus.READY,
                region="us-east1",
                created_at=NOW,
                updated_at=NOW,
            ),
        )
        self.stored_applications = (
            Application(
                id="application_one",
                organisation_id="org_one",
                display_name="Acme Store",
                created_at=NOW,
                updated_at=NOW,
            ),
        )
        self.stored_environments = (
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

    async def get_application(self, organisation_id: str, resource_id: str) -> Application:
        raise AssertionError("not used")

    async def get_environment(self, organisation_id: str, resource_id: str) -> Environment:
        raise AssertionError("not used")

    async def get_connection(self, organisation_id: str, resource_id: str) -> Connection:
        raise AssertionError("not used")

    async def update_authentication(
        self,
        organisation_id: str,
        connection_id: str,
        expected_revision: int,
        auth_reference: str,
        status: ConnectionStatus,
        updated_at: datetime,
    ) -> Connection:
        raise AssertionError("not used")

    async def import_credential(
        self,
        credential: ManagedCredential,
        generation: CredentialGeneration,
        bindings: tuple[ConsumerBinding, ...],
    ) -> ManagedCredential:
        raise AssertionError("not used")

    async def credentials(self, organisation_id: str) -> tuple[ManagedCredential, ...]:
        return (
            ManagedCredential(
                id="credential_one",
                organisation_id="org_one",
                connection_id="connection_one",
                provider="sendgrid",
                kind="api-key",
                display_name="production-password-emailer",
                policy_version="policy_version_one",
                playbook_version="playbook_version_one",
                created_at=NOW,
                updated_at=NOW,
            ),
        )

    async def count_credentials(self, organisation_id: str) -> int:
        return len(await self.credentials(organisation_id))

    async def services(self, organisation_id: str) -> tuple[ConsumerService, ...]:
        return ()

    async def bindings(self, organisation_id: str) -> tuple[ConsumerBinding, ...]:
        return ()


def app(role: Role = Role.OPERATOR) -> FastAPI:
    sequence = itertools.count(1)
    repository = MemoryRunRepository()
    workflow = RunWorkflow(
        repository,
        clock=lambda: NOW,
        id_factory=lambda prefix: f"{prefix}_{next(sequence)}",
    )
    inventory = InventoryRepository()
    incidents = IncidentRepository()
    approvals = ApprovalRepository()
    services = ApiServices(
        workflow=workflow,
        access=AccessControl(AccessRepository(role)),
        tokens=TokenVerifier(),
        inventory=InventoryService(inventory),
        playbooks=PlaybookService(PlaybookRepository(), clock=lambda: NOW),
        approvals=ApprovalService(approvals, clock=lambda: NOW),
        incidents=IncidentService(incidents, clock=lambda: NOW),
        policies=PolicyService(PolicyRepository(), clock=lambda: NOW),
        audit=AuditWriter(AuditRepository(), "us-east1", lambda: NOW),
        overview=OverviewService(inventory, repository, incidents, approvals),
        browser_setup=BrowserSetup(),
    )
    return create_app(services)


class BrowserSetup:
    def __init__(self) -> None:
        self.gateway_url = "https://gateway.firekey.example"
        self.session: SetupSession | None = None
        self.token = "t" * 43

    async def begin(
        self,
        organisation_id: str,
        connection_id: str,
        secret_container: str,
        subject: str,
        extra_domains: tuple[str, ...] = (),
    ) -> tuple[SetupSession, str]:
        del extra_domains
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
            kind=ConnectionKind.BROWSER,
            provider="vendor",
            display_name="Vendor console",
            auth_reference=f"{session.secret_container}/versions/2",
            capabilities=frozenset({"browser.execute"}),
            allowed_resources=("*.vendor.example.com",),
            status=ConnectionStatus.READY,
            region="us-east1",
            created_at=NOW,
            updated_at=NOW,
        )
        self.session = session.model_copy(
            update={
                "status": SetupStatus.COMPLETE,
                "auth_reference": connection.auth_reference,
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
        "policy_version": "policy_one",
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
        created_at=created_at,
        updated_at=created_at,
    )


def _approval(
    approval_id: str,
    decision: ApprovalDecision,
    created_at: datetime,
    decided: bool = False,
) -> Approval:
    return Approval(
        id=approval_id,
        organisation_id="org_one",
        run_id="run_one",
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


def _policy_record(policy_id: str, created_at: datetime) -> Policy:
    return Policy(
        id=policy_id,
        organisation_id="org_one",
        name=f"Production SaaS Keys {policy_id}",
        created_at=created_at,
        updated_at=created_at,
    )


def _playbook(playbook_id: str, created_at: datetime) -> Playbook:
    return Playbook(
        id=playbook_id,
        organisation_id="org_one",
        name=f"SendGrid Mail API Key Rotation {playbook_id}",
        provider="sendgrid",
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
async def test_list_policies_and_playbooks_newest_first() -> None:
    transport = httpx.ASGITransport(app=app(), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        policies = await client.get(
            "/v1/organisations/org_one/policies",
            headers=headers(),
        )
        playbooks = await client.get(
            "/v1/organisations/org_one/playbooks",
            headers=headers(),
        )

    assert policies.status_code == 200
    assert [item["id"] for item in policies.json()] == ["policy_two", "policy_one"]
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

    assert connections.status_code == 200
    assert [item["id"] for item in connections.json()] == ["connection_one"]
    assert connections.json()[0]["auth_reference"] == (
        "projects/org-one/secrets/sendgrid-admin/versions/1"
    )
    assert applications.status_code == 200
    assert [item["id"] for item in applications.json()] == ["application_one"]
    assert environments.status_code == 200
    assert [item["id"] for item in environments.json()] == ["environment_one"]


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
        policies = await client.get("/v1/organisations/org_one/policies", headers=headers())
        playbooks = await client.get("/v1/organisations/org_one/playbooks", headers=headers())
        create = await client.post(
            "/v1/organisations/org_one/runs",
            headers=headers("request-denied"),
            json=create_body(),
        )

    assert runs.status_code == 200
    assert incidents.status_code == 200
    assert approvals.status_code == 200
    assert policies.status_code == 200
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
async def test_administrator_can_run_browser_connection_setup() -> None:
    transport = httpx.ASGITransport(
        app=app(Role.ADMINISTRATOR),
        raise_app_exceptions=False,
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        begun = await client.post(
            "/v1/organisations/org_one/inventory/connections/connection_browser/setup",
            headers=headers(),
            json={"secret_container": "projects/project-one/secrets/vendor-session"},
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
    assert begun.json()["gateway_url"] == "https://gateway.firekey.example"
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
            json={"secret_container": "projects/project-one/secrets/vendor-session"},
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
        policies=PolicyService(PolicyRepository(), clock=lambda: NOW),
        audit=AuditWriter(AuditRepository(), "us-east1", lambda: NOW),
        overview=OverviewService(inventory, repository, IncidentRepository(), ApprovalRepository()),
    )
    transport = httpx.ASGITransport(app=create_app(services), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        begun = await client.post(
            "/v1/organisations/org_one/inventory/connections/connection_browser/setup",
            headers=headers(),
            json={"secret_container": "projects/project-one/secrets/vendor-session"},
        )

    assert begun.status_code == 409
    assert begun.json()["code"] == "conflict"
