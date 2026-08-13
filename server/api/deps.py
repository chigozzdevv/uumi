import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, cast

from core.approval import ApprovalService
from core.auth import (
    AccessControl,
    AuthenticatedIdentity,
    FirestoreAccessRepository,
    GoogleTokenVerifier,
    IdentityTokenVerifier,
)
from core.config import Settings
from core.errors import AuthenticationError
from core.incident import IncidentService
from core.inventory import InventoryService
from core.playbook import PlaybookService
from core.storage import (
    FirestoreApprovalRepository,
    FirestoreIncidentRepository,
    FirestoreInventoryRepository,
    FirestorePlaybookRepository,
    FirestoreRunRepository,
)
from core.workflow import RunWorkflow
from fastapi import Depends, Header, Request
from google.cloud.firestore_v1 import AsyncClient


@dataclass(frozen=True, slots=True)
class ApiServices:
    workflow: RunWorkflow
    access: AccessControl
    tokens: IdentityTokenVerifier
    inventory: InventoryService | None = None
    playbooks: PlaybookService | None = None
    approvals: ApprovalService | None = None
    incidents: IncidentService | None = None


def build_services(settings: Settings | None = None) -> ApiServices:
    configured = settings or Settings()
    client = AsyncClient(
        project=configured.project_id,
        database=configured.firestore_database,
    )
    return ApiServices(
        workflow=RunWorkflow(FirestoreRunRepository(client)),
        access=AccessControl(FirestoreAccessRepository(client)),
        tokens=GoogleTokenVerifier(configured.oidc_audience),
        inventory=InventoryService(FirestoreInventoryRepository(client)),
        playbooks=PlaybookService(FirestorePlaybookRepository(client), _now),
        approvals=ApprovalService(FirestoreApprovalRepository(client), _now),
        incidents=IncidentService(FirestoreIncidentRepository(client), _now),
    )


def services(request: Request) -> ApiServices:
    return cast(ApiServices, request.app.state.services)


async def authenticated_identity(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> AuthenticatedIdentity:
    if authorization is None:
        raise AuthenticationError("bearer identity token is required")
    scheme, separator, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not separator or not token:
        raise AuthenticationError("authorization must use a bearer token")
    return await services(request).tokens.verify(token)


def idempotency_key(
    value: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=8, max_length=256),
    ],
) -> str:
    return value


def command_id(
    identity: AuthenticatedIdentity,
    organisation_id: str,
    key: str,
) -> str:
    payload = f"{identity.subject}\0{organisation_id}\0{key}".encode()
    return f"cmd_{hashlib.sha256(payload).hexdigest()[:40]}"


def required[T](value: T | None, name: str) -> T:
    if value is None:
        raise RuntimeError(f"API service {name} is not configured")
    return value


def _now() -> datetime:
    return datetime.now(UTC)


Identity = Annotated[AuthenticatedIdentity, Depends(authenticated_identity)]
IdempotencyKey = Annotated[str, Depends(idempotency_key)]
