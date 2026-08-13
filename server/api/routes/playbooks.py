from datetime import UTC, datetime

from contracts import (
    AgentKind,
    AgentResult,
    AgentTask,
    Contract,
    DryRun,
    Identifier,
    Playbook,
    PlaybookAssignment,
    PlaybookDraft,
    PlaybookVersion,
    RotationRun,
)
from core.auth import Permission
from core.errors import PlaybookError
from fastapi import APIRouter, Request, Response, status
from pydantic import AwareDatetime, Field

from api.deps import IdempotencyKey, Identity, command_id, required, services

router = APIRouter(
    prefix="/v1/organisations/{organisation_id}/playbooks",
    tags=["playbooks"],
)


class CreateVersionRequest(Contract):
    version_id: Identifier
    definition: PlaybookDraft
    source_ids: tuple[Identifier, ...] = ()


class VersionResponse(Contract):
    playbook: Playbook
    version: PlaybookVersion


class BuildVersionRequest(Contract):
    version_id: Identifier
    objective: str = Field(min_length=1, max_length=2048)
    source_ids: tuple[Identifier, ...] = Field(min_length=1)


class BuildVersionResponse(VersionResponse):
    agent: AgentResult


class DryRunRequest(Contract):
    id: Identifier
    version_id: Identifier
    environment_id: Identifier
    credential_id: Identifier
    policy_version: Identifier
    reason: str = Field(min_length=1, max_length=1024)
    urgency: str = Field(min_length=1, max_length=32)
    received_at: AwareDatetime


class DryRunResponse(Contract):
    dryrun: DryRun
    run: RotationRun
    applied: bool


class ActivateRequest(Contract):
    dryrun_id: Identifier


class AssignmentRequest(Contract):
    credential_id: Identifier
    version_id: Identifier
    connection_ids: tuple[Identifier, ...]
    dry_run_only: bool = False
    environment_id: Identifier | None = None


@router.post(
    "/{playbook_id}/versions",
    response_model=VersionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_version(
    organisation_id: Identifier,
    playbook_id: Identifier,
    body: CreateVersionRequest,
    identity: Identity,
    request: Request,
) -> VersionResponse:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.PLAYBOOK_WRITE)
    root, version = await required(api.playbooks, "playbooks").create_version(
        organisation_id,
        playbook_id,
        body.version_id,
        body.definition,
        identity.actor_id,
        body.source_ids,
    )
    return VersionResponse(playbook=root, version=version)


@router.post(
    "/{playbook_id}/build",
    response_model=BuildVersionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def build_version(
    organisation_id: Identifier,
    playbook_id: Identifier,
    body: BuildVersionRequest,
    identity: Identity,
    key: IdempotencyKey,
    request: Request,
) -> BuildVersionResponse:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.PLAYBOOK_WRITE)
    task_id = command_id(identity, organisation_id, key).replace("cmd_", "task_", 1)
    result = await required(api.agents, "agents").execute(
        AgentTask(
            id=task_id,
            organisation_id=organisation_id,
            run_id=f"run_{task_id.removeprefix('task_')}",
            agent=AgentKind.PLAYBOOK,
            skill="build_playbook",
            objective=(
                f"{body.objective}\nBuild playbook {playbook_id} from the declared sanitised "
                f"evidence IDs: {', '.join(body.source_ids)}."
            ),
            evidence_ids=body.source_ids,
            requested_at=datetime.now(UTC),
        )
    )
    if not result.succeeded:
        raise PlaybookError(result.error or "Playbook Builder Agent failed")
    candidate = result.output.get("playbook_draft", result.output)
    try:
        definition = PlaybookDraft.model_validate(candidate)
    except ValueError as error:
        raise PlaybookError("Playbook Builder Agent returned an invalid definition") from error
    root, version = await required(api.playbooks, "playbooks").create_version(
        organisation_id,
        playbook_id,
        body.version_id,
        definition,
        identity.actor_id,
        body.source_ids,
    )
    return BuildVersionResponse(playbook=root, version=version, agent=result)


@router.post("/{playbook_id}/dryruns", response_model=DryRunResponse)
async def start_dryrun(
    organisation_id: Identifier,
    playbook_id: Identifier,
    body: DryRunRequest,
    identity: Identity,
    key: IdempotencyKey,
    request: Request,
    response: Response,
) -> DryRunResponse:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.PLAYBOOK_WRITE)
    dryrun, run, applied = await required(api.playbooks, "playbooks").start_dryrun(
        organisation_id,
        playbook_id,
        body.id,
        body.version_id,
        body.environment_id,
        body.credential_id,
        body.policy_version,
        identity.actor_id,
        command_id(identity, organisation_id, key),
        body.reason,
        body.urgency,
        body.received_at,
    )
    response.status_code = status.HTTP_201_CREATED if applied else status.HTTP_200_OK
    return DryRunResponse(dryrun=dryrun, run=run, applied=applied)


@router.post("/{playbook_id}/versions/{version_id}/activate", response_model=PlaybookVersion)
async def activate(
    organisation_id: Identifier,
    playbook_id: Identifier,
    version_id: Identifier,
    body: ActivateRequest,
    identity: Identity,
    request: Request,
) -> PlaybookVersion:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.PLAYBOOK_APPROVE)
    return await required(api.playbooks, "playbooks").activate(
        organisation_id,
        playbook_id,
        version_id,
        body.dryrun_id,
        identity.actor_id,
    )


@router.post("/{playbook_id}/assignments", response_model=PlaybookAssignment)
async def assign(
    organisation_id: Identifier,
    playbook_id: Identifier,
    body: AssignmentRequest,
    identity: Identity,
    request: Request,
) -> PlaybookAssignment:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.PLAYBOOK_APPROVE)
    return await required(api.playbooks, "playbooks").assign(
        organisation_id,
        body.credential_id,
        playbook_id,
        body.version_id,
        body.connection_ids,
        identity.actor_id,
        body.dry_run_only,
        body.environment_id,
    )
