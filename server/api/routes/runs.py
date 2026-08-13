from contracts import (
    CleanupRunCommand,
    CompleteStageCommand,
    Contract,
    CreateRunCommand,
    FailRunCommand,
    Failure,
    Identifier,
    PauseRunCommand,
    RecoverRunCommand,
    RenewLeaseCommand,
    ResumeRunCommand,
    RotationRun,
    RunStep,
    Stage,
    StageProof,
    StartRunCommand,
    Trigger,
)
from core.auth import Permission
from core.storage import MutationResult
from fastapi import APIRouter, Request, Response, status
from pydantic import AwareDatetime, Field

from api.deps import (
    IdempotencyKey,
    Identity,
    command_id,
    services,
)

router = APIRouter(
    prefix="/v1/organisations/{organisation_id}/runs",
    tags=["runs"],
)


class CreateRunRequest(Contract):
    credential_id: Identifier
    policy_version: Identifier
    source: str = Field(min_length=1, max_length=64)
    event_id: str = Field(min_length=1, max_length=256)
    reason: str = Field(min_length=1, max_length=1024)
    urgency: str = Field(min_length=1, max_length=32)
    received_at: AwareDatetime


class RevisionRequest(Contract):
    expected_revision: int = Field(ge=0)


class StartRequest(RevisionRequest):
    expires_at: AwareDatetime


class RenewRequest(StartRequest):
    fencing_token: int = Field(gt=0)


class ProofRequest(Contract):
    stage: Stage
    checks: frozenset[str]
    evidence_ids: tuple[Identifier, ...]
    recorded_at: AwareDatetime


class CompleteRequest(RevisionRequest):
    fencing_token: int = Field(gt=0)
    proof: ProofRequest


class FencedRequest(RevisionRequest):
    fencing_token: int = Field(gt=0)


class FailureRequest(FencedRequest):
    failure: Failure


class LeaseRequest(RevisionRequest):
    expires_at: AwareDatetime


class MutationResponse(Contract):
    run: RotationRun
    step: RunStep
    applied: bool


OrganisationId = Identifier
RunId = Identifier


def _response(result: MutationResult) -> MutationResponse:
    return MutationResponse(run=result.run, step=result.step, applied=result.applied)


@router.post("", response_model=MutationResponse)
async def create_run(
    organisation_id: OrganisationId,
    body: CreateRunRequest,
    identity: Identity,
    key: IdempotencyKey,
    request: Request,
    response: Response,
) -> MutationResponse:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.RUN_WRITE)
    command = CreateRunCommand(
        id=command_id(identity, organisation_id, key),
        organisation_id=organisation_id,
        credential_id=body.credential_id,
        policy_version=body.policy_version,
        trigger=Trigger(
            source=body.source,
            event_id=body.event_id,
            actor_id=identity.actor_id,
            reason=body.reason,
            urgency=body.urgency,
            received_at=body.received_at,
        ),
    )
    result = await api.workflow.create(command)
    response.status_code = status.HTTP_201_CREATED if result.applied else status.HTTP_200_OK
    return _response(result)


@router.get("/{run_id}", response_model=RotationRun)
async def get_run(
    organisation_id: OrganisationId,
    run_id: RunId,
    identity: Identity,
    request: Request,
) -> RotationRun:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.RUN_READ)
    return await api.workflow.get(organisation_id, run_id)


@router.post("/{run_id}/start", response_model=MutationResponse)
async def start_run(
    organisation_id: OrganisationId,
    run_id: RunId,
    body: StartRequest,
    identity: Identity,
    key: IdempotencyKey,
    request: Request,
) -> MutationResponse:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.RUN_WRITE)
    result = await api.workflow.start(
        StartRunCommand(
            id=command_id(identity, organisation_id, key),
            organisation_id=organisation_id,
            run_id=run_id,
            actor_id=identity.actor_id,
            expected_revision=body.expected_revision,
            owner_id=identity.actor_id,
            expires_at=body.expires_at,
        )
    )
    return _response(result)


@router.post("/{run_id}/renew", response_model=MutationResponse)
async def renew_run(
    organisation_id: OrganisationId,
    run_id: RunId,
    body: RenewRequest,
    identity: Identity,
    key: IdempotencyKey,
    request: Request,
) -> MutationResponse:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.RUN_WRITE)
    result = await api.workflow.renew(
        RenewLeaseCommand(
            id=command_id(identity, organisation_id, key),
            organisation_id=organisation_id,
            run_id=run_id,
            actor_id=identity.actor_id,
            expected_revision=body.expected_revision,
            owner_id=identity.actor_id,
            expires_at=body.expires_at,
            fencing_token=body.fencing_token,
        )
    )
    return _response(result)


@router.post("/{run_id}/complete", response_model=MutationResponse)
async def complete_stage(
    organisation_id: OrganisationId,
    run_id: RunId,
    body: CompleteRequest,
    identity: Identity,
    key: IdempotencyKey,
    request: Request,
) -> MutationResponse:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.RUN_WRITE)
    proof = StageProof(
        run_id=run_id,
        organisation_id=organisation_id,
        stage=body.proof.stage,
        checks=body.proof.checks,
        evidence_ids=body.proof.evidence_ids,
        actor_id=identity.actor_id,
        recorded_at=body.proof.recorded_at,
    )
    result = await api.workflow.complete(
        CompleteStageCommand(
            id=command_id(identity, organisation_id, key),
            organisation_id=organisation_id,
            run_id=run_id,
            actor_id=identity.actor_id,
            expected_revision=body.expected_revision,
            fencing_token=body.fencing_token,
            proof=proof,
        )
    )
    return _response(result)


@router.post("/{run_id}/pause", response_model=MutationResponse)
async def pause_run(
    organisation_id: OrganisationId,
    run_id: RunId,
    body: FencedRequest,
    identity: Identity,
    key: IdempotencyKey,
    request: Request,
) -> MutationResponse:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.RUN_WRITE)
    result = await api.workflow.pause(
        PauseRunCommand(
            id=command_id(identity, organisation_id, key),
            organisation_id=organisation_id,
            run_id=run_id,
            actor_id=identity.actor_id,
            expected_revision=body.expected_revision,
            fencing_token=body.fencing_token,
        )
    )
    return _response(result)


@router.post("/{run_id}/resume", response_model=MutationResponse)
async def resume_run(
    organisation_id: OrganisationId,
    run_id: RunId,
    body: LeaseRequest,
    identity: Identity,
    key: IdempotencyKey,
    request: Request,
) -> MutationResponse:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.RUN_WRITE)
    result = await api.workflow.resume(
        ResumeRunCommand(
            id=command_id(identity, organisation_id, key),
            organisation_id=organisation_id,
            run_id=run_id,
            actor_id=identity.actor_id,
            expected_revision=body.expected_revision,
            owner_id=identity.actor_id,
            expires_at=body.expires_at,
        )
    )
    return _response(result)


@router.post("/{run_id}/cleanup", response_model=MutationResponse)
async def cleanup_run(
    organisation_id: OrganisationId,
    run_id: RunId,
    body: FailureRequest,
    identity: Identity,
    key: IdempotencyKey,
    request: Request,
) -> MutationResponse:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.RUN_WRITE)
    result = await api.workflow.cleanup(
        CleanupRunCommand(
            id=command_id(identity, organisation_id, key),
            organisation_id=organisation_id,
            run_id=run_id,
            actor_id=identity.actor_id,
            expected_revision=body.expected_revision,
            fencing_token=body.fencing_token,
            failure=body.failure,
        )
    )
    return _response(result)


@router.post("/{run_id}/fail", response_model=MutationResponse)
async def fail_run(
    organisation_id: OrganisationId,
    run_id: RunId,
    body: FailureRequest,
    identity: Identity,
    key: IdempotencyKey,
    request: Request,
) -> MutationResponse:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.RUN_WRITE)
    result = await api.workflow.fail(
        FailRunCommand(
            id=command_id(identity, organisation_id, key),
            organisation_id=organisation_id,
            run_id=run_id,
            actor_id=identity.actor_id,
            expected_revision=body.expected_revision,
            fencing_token=body.fencing_token,
            failure=body.failure,
        )
    )
    return _response(result)


@router.post("/{run_id}/recover", response_model=MutationResponse)
async def recover_run(
    organisation_id: OrganisationId,
    run_id: RunId,
    body: LeaseRequest,
    identity: Identity,
    key: IdempotencyKey,
    request: Request,
) -> MutationResponse:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.RUN_WRITE)
    result = await api.workflow.recover(
        RecoverRunCommand(
            id=command_id(identity, organisation_id, key),
            organisation_id=organisation_id,
            run_id=run_id,
            actor_id=identity.actor_id,
            expected_revision=body.expected_revision,
            owner_id=identity.actor_id,
            expires_at=body.expires_at,
        )
    )
    return _response(result)
