import asyncio
import hashlib
import json
import os
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from broker.evidence import GcsEvidenceSink
from connectors.google import GoogleRestClient
from contracts import (
    AgentKind,
    AgentMemory,
    AgentRegistration,
    AgentResult,
    AgentSession,
    AgentTask,
)
from google.cloud.firestore_v1 import AsyncClient

from agents.armor import ModelArmorGuard
from agents.continuity import AgentContinuityService
from agents.fleet import AgentFleetService
from agents.probe import _inventory_context
from agents.runtime import AgentRuntimeService
from agents.storage import AgentRepository


class ObservedContinuity(AgentContinuityService):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.sessions: list[AgentSession] = []
        self.retrievals: list[tuple[dict[str, Any], ...]] = []

    async def create_session(
        self,
        registration: AgentRegistration,
        session_id: str,
        run_id: str,
        purpose: str,
        task_context: dict[str, Any],
        ttl: timedelta = timedelta(hours=24),
    ) -> AgentSession:
        session = await super().create_session(
            registration, session_id, run_id, purpose, task_context, ttl
        )
        self.sessions.append(session)
        return session

    async def retrieve(
        self,
        registration: AgentRegistration,
        query: str,
        count: int = 10,
    ) -> tuple[dict[str, Any], ...]:
        memories = await super().retrieve(registration, query, count)
        self.retrievals.append(memories)
        return memories


async def run_continuity_probe(
    runtime: AgentRuntimeService,
    continuity: ObservedContinuity,
    registration: AgentRegistration,
    evidence: GcsEvidenceSink,
    organisation_id: str,
    run_id: str,
    clock: Callable[[], datetime],
) -> tuple[dict[str, Any], bool]:
    suffix = hashlib.sha256(run_id.encode()).hexdigest()[:12]
    fact = (
        "credential_evidence_demo is approved for service_evidence_demo during the "
        f"managed continuity evidence run checkpoint {suffix}."
    )
    fact_digest = hashlib.sha256(fact.encode()).hexdigest()
    context_record = {
        "schema": "uumi.continuity-context.v1",
        "organisation_id": organisation_id,
        "run_id": run_id,
        "agent": registration.kind.value,
        "fact_sha256": fact_digest,
        "approved_by": "uumi_continuity_probe",
        "ttl_seconds": 86400,
    }
    context_evidence = await evidence.store(
        organisation_id,
        run_id,
        "continuity-context",
        json.dumps(context_record, separators=(",", ":"), sort_keys=True).encode(),
        "application/json",
        clock(),
    )
    memory = await continuity.remember(
        registration,
        f"memory_continuity_{suffix}",
        fact,
        (context_evidence.id,),
        "uumi_continuity_probe",
        timedelta(hours=24),
    )
    await _await_memory(continuity, registration, fact)
    continuity.sessions.clear()
    continuity.retrievals.clear()

    results = []
    observations: list[tuple[AgentResult, AgentSession | None, tuple[dict[str, Any], ...]]] = []
    for index in range(1, 3):
        session_offset = len(continuity.sessions)
        retrieval_offset = len(continuity.retrievals)
        result = await runtime.execute(
            AgentTask(
                id=f"task_continuity_{index}_{suffix}",
                organisation_id=organisation_id,
                run_id=run_id,
                agent=AgentKind.INVENTORY,
                skill="detect_stale_mapping",
                objective=(
                    "Use detect_stale_mapping for credential_evidence_demo and preserve its "
                    "approved service_evidence_demo context across this continuity check."
                ),
                context=_inventory_context(),
                requested_at=clock(),
            )
        )
        results.append(result)
        session = (
            continuity.sessions[session_offset]
            if len(continuity.sessions) == session_offset + 1
            else None
        )
        memories = (
            continuity.retrievals[retrieval_offset]
            if len(continuity.retrievals) == retrieval_offset + 1
            else ()
        )
        observations.append((result, session, memories))

    passed = _passed(memory, fact, results, continuity.sessions, continuity.retrievals)
    report = {
        "schema": "uumi.continuity-probe.v1",
        "organisation_id": organisation_id,
        "run_id": run_id,
        "passed": passed,
        "recorded_at": clock().isoformat(),
        "memory": _memory_summary(memory),
        "invocations": [
            _invocation_summary(result, session, memories)
            for result, session, memories in observations
        ],
    }
    content = json.dumps(report, separators=(",", ":"), sort_keys=True).encode()
    summary = await evidence.store(
        organisation_id,
        run_id,
        "continuity-probe",
        content,
        "application/json",
        clock(),
    )
    return {**report, "summary_evidence_id": summary.id}, passed


async def _await_memory(
    continuity: ObservedContinuity,
    registration: AgentRegistration,
    fact: str,
) -> None:
    for attempt in range(12):
        memories = await continuity.retrieve(registration, fact, count=5)
        if any(item.get("fact") == fact for item in memories):
            return
        if attempt < 11:
            await asyncio.sleep(5)
    raise RuntimeError("approved Memory Bank context was not retrievable before its deadline")


def _passed(
    memory: AgentMemory,
    fact: str,
    results: list[AgentResult],
    sessions: list[AgentSession],
    retrievals: list[tuple[dict[str, Any], ...]],
) -> bool:
    return (
        len(results) == 2
        and all(result.succeeded and len(result.evidence_ids) >= 2 for result in results)
        and len(sessions) == 2
        and len({session.remote_session for session in sessions}) == 2
        and all(session.expires_at > session.created_at for session in sessions)
        and len(retrievals) == 2
        and all(
            any(
                item.get("fact") == fact
                and item.get("provenance") == memory.provenance
                and item.get("approved_by") == memory.approved_by
                for item in items
            )
            for items in retrievals
        )
        and memory.expires_at > memory.created_at
    )


def _memory_summary(memory: AgentMemory) -> dict[str, Any]:
    return {
        "id": memory.id,
        "agent": memory.agent.value,
        "remote_memory": memory.remote_memory,
        "remote_memory_sha256": hashlib.sha256(memory.remote_memory.encode()).hexdigest(),
        "fact_sha256": hashlib.sha256(memory.fact.encode()).hexdigest(),
        "provenance": list(memory.provenance),
        "approved_by": memory.approved_by,
        "created_at": memory.created_at.isoformat(),
        "expires_at": memory.expires_at.isoformat(),
    }


def _invocation_summary(
    result: AgentResult,
    session: AgentSession | None,
    memories: tuple[dict[str, Any], ...],
) -> dict[str, Any]:
    return {
        "task_id": result.task_id,
        "agent": result.agent.value,
        "skill": result.skill,
        "succeeded": result.succeeded,
        "session_id": session.id if session else None,
        "remote_session": session.remote_session if session else None,
        "remote_session_sha256": (
            hashlib.sha256(session.remote_session.encode()).hexdigest() if session else None
        ),
        "session_expires_at": session.expires_at.isoformat() if session else None,
        "retrieved_memory_count": len(memories),
        "retrieved_fact_sha256": sorted(
            hashlib.sha256(str(item.get("fact", "")).encode()).hexdigest() for item in memories
        ),
        "evidence_ids": list(result.evidence_ids),
        "output_keys": sorted(result.output),
        "error": result.error,
    }


async def _main() -> int:
    project_id = _required("UUMI_PROJECT_ID")
    database = os.environ.get("UUMI_FIRESTORE_DATABASE", "(default)")
    region = _required("UUMI_REGION")
    bucket = _required("UUMI_EVIDENCE_BUCKET")
    template = _required("UUMI_MODEL_ARMOR_TEMPLATE")
    organisation_id = _required("UUMI_PROBE_ORGANISATION")
    run_id = _required("UUMI_PROBE_RUN_ID")
    firestore = AsyncClient(project=project_id, database=database)
    google = GoogleRestClient(timeout=180)
    repository = AgentRepository(firestore)
    fleet = AgentFleetService(repository)
    evidence = GcsEvidenceSink(google, firestore, bucket, region)
    continuity = ObservedContinuity(repository, google, project_id, database, _now)
    runtime = AgentRuntimeService(
        fleet,
        continuity,
        google,
        ModelArmorGuard(google, template, evidence, _now),
        _now,
    )
    try:
        registration = await fleet.resolve(
            organisation_id, AgentKind.INVENTORY, "detect_stale_mapping"
        )
        report, passed = await run_continuity_probe(
            runtime,
            continuity,
            registration,
            evidence,
            organisation_id,
            run_id,
            _now,
        )
        print(json.dumps(report, separators=(",", ":"), sort_keys=True))
        return 0 if passed else 1
    finally:
        firestore.close()  # type: ignore[no-untyped-call]
        await google.close()


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"{name} is required")
    return value


def _now() -> datetime:
    return datetime.now(UTC)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
