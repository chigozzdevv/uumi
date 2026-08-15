from typing import Protocol

from contracts import (
    Approval,
    ApprovalDecision,
    Incident,
    IncidentStatus,
    ManagedCredential,
    OverviewSummary,
    RotationRun,
    RunStatus,
)

_LIST_SCAN_LIMIT = 500

_ACTIVE_RUNS = frozenset(
    {
        RunStatus.PENDING,
        RunStatus.RUNNING,
        RunStatus.PAUSED,
        RunStatus.RECOVERING,
        RunStatus.CLEANUP,
    }
)

_OPEN_INCIDENTS = frozenset(
    {
        IncidentStatus.NEW,
        IncidentStatus.CORRELATING,
        IncidentStatus.ACTION,
        IncidentStatus.ROTATING,
    }
)


class OverviewCredentials(Protocol):
    async def credentials(self, organisation_id: str) -> tuple[ManagedCredential, ...]: ...


class OverviewRuns(Protocol):
    async def list_runs(self, organisation_id: str, limit: int) -> tuple[RotationRun, ...]: ...


class OverviewIncidents(Protocol):
    async def list_incidents(self, organisation_id: str, limit: int) -> tuple[Incident, ...]: ...


class OverviewApprovals(Protocol):
    async def list_approvals(self, organisation_id: str, limit: int) -> tuple[Approval, ...]: ...


class OverviewService:
    def __init__(
        self,
        credentials: OverviewCredentials,
        runs: OverviewRuns,
        incidents: OverviewIncidents,
        approvals: OverviewApprovals,
    ) -> None:
        self._credentials = credentials
        self._runs = runs
        self._incidents = incidents
        self._approvals = approvals

    async def summary(self, organisation_id: str) -> OverviewSummary:
        credentials = await self._credentials.credentials(organisation_id)
        runs = await self._runs.list_runs(organisation_id, _LIST_SCAN_LIMIT)
        incidents = await self._incidents.list_incidents(organisation_id, _LIST_SCAN_LIMIT)
        approvals = await self._approvals.list_approvals(organisation_id, _LIST_SCAN_LIMIT)
        return OverviewSummary(
            credentials=len(credentials),
            rotations_in_progress=sum(1 for run in runs if run.status in _ACTIVE_RUNS),
            failed_rotations=sum(1 for run in runs if run.status is RunStatus.FAILED),
            open_incidents=sum(1 for incident in incidents if incident.status in _OPEN_INCIDENTS),
            pending_approvals=sum(
                1 for approval in approvals if approval.decision is ApprovalDecision.PENDING
            ),
        )
