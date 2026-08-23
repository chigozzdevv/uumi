from contracts import (
    Connection,
    Contract,
    GoogleCloudOnboardingSession,
    GoogleCloudProject,
)
from core.auth import Permission
from fastapi import APIRouter, Request, status
from pydantic import Field

from api.deps import Identity, required, services

router = APIRouter(
    prefix="/v1/organisations/{organisation_id}/google-cloud",
    tags=["google-cloud"],
)


class BeginGoogleCloudResponse(Contract):
    session: GoogleCloudOnboardingSession
    state: str = Field(min_length=32, max_length=256)
    pkce_verifier: str = Field(min_length=43, max_length=128)
    authorization_url: str = Field(pattern=r"^https://accounts\.google\.com/")


class CompleteGoogleCloudRequest(Contract):
    state: str = Field(min_length=32, max_length=256)
    pkce_verifier: str = Field(min_length=43, max_length=128)
    code: str = Field(min_length=8, max_length=2048)


class CompleteGoogleCloudResponse(Contract):
    session: GoogleCloudOnboardingSession
    projects: tuple[GoogleCloudProject, ...]


class PrepareGoogleCloudConnectionRequest(Contract):
    project_id: str = Field(
        min_length=6,
        max_length=30,
        pattern=r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$",
    )
    automation_identity: str = Field(min_length=20, max_length=320)


class PrepareGoogleCloudConnectionResponse(Contract):
    connection: Connection
    grant_command: str = Field(min_length=32, max_length=4096)


class VerifyGoogleCloudConnectionRequest(Contract):
    expected_revision: int = Field(ge=0)


@router.post(
    "/onboarding",
    response_model=BeginGoogleCloudResponse,
    status_code=status.HTTP_201_CREATED,
)
async def begin_google_cloud_onboarding(
    organisation_id: str,
    request: Request,
    identity: Identity,
) -> BeginGoogleCloudResponse:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_WRITE)
    session, state, verifier, authorization_url = await required(
        api.google_cloud, "google-cloud"
    ).begin(organisation_id, identity.subject)
    return BeginGoogleCloudResponse(
        session=session,
        state=state,
        pkce_verifier=verifier,
        authorization_url=authorization_url,
    )


@router.post(
    "/onboarding/{session_id}",
    response_model=CompleteGoogleCloudResponse,
)
async def complete_google_cloud_onboarding(
    organisation_id: str,
    session_id: str,
    payload: CompleteGoogleCloudRequest,
    request: Request,
    identity: Identity,
) -> CompleteGoogleCloudResponse:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_WRITE)
    session, projects = await required(api.google_cloud, "google-cloud").complete(
        organisation_id,
        session_id,
        identity.subject,
        payload.state,
        payload.pkce_verifier,
        payload.code,
    )
    return CompleteGoogleCloudResponse(session=session, projects=projects)


@router.post(
    "/onboarding/{session_id}/connection",
    response_model=PrepareGoogleCloudConnectionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def prepare_google_cloud_connection(
    organisation_id: str,
    session_id: str,
    payload: PrepareGoogleCloudConnectionRequest,
    request: Request,
    identity: Identity,
) -> PrepareGoogleCloudConnectionResponse:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_WRITE)
    connection, command = await required(api.google_cloud, "google-cloud").prepare_connection(
        organisation_id,
        session_id,
        identity.subject,
        payload.project_id,
        payload.automation_identity,
    )
    return PrepareGoogleCloudConnectionResponse(
        connection=connection,
        grant_command=command,
    )


@router.post(
    "/onboarding/{session_id}/connection/verify",
    response_model=Connection,
)
async def verify_google_cloud_connection(
    organisation_id: str,
    session_id: str,
    payload: VerifyGoogleCloudConnectionRequest,
    request: Request,
    identity: Identity,
) -> Connection:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_WRITE)
    return await required(api.google_cloud, "google-cloud").verify_connection(
        organisation_id,
        session_id,
        identity.subject,
        payload.expected_revision,
    )
