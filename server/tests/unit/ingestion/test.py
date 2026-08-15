import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

import pytest
from contracts import (
    Confidence,
    ConsumerBinding,
    ConsumerService,
    Incident,
    IncidentStatus,
    ManagedCredential,
    PolicyDefinition,
    PolicyState,
    PolicyVersion,
    RecoveryMode,
    Severity,
    SourceResource,
)
from core.incident import IncidentService
from core.workflow import RunWorkflow
from ingestion.app import _pubsub, _verify_hmac
from ingestion.automation import IncidentAutomation
from ingestion.sources import ProviderSource, ScheduleSource, SecretManagerSource
from policy import REQUIRED_CHECKS, digest
from testkit import MemoryRunRepository

NOW = datetime(2026, 8, 13, 12, tzinfo=UTC)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_schedule_occurrence_is_stable_across_retries() -> None:
    source = ScheduleSource()
    payload = {"credential_id": "credential_one", "due_at": NOW.isoformat()}

    first = source.normalise("org_one", "schedule_one", payload, NOW)
    retry = source.normalise("org_one", "schedule_one", payload, NOW + timedelta(minutes=1))

    assert first.id == retry.id
    assert first.source_event_id == f"schedule_one:{NOW.isoformat()}"
    assert first.resource.credential_id == "credential_one"


def test_secret_manager_uses_authenticated_pubsub_attributes() -> None:
    resource = "projects/project-one/secrets/mailer"
    metadata = {"name": resource, "rotation": {"nextRotationTime": NOW.isoformat()}}
    envelope = {
        "message": {
            "messageId": "message-one",
            "publishTime": NOW.isoformat(),
            "attributes": {
                "eventType": "SECRET_ROTATE",
                "secretId": resource,
                "dataFormat": "JSON_API_V1",
                "timestamp": NOW.isoformat(),
            },
            "data": base64.b64encode(json.dumps(metadata).encode()).decode(),
        }
    }

    message_id, _, payload, attributes = _pubsub(envelope)
    event = SecretManagerSource().normalise("org_one", payload, attributes, message_id, NOW)

    assert event.kind == "credential-rotation-due"
    assert event.resource.provider_id == resource
    assert event.observed_at == NOW


def test_provider_signature_binds_timestamp_and_rejects_replay() -> None:
    body = b'{"event_id":"event-one"}'
    timestamp = NOW.isoformat()
    secret = b"webhook-secret"
    signature = (
        "sha256=" + hmac.new(secret, timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    )

    _verify_hmac(body, signature, timestamp, secret, NOW, 300)

    with pytest.raises(ValueError, match="replay window"):
        _verify_hmac(body, signature, timestamp, secret, NOW + timedelta(minutes=6), 300)


def test_provider_event_preserves_exact_provider_identifier() -> None:
    event = ProviderSource().normalise(
        "org_one",
        "sendgrid",
        {
            "event_id": "provider-event-one",
            "event_type": "credential.compromised",
            "provider_id": "provider-key-one",
            "observed_at": NOW.isoformat(),
        },
        NOW,
    )

    assert event.severity is Severity.CRITICAL
    assert event.confidence is Confidence.VERIFIED
    assert event.resource.provider_id == "provider-key-one"


@pytest.mark.anyio
async def test_active_policy_automatically_starts_exactly_correlated_run() -> None:
    credential = _credential()
    inventory = Inventory(credential)
    incidents = Incidents()
    service = IncidentService(
        incidents,
        lambda: NOW,
        inventory,
        RunWorkflow(MemoryRunRepository(), clock=lambda: NOW),
    )
    automation = IncidentAutomation(service, inventory, Policies(_policy()))
    event = ProviderSource().normalise(
        "org_one",
        "sendgrid",
        {
            "event_id": "provider-event-one",
            "event_type": "credential.compromised",
            "provider_id": "provider-key-one",
            "observed_at": NOW.isoformat(),
        },
        NOW,
    )

    incident, applied = await automation.ingest(event)
    retry, retry_applied = await automation.ingest(
        event.model_copy(update={"received_at": NOW + timedelta(minutes=1)})
    )

    assert applied
    assert not retry_applied
    assert incident.status is IncidentStatus.ROTATING
    assert retry.run_id == incident.run_id


@pytest.mark.anyio
async def test_high_confidence_single_candidate_requires_explicit_policy_threshold() -> None:
    credential = _credential().model_copy(update={"provider_id": None})
    inventory = Inventory(credential, repository="example/mailer")
    incidents = Incidents()
    service = IncidentService(
        incidents,
        lambda: NOW,
        inventory,
        RunWorkflow(MemoryRunRepository(), clock=lambda: NOW),
    )
    automation = IncidentAutomation(service, inventory, Policies(_policy()))
    event = (
        ProviderSource()
        .normalise(
            "org_one",
            "sendgrid",
            {
                "event_id": "provider-event-two",
                "event_type": "credential.compromised",
                "provider_id": "unmapped-key",
                "observed_at": NOW.isoformat(),
            },
            NOW,
        )
        .model_copy(
            update={"resource": SourceResource(repository="example/mailer", provider="sendgrid")}
        )
    )

    incident, _ = await automation.ingest(event)

    assert incident.status is IncidentStatus.ROTATING
    assert incident.credential_id == "credential_one"


class Incidents:
    def __init__(self) -> None:
        self.values: dict[str, Incident] = {}

    async def ingest(self, incident: Incident, event: object) -> tuple[Incident, bool]:
        current = self.values.get(incident.id)
        if current is not None:
            return current, False
        self.values[incident.id] = incident
        return incident, True

    async def get(self, organisation_id: str, incident_id: str) -> Incident:
        return self.values[incident_id]

    async def list_incidents(self, organisation_id: str, limit: int) -> tuple[Incident, ...]:
        return tuple(self.values.values())[:limit]

    async def correlate(
        self,
        organisation_id: str,
        incident_id: str,
        expected_revision: int,
        candidates: tuple[object, ...],
        credential_id: str | None,
        updated_at: datetime,
    ) -> Incident:
        current = self.values[incident_id]
        changed = current.model_copy(
            update={
                "candidates": candidates,
                "credential_id": credential_id,
                "status": IncidentStatus.ACTION if credential_id else IncidentStatus.CORRELATING,
                "updated_at": updated_at,
                "revision": current.revision + 1,
            }
        )
        self.values[incident_id] = changed
        return changed

    async def link_run(
        self,
        organisation_id: str,
        incident_id: str,
        expected_revision: int,
        credential_id: str,
        run_id: str,
        updated_at: datetime,
    ) -> Incident:
        current = self.values[incident_id]
        changed = current.model_copy(
            update={
                "credential_id": credential_id,
                "run_id": run_id,
                "status": IncidentStatus.ROTATING,
                "updated_at": updated_at,
                "revision": current.revision + 1,
            }
        )
        self.values[incident_id] = changed
        return changed

    async def advance_run(
        self,
        organisation_id: str,
        run_id: str,
        status: IncidentStatus,
        updated_at: datetime,
    ) -> tuple[Incident, ...]:
        return ()


class Inventory:
    def __init__(self, credential: ManagedCredential, repository: str | None = None) -> None:
        self.credential = credential
        self.repository = repository

    async def credentials(self, organisation_id: str) -> tuple[ManagedCredential, ...]:
        return (self.credential,)

    async def services(self, organisation_id: str) -> tuple[ConsumerService, ...]:
        if self.repository is None:
            return ()
        return (
            ConsumerService(
                id="service_one",
                organisation_id="org_one",
                application_id="application_one",
                environment_id="environment_one",
                runtime_connection_id="runtime_one",
                runtime_resource="projects/project-one/services/mailer",
                display_name="Mailer",
                repository=self.repository,
                identity="mailer@example.iam.gserviceaccount.com",
                created_at=NOW,
                updated_at=NOW,
            ),
        )

    async def bindings(self, organisation_id: str) -> tuple[ConsumerBinding, ...]:
        if self.repository is None:
            return ()
        return (
            ConsumerBinding(
                id="binding_one",
                organisation_id="org_one",
                credential_id=self.credential.id,
                service_id="service_one",
                environment_id="environment_one",
                runtime_connection_id="runtime_one",
                runtime_resource="projects/project-one/services/mailer",
                secret_reference="projects/project-one/secrets/mailer/versions/1",
                current_generation_id="generation_one",
                verification_id="verification_one",
            ),
        )


class Policies:
    def __init__(self, policy: PolicyVersion) -> None:
        self.policy = policy

    async def get_version(self, organisation_id: str, version_id: str) -> PolicyVersion:
        return self.policy


def _credential() -> ManagedCredential:
    return ManagedCredential(
        id="credential_one",
        organisation_id="org_one",
        connection_id="connection_one",
        provider="sendgrid",
        kind="api-key",
        display_name="Production mailer",
        provider_id="provider-key-one",
        policy_version="policy_one",
        playbook_version="playbook_one",
        created_at=NOW,
        updated_at=NOW,
    )


def _policy() -> PolicyVersion:
    definition = PolicyDefinition(
        required_checks=REQUIRED_CHECKS,
        allowed_tools=frozenset({"verification.run"}),
        allowed_recovery_modes=frozenset({RecoveryMode.ROLLBACK}),
        maximum_observation_seconds=1800,
        automatic_triggers=frozenset({"credential-exposure-detected"}),
        emergency_triggers=frozenset({"credential-exposure-detected"}),
        minimum_automatic_confidence=Confidence.HIGH,
    )
    return PolicyVersion(
        id="policy_one",
        organisation_id="org_one",
        policy_id="policy_root",
        number=1,
        definition=definition,
        digest=digest(definition),
        state=PolicyState.ACTIVE,
        created_by="admin_one",
        created_at=NOW,
        approved_by="admin_two",
        approved_at=NOW,
    )
