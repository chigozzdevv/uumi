from contracts import AccountProfile, Contract, Identifier, MemberRole, TeamMember
from core.auth import Permission
from core.ids import new_id
from fastapi import APIRouter, Request, status
from pydantic import Field

from api.deps import Identity, required, services

router = APIRouter(
    prefix="/v1/organisations/{organisation_id}/settings",
    tags=["settings"],
)


class ProfileRequest(Contract):
    expected_revision: int = Field(ge=0)
    display_name: str = Field(min_length=1, max_length=120)


class InvitationRequest(Contract):
    email: str = Field(min_length=3, max_length=320)
    role: MemberRole


class MemberRequest(Contract):
    expected_revision: int = Field(ge=0)
    role: MemberRole
    enabled: bool


class InvitationStateRequest(Contract):
    expected_revision: int = Field(ge=0)


@router.get("/profile", response_model=AccountProfile)
async def profile(
    organisation_id: Identifier,
    identity: Identity,
    request: Request,
) -> AccountProfile:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.PROFILE_READ)
    return await required(api.accounts, "accounts").profile(organisation_id, identity)


@router.patch("/profile", response_model=AccountProfile)
async def update_profile(
    organisation_id: Identifier,
    body: ProfileRequest,
    identity: Identity,
    request: Request,
) -> AccountProfile:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.PROFILE_WRITE)
    changed = await required(api.accounts, "accounts").update_profile(
        organisation_id,
        identity,
        body.expected_revision,
        body.display_name,
    )
    if api.audit is not None:
        await api.audit.append(
            new_id("audit"),
            organisation_id,
            "profile.updated",
            identity.actor_id,
            f"profiles/{changed.id}",
            {"revision": changed.revision},
        )
    return changed


@router.get("/team", response_model=tuple[TeamMember, ...])
async def team(
    organisation_id: Identifier,
    identity: Identity,
    request: Request,
) -> tuple[TeamMember, ...]:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.TEAM_READ)
    return await required(api.accounts, "accounts").team(organisation_id)


@router.post(
    "/team/invitations",
    response_model=TeamMember,
    status_code=status.HTTP_201_CREATED,
)
async def invite(
    organisation_id: Identifier,
    body: InvitationRequest,
    identity: Identity,
    request: Request,
) -> TeamMember:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.TEAM_WRITE)
    member = await required(api.accounts, "accounts").invite(
        organisation_id,
        identity,
        body.email,
        body.role,
    )
    if api.audit is not None:
        await api.audit.append(
            new_id("audit"),
            organisation_id,
            "team.invited",
            identity.actor_id,
            f"team/{member.id}",
            {"role": member.role.value},
        )
    return member


@router.patch("/team/members/{member_id}", response_model=TeamMember)
async def update_member(
    organisation_id: Identifier,
    member_id: Identifier,
    body: MemberRequest,
    identity: Identity,
    request: Request,
) -> TeamMember:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.TEAM_WRITE)
    member = await required(api.accounts, "accounts").update_member(
        organisation_id,
        identity,
        member_id,
        body.expected_revision,
        body.role,
        body.enabled,
    )
    if api.audit is not None:
        await api.audit.append(
            new_id("audit"),
            organisation_id,
            "team.member-updated",
            identity.actor_id,
            f"team/{member.id}",
            {"role": member.role.value, "enabled": member.status.value == "active"},
        )
    return member


@router.post("/team/invitations/{invitation_id}/cancel", response_model=TeamMember)
async def cancel_invitation(
    organisation_id: Identifier,
    invitation_id: Identifier,
    body: InvitationStateRequest,
    identity: Identity,
    request: Request,
) -> TeamMember:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.TEAM_WRITE)
    member = await required(api.accounts, "accounts").revoke_invitation(
        organisation_id,
        invitation_id,
        body.expected_revision,
    )
    if api.audit is not None:
        await api.audit.append(
            new_id("audit"),
            organisation_id,
            "team.invitation-cancelled",
            identity.actor_id,
            f"team/{member.id}",
            {"revision": member.revision},
        )
    return member
