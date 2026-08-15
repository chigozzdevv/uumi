from datetime import UTC, datetime

import pytest
from contracts import (
    Confidence,
    ConsumerBinding,
    ConsumerService,
    CorrelationCandidate,
    Incident,
    IncidentStatus,
    IngestionEvent,
    ManagedCredential,
    Severity,
    SourceResource,
)
from core.errors import ResourceConflictError
from core.incident import IncidentService
from core.workflow import RunWorkflow
from testkit import MemoryRunRepository

NOW = datetime(2026, 8, 13, 12, tzinfo=UTC)


class Incidents:
    def __init__(self) -> None:
        self.values: dict[str, Incident] = {}

    async def ingest(self, incident: Incident, event: IngestionEvent) -> tuple[Incident, bool]:
        existing = self.values.get(incident.id)
        if existing is not None:
            return existing, False
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
        candidates: tuple[CorrelationCandidate, ...],
        credential_id: str | None,
        updated_at: datetime,
    ) -> Incident:
        current = self.values[incident_id]
        if current.revision != expected_revision:
            raise ResourceConflictError("incident revision changed")
        changed = current.model_copy(
            update={
                "candidates": candidates,
                "credential_id": credential_id,
                "status": (
                    IncidentStatus.ACTION
                    if credential_id is not None
                    else IncidentStatus.CORRELATING
                ),
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
        if current.revision != expected_revision:
            raise ResourceConflictError("incident revision changed")
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
        values = []
        for incident_id, current in self.values.items():
            if current.run_id != run_id:
                continue
            changed = current.model_copy(
                update={
                    "status": status,
                    "updated_at": updated_at,
                    "revision": current.revision + 1,
                }
            )
            self.values[incident_id] = changed
            values.append(changed)
        return tuple(values)


class Inventory:
    def __init__(self, credentials: tuple[ManagedCredential, ...]) -> None:
        self._credentials = credentials

    async def credentials(self, organisation_id: str) -> tuple[ManagedCredential, ...]:
        return self._credentials

    async def services(self, organisation_id: str) -> tuple[ConsumerService, ...]:
        return (_service(),)

    async def bindings(self, organisation_id: str) -> tuple[ConsumerBinding, ...]:
        return tuple(_binding(item.id) for item in self._credentials)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_exact_provider_identity_correlates_and_starts_rotation() -> None:
    repository = Incidents()
    workflow = RunWorkflow(MemoryRunRepository(), clock=lambda: NOW)
    service = IncidentService(
        repository,
        lambda: NOW,
        Inventory((_credential("credential_one", "provider-key-one"),)),
        workflow,
    )

    incident, applied = await service.ingest("incident_one", _event("provider-key-one"))

    assert applied
    assert incident.status is IncidentStatus.ACTION
    assert incident.credential_id == "credential_one"
    assert incident.candidates[0].confidence is Confidence.VERIFIED

    linked, run, created = await service.start_rotation(
        "org_one",
        incident.id,
        "command_one",
        "operator_one",
        "policy_one",
        "credential exposed",
        "critical",
        NOW,
    )

    assert created
    assert linked.status is IncidentStatus.ROTATING
    assert linked.run_id == run.id
    assert run.trigger.source == "incident"
    assert run.credential_id == "credential_one"

    contained = await service.advance_run("org_one", run.id, IncidentStatus.CONTAINED)
    resolved = await service.advance_run("org_one", run.id, IncidentStatus.RESOLVED)

    assert contained[0].status is IncidentStatus.CONTAINED
    assert resolved[0].status is IncidentStatus.RESOLVED


@pytest.mark.anyio
async def test_ambiguous_repository_match_requires_explicit_confirmation() -> None:
    repository = Incidents()
    service = IncidentService(
        repository,
        lambda: NOW,
        Inventory(
            (
                _credential("credential_one", "provider-key-one"),
                _credential("credential_two", "provider-key-two"),
            )
        ),
    )

    incident, _ = await service.ingest("incident_one", _event(None))

    assert incident.status is IncidentStatus.CORRELATING
    assert incident.credential_id is None
    assert {item.confidence for item in incident.candidates} == {Confidence.HIGH}

    confirmed = await service.confirm("org_one", incident.id, incident.revision, "credential_two")

    assert confirmed.status is IncidentStatus.ACTION
    assert confirmed.credential_id == "credential_two"
    assert sum(item.confidence is Confidence.VERIFIED for item in confirmed.candidates) == 1


@pytest.mark.anyio
async def test_secret_resource_exactly_correlates_through_consumer_binding() -> None:
    repository = Incidents()
    service = IncidentService(
        repository,
        lambda: NOW,
        Inventory((_credential("credential_one", "provider-key-one"),)),
    )
    event = _event(None).model_copy(
        update={
            "source": "secret-manager",
            "source_event_id": "message-one",
            "kind": "credential-rotation-due",
            "resource": SourceResource(
                provider="google-secret-manager",
                provider_id="projects/project-one/secrets/mailer",
            ),
        }
    )

    incident, _ = await service.ingest("incident_one", event)

    assert incident.status is IncidentStatus.ACTION
    assert incident.credential_id == "credential_one"
    assert incident.candidates[0].confidence is Confidence.VERIFIED


def _credential(credential_id: str, provider_id: str) -> ManagedCredential:
    return ManagedCredential(
        id=credential_id,
        organisation_id="org_one",
        connection_id="connection_one",
        provider="sendgrid",
        kind="api-key",
        display_name=credential_id,
        provider_id=provider_id,
        consumer_ids=("service_one",),
        policy_version="policy_one",
        playbook_version="version_one",
        created_at=NOW,
        updated_at=NOW,
    )


def _service() -> ConsumerService:
    return ConsumerService(
        id="service_one",
        organisation_id="org_one",
        application_id="application_one",
        environment_id="environment_one",
        runtime_connection_id="runtime_one",
        runtime_resource="projects/project-one/services/mailer",
        display_name="Mailer",
        repository="example/mailer",
        identity="mailer@example.iam.gserviceaccount.com",
        created_at=NOW,
        updated_at=NOW,
    )


def _binding(credential_id: str) -> ConsumerBinding:
    return ConsumerBinding(
        id=f"binding_{credential_id}",
        organisation_id="org_one",
        credential_id=credential_id,
        service_id="service_one",
        environment_id="environment_one",
        runtime_connection_id="runtime_one",
        runtime_resource="projects/project-one/services/mailer",
        secret_reference="projects/project-one/secrets/mailer/versions/1",
        current_generation_id="generation_one",
        verification_id="verification_one",
    )


def _event(provider_id: str | None) -> IngestionEvent:
    return IngestionEvent(
        id="ingestion_one",
        organisation_id="org_one",
        source="github-secret-scanning",
        source_event_id="example/mailer#1",
        kind="credential-exposure-detected",
        observed_at=NOW,
        severity=Severity.CRITICAL,
        confidence=Confidence.HIGH,
        resource=SourceResource(
            repository="example/mailer",
            provider="sendgrid",
            provider_id=provider_id,
        ),
        source_reference="https://github.com/example/mailer/security/secret-scanning/1",
        received_at=NOW,
    )
