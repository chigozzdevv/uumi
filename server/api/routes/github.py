from contracts import (
    Contract,
    GitHubInstallation,
    GitHubOnboardingSession,
    GitHubRepository,
    Identifier,
)
from core.auth import Permission
from fastapi import APIRouter, Request, status
from pydantic import Field

from api.deps import Identity, required, services

router = APIRouter(
    prefix="/v1/organisations/{organisation_id}/github",
    tags=["github"],
)


class BeginGitHubResponse(Contract):
    session: GitHubOnboardingSession
    state: str = Field(min_length=32, max_length=256)
    pkce_verifier: str = Field(min_length=43, max_length=128)
    installation_url: str = Field(pattern=r"^https://github\.com/")
    authorization_url: str = Field(pattern=r"^https://github\.com/")


class CompleteGitHubRequest(Contract):
    state: str = Field(min_length=32, max_length=256)
    pkce_verifier: str = Field(min_length=43, max_length=128)
    code: str = Field(min_length=8, max_length=512)
    installation_id: int = Field(gt=0)
    repository_mappings: dict[str, Identifier] = Field(min_length=1, max_length=400)


class CompleteGitHubResponse(Contract):
    session: GitHubOnboardingSession
    installation: GitHubInstallation
    repositories: tuple[GitHubRepository, ...]


@router.post("/onboarding", response_model=BeginGitHubResponse, status_code=status.HTTP_201_CREATED)
async def begin(
    organisation_id: Identifier,
    identity: Identity,
    request: Request,
) -> BeginGitHubResponse:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_WRITE)
    session, state, verifier, installation_url, authorization_url = await required(
        api.github, "github"
    ).begin(organisation_id, identity.subject)
    return BeginGitHubResponse(
        session=session,
        state=state,
        pkce_verifier=verifier,
        installation_url=installation_url,
        authorization_url=authorization_url,
    )


@router.post(
    "/onboarding/{session_id}/complete",
    response_model=CompleteGitHubResponse,
)
async def complete(
    organisation_id: Identifier,
    session_id: Identifier,
    body: CompleteGitHubRequest,
    identity: Identity,
    request: Request,
) -> CompleteGitHubResponse:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_WRITE)
    session, installation, repositories = await required(api.github, "github").complete(
        organisation_id,
        session_id,
        identity.subject,
        body.state,
        body.pkce_verifier,
        body.code,
        body.installation_id,
        body.repository_mappings,
    )
    return CompleteGitHubResponse(
        session=session,
        installation=installation,
        repositories=repositories,
    )
