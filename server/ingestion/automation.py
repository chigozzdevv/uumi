import hashlib
from typing import Protocol

from contracts import (
    Confidence,
    Incident,
    IncidentStatus,
    IngestionEvent,
    ManagedCredential,
    PolicyState,
    PolicyVersion,
)
from core.errors import ResourceConflictError
from core.incident import IncidentService


class CredentialReader(Protocol):
    async def credentials(self, organisation_id: str) -> tuple[ManagedCredential, ...]: ...


class PolicyReader(Protocol):
    async def get_version(self, organisation_id: str, version_id: str) -> PolicyVersion: ...


class IncidentAutomation:
    def __init__(
        self,
        incidents: IncidentService,
        inventory: CredentialReader,
        policies: PolicyReader,
    ) -> None:
        self._incidents = incidents
        self._inventory = inventory
        self._policies = policies

    async def ingest(self, event: IngestionEvent) -> tuple[Incident, bool]:
        incident_id = _identifier("incident", event.id)
        incident, applied = await self._incidents.ingest(incident_id, event)
        if incident.status is IncidentStatus.ROTATING:
            return incident, applied
        if incident.status is IncidentStatus.CORRELATING and len(incident.candidates) == 1:
            candidate = incident.candidates[0]
            credential = await self._credential(event.organisation_id, candidate.credential_id)
            policy = await self._policies.get_version(
                event.organisation_id, credential.policy_version
            )
            if self._automatic(policy, event, candidate.confidence):
                incident = await self._incidents.confirm(
                    event.organisation_id,
                    incident.id,
                    incident.revision,
                    credential.id,
                )
        if incident.status is not IncidentStatus.ACTION or incident.credential_id is None:
            return incident, applied
        credential = await self._credential(event.organisation_id, incident.credential_id)
        policy = await self._policies.get_version(event.organisation_id, credential.policy_version)
        if not self._automatic(policy, event, Confidence.VERIFIED):
            return incident, applied
        urgency = "emergency" if event.kind in policy.definition.emergency_triggers else "routine"
        command_id = _identifier("command", event.id)
        try:
            linked, _, _ = await self._incidents.start_rotation(
                event.organisation_id,
                incident.id,
                command_id,
                "firekey_ingestion",
                policy.id,
                f"automatic policy response to {event.kind}",
                urgency,
                event.observed_at,
            )
        except ResourceConflictError:
            current = await self._incidents.get(event.organisation_id, incident.id)
            if current.status is IncidentStatus.ROTATING:
                return current, applied
            raise
        return linked, applied

    def _automatic(
        self,
        policy: PolicyVersion,
        event: IngestionEvent,
        confidence: Confidence,
    ) -> bool:
        levels = {
            Confidence.LOW: 0,
            Confidence.MEDIUM: 1,
            Confidence.HIGH: 2,
            Confidence.VERIFIED: 3,
        }
        return (
            policy.state is PolicyState.ACTIVE
            and event.kind in policy.definition.automatic_triggers
            and levels[confidence] >= levels[policy.definition.minimum_automatic_confidence]
        )

    async def _credential(self, organisation_id: str, credential_id: str) -> ManagedCredential:
        matches = tuple(
            credential
            for credential in await self._inventory.credentials(organisation_id)
            if credential.id == credential_id
        )
        if len(matches) != 1:
            raise ResourceConflictError("correlated credential disappeared from inventory")
        return matches[0]


def _identifier(prefix: str, event_id: str) -> str:
    return f"{prefix}_{hashlib.sha256(event_id.encode()).hexdigest()[:40]}"
