from datetime import UTC, datetime

from contracts import (
    AgentKind,
    AgentResult,
    AgentTask,
    Connection,
    Contract,
    Identifier,
    Playbook,
    PlaybookDraft,
    PlaybookVersion,
)
from core.auth import Permission
from core.errors import PlaybookError
from fastapi import APIRouter, Query, Request, status
from pydantic import Field

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


class AttachRequest(Contract):
    connection_id: Identifier
    expected_revision: int = Field(ge=0)


@router.get("", response_model=tuple[Playbook, ...])
async def list_playbooks(
    organisation_id: Identifier,
    identity: Identity,
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
) -> tuple[Playbook, ...]:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.PLAYBOOK_READ)
    return await required(api.playbooks, "playbooks").list_playbooks(organisation_id, limit)


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
    if body.source_ids:
        await required(api.walkthroughs, "walkthroughs").ready(
            organisation_id,
            playbook_id,
            body.source_ids,
        )
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
    sources = await required(api.walkthroughs, "walkthroughs").ready(
        organisation_id,
        playbook_id,
        body.source_ids,
    )
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
            context={
                "walkthroughs": tuple(
                    source.analysis.model_dump(mode="json")
                    for source in sources
                    if source.analysis is not None
                )
            },
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


@router.post("/{playbook_id}/versions/{version_id}/publish", response_model=PlaybookVersion)
async def publish(
    organisation_id: Identifier,
    playbook_id: Identifier,
    version_id: Identifier,
    identity: Identity,
    request: Request,
) -> PlaybookVersion:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.PLAYBOOK_APPROVE)
    return await required(api.playbooks, "playbooks").publish(
        organisation_id,
        playbook_id,
        version_id,
        identity.actor_id,
    )


@router.post("/{playbook_id}/versions/{version_id}/attach", response_model=Connection)
async def attach(
    organisation_id: Identifier,
    playbook_id: Identifier,
    version_id: Identifier,
    body: AttachRequest,
    identity: Identity,
    request: Request,
) -> Connection:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.PLAYBOOK_APPROVE)
    return await required(api.playbooks, "playbooks").attach(
        organisation_id,
        body.connection_id,
        body.expected_revision,
        playbook_id,
        version_id,
    )
