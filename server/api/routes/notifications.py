from contracts import (
    Contract,
    Identifier,
    Notification,
    NotificationChannel,
    NotificationEndpoint,
    NotificationKind,
    NotificationProvider,
)
from core.auth import Permission
from fastapi import APIRouter, Query, Request, status
from pydantic import Field

from api.deps import Identity, required, services

router = APIRouter(
    prefix="/v1/organisations/{organisation_id}/notifications",
    tags=["notifications"],
)


class ReadRequest(Contract):
    expected_revision: int = Field(ge=0)


class EndpointRequest(Contract):
    id: Identifier
    display_name: str = Field(min_length=1, max_length=160)
    channel: NotificationChannel
    provider: NotificationProvider
    auth_reference: str = Field(min_length=20, max_length=1024)
    event_kinds: frozenset[NotificationKind] = Field(min_length=1)
    recipients: tuple[str, ...] = Field(default=(), max_length=50)
    sender: str | None = Field(default=None, max_length=320)


class EndpointStateRequest(Contract):
    expected_revision: int = Field(ge=0)
    enabled: bool


@router.get("", response_model=tuple[Notification, ...])
async def list_notifications(
    organisation_id: Identifier,
    identity: Identity,
    request: Request,
    limit: int = Query(default=100, ge=1, le=200),
) -> tuple[Notification, ...]:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.NOTIFICATION_READ)
    return await required(api.notifications, "notifications").list_notifications(
        organisation_id, limit
    )


@router.post("/{notification_id}/read", response_model=Notification)
async def mark_read(
    organisation_id: Identifier,
    notification_id: Identifier,
    body: ReadRequest,
    identity: Identity,
    request: Request,
) -> Notification:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.NOTIFICATION_READ)
    return await required(api.notifications, "notifications").mark_read(
        organisation_id, notification_id, body.expected_revision
    )


@router.get("/endpoints", response_model=tuple[NotificationEndpoint, ...])
async def list_endpoints(
    organisation_id: Identifier,
    identity: Identity,
    request: Request,
) -> tuple[NotificationEndpoint, ...]:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.NOTIFICATION_WRITE)
    return await required(api.notifications, "notifications").list_endpoints(organisation_id)


@router.post("/endpoints", response_model=NotificationEndpoint, status_code=status.HTTP_201_CREATED)
async def create_endpoint(
    organisation_id: Identifier,
    body: EndpointRequest,
    identity: Identity,
    request: Request,
) -> NotificationEndpoint:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.NOTIFICATION_WRITE)
    return await required(api.notifications, "notifications").register_endpoint(
        body.id,
        organisation_id,
        body.display_name,
        body.channel,
        body.provider,
        body.auth_reference,
        body.event_kinds,
        body.recipients,
        body.sender,
    )


@router.post("/endpoints/{endpoint_id}/state", response_model=NotificationEndpoint)
async def set_endpoint_state(
    organisation_id: Identifier,
    endpoint_id: Identifier,
    body: EndpointStateRequest,
    identity: Identity,
    request: Request,
) -> NotificationEndpoint:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.NOTIFICATION_WRITE)
    return await required(api.notifications, "notifications").set_endpoint_enabled(
        organisation_id,
        endpoint_id,
        body.expected_revision,
        body.enabled,
    )
