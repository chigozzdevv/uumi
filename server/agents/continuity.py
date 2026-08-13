from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from connectors.google.rest import GoogleRestClient
from contracts import AgentMemory, AgentRegistration, AgentSession

from agents.storage import AgentRepository


class AgentContinuityService:
    def __init__(
        self,
        repository: AgentRepository,
        google: GoogleRestClient,
        project_id: str,
        firestore_database: str,
        clock: Callable[[], datetime],
    ) -> None:
        self._repository = repository
        self._google = google
        self._project = project_id
        self._database = firestore_database
        self._clock = clock

    async def create_session(
        self,
        registration: AgentRegistration,
        session_id: str,
        run_id: str,
        purpose: str,
        ttl: timedelta = timedelta(hours=24),
    ) -> AgentSession:
        if ttl < timedelta(hours=24):
            raise ValueError("managed Agent Runtime sessions require at least 24 hours TTL")
        parent = registration.deployment
        endpoint = _endpoint(registration.region)
        operation = await self._google.request(
            "POST",
            f"{endpoint}/v1/{parent}/sessions",
            params={"sessionId": _remote_id(session_id)},
            json={
                "displayName": purpose,
                "userId": registration.organisation_id,
                "ttl": f"{int(ttl.total_seconds())}s",
                "labels": {
                    "firekey-org": registration.organisation_id,
                    "firekey-run": run_id,
                    "firekey-agent": registration.kind.value,
                },
                "sessionState": {
                    "organisation_id": registration.organisation_id,
                    "run_id": run_id,
                    "project_id": self._project,
                    "firestore_database": self._database,
                },
            },
            expected=frozenset({200}),
        )
        response = await self._google.wait_operation(
            _string(operation, "name"), base_url=f"{endpoint}/v1"
        )
        now = self._clock()
        session = AgentSession(
            id=session_id,
            organisation_id=registration.organisation_id,
            run_id=run_id,
            agent=registration.kind,
            remote_session=_string(response, "name"),
            region=registration.region,
            purpose=purpose,
            created_at=now,
            expires_at=now + ttl,
        )
        return await self._repository.save_session(session)

    async def remember(
        self,
        registration: AgentRegistration,
        memory_id: str,
        fact: str,
        provenance: tuple[str, ...],
        approved_by: str,
        ttl: timedelta = timedelta(days=30),
    ) -> AgentMemory:
        if not provenance:
            raise ValueError("agent memory requires provenance")
        _validate_fact(fact)
        endpoint = _endpoint(registration.region)
        operation = await self._google.request(
            "POST",
            f"{endpoint}/v1beta1/{registration.deployment}/memories",
            params={"memoryId": _remote_id(memory_id)},
            json={
                "displayName": memory_id,
                "description": "Approved FireKey operational fact",
                "fact": fact,
                "scope": {
                    "organisation": registration.organisation_id,
                    "agent": registration.kind.value,
                },
                "revisionLabels": {"approved-by": approved_by},
                "ttl": f"{int(ttl.total_seconds())}s",
                "disableMemoryRevisions": False,
            },
            expected=frozenset({200}),
        )
        response = await self._google.wait_operation(
            _string(operation, "name"), base_url=f"{endpoint}/v1beta1"
        )
        now = self._clock()
        memory = AgentMemory(
            id=memory_id,
            organisation_id=registration.organisation_id,
            agent=registration.kind,
            remote_memory=_string(response, "name"),
            fact=fact,
            provenance=provenance,
            approved_by=approved_by,
            region=registration.region,
            created_at=now,
            expires_at=now + ttl,
        )
        return await self._repository.save_memory(memory)

    async def retrieve(
        self,
        registration: AgentRegistration,
        query: str,
        count: int = 10,
    ) -> tuple[dict[str, Any], ...]:
        endpoint = _endpoint(registration.region)
        result = await self._google.request(
            "POST",
            f"{endpoint}/v1beta1/{registration.deployment}/memories:retrieve",
            json={
                "scope": {
                    "organisation": registration.organisation_id,
                    "agent": registration.kind.value,
                },
                "memoryTypes": ["NATURAL_LANGUAGE_COLLECTION"],
                "similaritySearchParams": {"searchQuery": query, "topK": count},
            },
            expected=frozenset({200}),
        )
        memories = result.get("retrievedMemories", [])
        if not isinstance(memories, list):
            raise ValueError("Memory Bank returned an invalid response")
        return tuple(item for item in memories if isinstance(item, dict))


def _endpoint(region: str) -> str:
    return f"https://{region}-aiplatform.googleapis.com"


def _remote_id(value: str) -> str:
    normalised = value.replace("_", "-")[:63].strip("-")
    if not normalised or not normalised[0].isalnum() or not normalised[-1].isalnum():
        raise ValueError("resource ID cannot be represented by Agent Runtime")
    return normalised


def _string(value: dict[str, Any], name: str) -> str:
    result = value.get(name)
    if not isinstance(result, str) or not result:
        raise ValueError(f"Google API response is missing {name}")
    return result


def _validate_fact(fact: str) -> None:
    if not fact or len(fact) > 2048:
        raise ValueError("memory fact length is invalid")
    forbidden = ("-----BEGIN", "api_key=", "password=", "authorization:", "bearer ")
    if any(marker in fact.lower() for marker in (item.lower() for item in forbidden)):
        raise ValueError("memory fact appears to contain credential material")
