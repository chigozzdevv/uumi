from contracts import Contract, Identifier, Policy, PolicyDefinition, PolicyVersion
from core.auth import Permission
from fastapi import APIRouter, Query, Request, status
from pydantic import Field

from api.deps import Identity, required, services

router = APIRouter(
    prefix="/v1/organisations/{organisation_id}/policies",
    tags=["policies"],
)


class CreatePolicyRequest(Contract):
    id: Identifier
    name: str = Field(min_length=1, max_length=160)


class CreateVersionRequest(Contract):
    id: Identifier
    definition: PolicyDefinition


@router.get("", response_model=tuple[Policy, ...])
async def list_policies(
    organisation_id: Identifier,
    identity: Identity,
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
) -> tuple[Policy, ...]:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.POLICY_READ)
    return await required(api.policies, "policies").list_policies(organisation_id, limit)


@router.post("", response_model=Policy, status_code=status.HTTP_201_CREATED)
async def create_policy(
    organisation_id: Identifier,
    body: CreatePolicyRequest,
    identity: Identity,
    request: Request,
) -> Policy:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.POLICY_WRITE)
    return await required(api.policies, "policies").create(organisation_id, body.id, body.name)


@router.post(
    "/{policy_id}/versions",
    response_model=PolicyVersion,
    status_code=status.HTTP_201_CREATED,
)
async def create_version(
    organisation_id: Identifier,
    policy_id: Identifier,
    body: CreateVersionRequest,
    identity: Identity,
    request: Request,
) -> PolicyVersion:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.POLICY_WRITE)
    return await required(api.policies, "policies").create_version(
        organisation_id, policy_id, body.id, body.definition, identity.actor_id
    )


@router.post(
    "/{policy_id}/versions/{version_id}/activate",
    response_model=PolicyVersion,
)
async def activate_version(
    organisation_id: Identifier,
    policy_id: Identifier,
    version_id: Identifier,
    identity: Identity,
    request: Request,
) -> PolicyVersion:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.POLICY_APPROVE)
    return await required(api.policies, "policies").activate(
        organisation_id, policy_id, version_id, identity.actor_id
    )
