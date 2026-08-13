from contracts import Contract, Identifier, Incident, IngestionEvent
from core.auth import Permission
from core.errors import ResourceConflictError
from fastapi import APIRouter, Request, Response, status

from api.deps import IdempotencyKey, Identity, command_id, required, services

router = APIRouter(
    prefix="/v1/organisations/{organisation_id}/incidents",
    tags=["incidents"],
)


class IncidentResponse(Contract):
    incident: Incident
    applied: bool


@router.post("/ingest", response_model=IncidentResponse)
async def ingest(
    organisation_id: Identifier,
    body: IngestionEvent,
    identity: Identity,
    key: IdempotencyKey,
    request: Request,
    response: Response,
) -> IncidentResponse:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INCIDENT_WRITE)
    if body.organisation_id != organisation_id:
        raise ResourceConflictError("incident crosses organisation boundary")
    incident_id = command_id(identity, organisation_id, key).replace("cmd_", "incident_", 1)
    incident, applied = await required(api.incidents, "incidents").ingest(incident_id, body)
    response.status_code = status.HTTP_201_CREATED if applied else status.HTTP_200_OK
    return IncidentResponse(incident=incident, applied=applied)
