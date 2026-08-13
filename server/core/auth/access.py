from enum import StrEnum
from typing import Protocol

from contracts import Contract
from google.cloud.firestore_v1 import AsyncClient

from core.auth.identity import AuthenticatedIdentity
from core.errors import AuthorizationError, StorageIntegrityError
from core.storage.paths import FirestorePaths


class Permission(StrEnum):
    RUN_READ = "run.read"
    RUN_WRITE = "run.write"
    INVENTORY_READ = "inventory.read"
    INVENTORY_WRITE = "inventory.write"
    PLAYBOOK_READ = "playbook.read"
    PLAYBOOK_WRITE = "playbook.write"
    PLAYBOOK_APPROVE = "playbook.approve"
    APPROVAL_READ = "approval.read"
    APPROVAL_DECIDE = "approval.decide"
    INCIDENT_WRITE = "incident.write"
    AGENT_READ = "agent.read"
    AGENT_WRITE = "agent.write"
    AUDIT_READ = "audit.read"


class Role(StrEnum):
    VIEWER = "viewer"
    OPERATOR = "operator"
    ADMINISTRATOR = "administrator"
    AUTOMATION = "automation"


ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.VIEWER: frozenset(
        {
            Permission.RUN_READ,
            Permission.INVENTORY_READ,
            Permission.PLAYBOOK_READ,
            Permission.APPROVAL_READ,
            Permission.AGENT_READ,
            Permission.AUDIT_READ,
        }
    ),
    Role.OPERATOR: frozenset(
        {
            Permission.RUN_READ,
            Permission.RUN_WRITE,
            Permission.INVENTORY_READ,
            Permission.PLAYBOOK_READ,
            Permission.PLAYBOOK_WRITE,
            Permission.APPROVAL_READ,
            Permission.AGENT_READ,
            Permission.INCIDENT_WRITE,
        }
    ),
    Role.AUTOMATION: frozenset(
        {
            Permission.RUN_READ,
            Permission.RUN_WRITE,
            Permission.INVENTORY_READ,
            Permission.PLAYBOOK_READ,
            Permission.PLAYBOOK_WRITE,
            Permission.APPROVAL_READ,
            Permission.AGENT_READ,
            Permission.INCIDENT_WRITE,
        }
    ),
    Role.ADMINISTRATOR: frozenset(Permission),
}


class PrincipalGrant(Contract):
    subject: str
    roles: frozenset[Role]
    enabled: bool = True


class AccessRepository(Protocol):
    async def get(
        self,
        organisation_id: str,
        identity: AuthenticatedIdentity,
    ) -> PrincipalGrant | None: ...


class FirestoreAccessRepository:
    def __init__(self, client: AsyncClient) -> None:
        self._client = client

    async def get(
        self,
        organisation_id: str,
        identity: AuthenticatedIdentity,
    ) -> PrincipalGrant | None:
        reference = self._client.document(
            FirestorePaths.principal(organisation_id, identity.document_id)
        )
        snapshot = await reference.get()
        if not snapshot.exists:
            return None
        data = snapshot.to_dict()
        if data is None:
            raise StorageIntegrityError(f"principal grant {snapshot.id} has no data")
        grant = PrincipalGrant.model_validate(data)
        if grant.subject != identity.subject:
            raise StorageIntegrityError("principal subject does not match its document path")
        return grant


class AccessControl:
    def __init__(self, repository: AccessRepository) -> None:
        self._repository = repository

    async def require(
        self,
        identity: AuthenticatedIdentity,
        organisation_id: str,
        permission: Permission,
    ) -> None:
        grant = await self._repository.get(organisation_id, identity)
        if grant is None or not grant.enabled:
            raise AuthorizationError("principal is not enabled for this organisation")
        allowed = frozenset(item for role in grant.roles for item in ROLE_PERMISSIONS[role])
        if permission not in allowed:
            raise AuthorizationError(f"principal lacks {permission.value}")
