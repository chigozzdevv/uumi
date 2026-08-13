from contracts import Contract, Identifier, WalkthroughSource
from core.auth import Permission
from fastapi import APIRouter, Request, Response, status
from pydantic import Field

from api.deps import Identity, required, services

router = APIRouter(
    prefix="/v1/organisations/{organisation_id}/playbooks/{playbook_id}/walkthroughs",
    tags=["walkthroughs"],
)


class BeginWalkthroughRequest(Contract):
    source_id: Identifier
    content_type: str = Field(pattern=r"^video/(mp4|webm|quicktime)$")
    size: int = Field(gt=0, le=2_000_000_000)
    crc32c: str = Field(min_length=4, max_length=16)


class BeginWalkthroughResponse(Contract):
    source: WalkthroughSource
    upload_url: str = Field(pattern=r"^https://")


@router.post("", response_model=BeginWalkthroughResponse, status_code=status.HTTP_201_CREATED)
async def begin(
    organisation_id: Identifier,
    playbook_id: Identifier,
    body: BeginWalkthroughRequest,
    identity: Identity,
    request: Request,
    response: Response,
) -> BeginWalkthroughResponse:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.PLAYBOOK_WRITE)
    source, upload, created = await required(api.walkthroughs, "walkthroughs").begin(
        organisation_id,
        playbook_id,
        body.source_id,
        body.content_type,
        body.size,
        body.crc32c,
        identity.actor_id,
    )
    if not created:
        response.status_code = status.HTTP_200_OK
    return BeginWalkthroughResponse(source=source, upload_url=upload)


@router.post("/{source_id}/complete", response_model=WalkthroughSource)
async def complete(
    organisation_id: Identifier,
    playbook_id: Identifier,
    source_id: Identifier,
    identity: Identity,
    request: Request,
) -> WalkthroughSource:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.PLAYBOOK_WRITE)
    return await required(api.walkthroughs, "walkthroughs").complete(
        organisation_id,
        playbook_id,
        source_id,
    )


@router.get("/{source_id}", response_model=WalkthroughSource)
async def get(
    organisation_id: Identifier,
    playbook_id: Identifier,
    source_id: Identifier,
    identity: Identity,
    request: Request,
) -> WalkthroughSource:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.PLAYBOOK_READ)
    return await required(api.walkthroughs, "walkthroughs").refresh(
        organisation_id,
        playbook_id,
        source_id,
    )
