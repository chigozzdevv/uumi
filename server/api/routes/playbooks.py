from contracts import (
    Contract,
    DryRun,
    Identifier,
    Playbook,
    PlaybookAssignment,
    PlaybookDraft,
    PlaybookVersion,
)
from core.auth import Permission
from core.errors import ResourceConflictError
from fastapi import APIRouter, Request, status

from api.deps import Identity, required, services

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


class ActivateRequest(Contract):
    dryrun_id: Identifier


class AssignmentRequest(Contract):
    credential_id: Identifier
    version_id: Identifier
    connection_ids: tuple[Identifier, ...]


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


@router.post("/{playbook_id}/dryruns", response_model=DryRun)
async def record_dryrun(
    organisation_id: Identifier,
    playbook_id: Identifier,
    body: DryRun,
    identity: Identity,
    request: Request,
) -> DryRun:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.PLAYBOOK_WRITE)
    if body.organisation_id != organisation_id or body.playbook_id != playbook_id:
        raise ResourceConflictError("dry run crosses playbook boundary")
    return await required(api.playbooks, "playbooks").record_dryrun(body)


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
    )
