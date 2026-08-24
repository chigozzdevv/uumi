from collections.abc import Callable
from datetime import datetime
from typing import Any

from contracts import MemberRole, Organisation, OrganisationMembership, TeamInvitation
from google.cloud.firestore_v1 import AsyncClient
from google.cloud.firestore_v1.async_transaction import AsyncTransaction, async_transactional
from google.cloud.firestore_v1.base_document import DocumentSnapshot
from google.cloud.firestore_v1.base_query import FieldFilter

from core.account.service import invitation_id
from core.auth import AuthenticatedIdentity, PrincipalGrant, Role
from core.errors import ResourceConflictError, ResourceNotFoundError, StorageIntegrityError
from core.storage.codec import encode
from core.storage.paths import FirestorePaths


class FirestoreAccountRepository:
    def __init__(self, client: AsyncClient, clock: Callable[[], datetime]) -> None:
        self._client = client
        self._clock = clock

    async def session(
        self,
        identity: AuthenticatedIdentity,
    ) -> tuple[OrganisationMembership, ...]:
        if identity.email is not None and identity.email_verified:
            email = identity.email.strip().lower()
            async for invitation_snapshot in (
                self._client.collection_group("team-invitations")
                .where(filter=FieldFilter("email", "==", email))
                .limit(200)
                .stream()
            ):
                await self.get(_organisation_id(invitation_snapshot), identity)

        memberships: list[OrganisationMembership] = []
        async for principal_snapshot in (
            self._client.collection_group("principals")
            .where(filter=FieldFilter("subject", "==", identity.subject))
            .limit(200)
            .stream()
        ):
            grant = _grant(principal_snapshot, identity)
            if not grant.enabled or Role.AUTOMATION in grant.roles:
                continue
            organisation_id = _organisation_id(principal_snapshot)
            organisation_snapshot = await self._client.document(
                FirestorePaths.organisation(organisation_id)
            ).get()
            if not organisation_snapshot.exists:
                raise StorageIntegrityError(
                    f"organisation {organisation_id} has no membership record"
                )
            memberships.append(
                OrganisationMembership(
                    organisation=Organisation.model_validate(_data(organisation_snapshot)),
                    role=_human_role(grant),
                )
            )
        return tuple(
            sorted(
                memberships,
                key=lambda item: (item.organisation.name.casefold(), item.organisation.id),
            )
        )

    async def create_organisation(
        self,
        organisation: Organisation,
        identity: AuthenticatedIdentity,
        created_at: datetime,
    ) -> OrganisationMembership:
        organisation_reference = self._client.document(FirestorePaths.organisation(organisation.id))
        principal_reference = self._client.document(
            FirestorePaths.principal(organisation.id, identity.document_id)
        )
        grant = PrincipalGrant(
            subject=identity.subject,
            roles=frozenset({Role.ADMINISTRATOR}),
            email=identity.email.strip().lower() if identity.email else None,
            display_name=identity.display_name,
            connected_via=identity.connected_via,
            created_at=created_at,
            updated_at=created_at,
        )

        @async_transactional
        async def apply(transaction: AsyncTransaction) -> OrganisationMembership:
            transaction.create(organisation_reference, encode(organisation))
            transaction.create(principal_reference, encode(grant))
            return OrganisationMembership(
                organisation=organisation,
                role=MemberRole.ADMINISTRATOR,
            )

        return await apply(self._client.transaction(max_attempts=5))

    async def get(
        self,
        organisation_id: str,
        identity: AuthenticatedIdentity,
    ) -> PrincipalGrant | None:
        reference = self._client.document(
            FirestorePaths.principal(organisation_id, identity.document_id)
        )
        snapshot = await reference.get()
        if snapshot.exists:
            return _grant(snapshot, identity)
        if identity.email is None or not identity.email_verified:
            return None
        email = identity.email
        invitation_reference = self._client.document(
            FirestorePaths.invitation(organisation_id, invitation_id(email))
        )

        @async_transactional
        async def activate(transaction: AsyncTransaction) -> PrincipalGrant | None:
            principal_snapshot = await reference.get(transaction=transaction)
            if principal_snapshot.exists:
                return _grant(principal_snapshot, identity)
            invitation_snapshot = await invitation_reference.get(transaction=transaction)
            if not invitation_snapshot.exists:
                return None
            invitation = TeamInvitation.model_validate(_data(invitation_snapshot))
            now = self._clock()
            if (
                invitation.email != email.strip().lower()
                or invitation.accepted_at is not None
                or invitation.revoked_at is not None
                or invitation.expires_at <= now
            ):
                return None
            grant = PrincipalGrant(
                subject=identity.subject,
                roles=frozenset({Role(invitation.role.value)}),
                email=invitation.email,
                display_name=identity.display_name,
                connected_via=identity.connected_via,
                created_at=now,
                updated_at=now,
            )
            accepted = invitation.model_copy(
                update={
                    "accepted_at": now,
                    "updated_at": now,
                    "revision": invitation.revision + 1,
                }
            )
            transaction.create(reference, encode(grant))
            transaction.set(invitation_reference, encode(accepted))
            return grant

        return await activate(self._client.transaction(max_attempts=5))

    async def sync_identity(
        self,
        organisation_id: str,
        identity: AuthenticatedIdentity,
        updated_at: datetime,
    ) -> PrincipalGrant:
        reference = self._client.document(
            FirestorePaths.principal(organisation_id, identity.document_id)
        )

        @async_transactional
        async def apply(transaction: AsyncTransaction) -> PrincipalGrant:
            snapshot = await reference.get(transaction=transaction)
            if not snapshot.exists:
                raise ResourceNotFoundError("the signed-in account is not an organisation member")
            current = _grant(snapshot, identity)
            changes: dict[str, Any] = {}
            if current.email is None and identity.email is not None:
                changes["email"] = identity.email.strip().lower()
            if current.display_name is None and identity.display_name:
                changes["display_name"] = identity.display_name
            if current.connected_via is None:
                changes["connected_via"] = identity.connected_via
            if not changes:
                return current
            changed = current.model_copy(
                update={
                    **changes,
                    "updated_at": updated_at,
                    "revision": current.revision + 1,
                }
            )
            transaction.set(reference, encode(changed))
            return changed

        return await apply(self._client.transaction(max_attempts=5))

    async def update_profile(
        self,
        organisation_id: str,
        identity: AuthenticatedIdentity,
        expected_revision: int,
        display_name: str,
        updated_at: datetime,
    ) -> PrincipalGrant:
        reference = self._client.document(
            FirestorePaths.principal(organisation_id, identity.document_id)
        )

        @async_transactional
        async def apply(transaction: AsyncTransaction) -> PrincipalGrant:
            snapshot = await reference.get(transaction=transaction)
            if not snapshot.exists:
                raise ResourceNotFoundError("the signed-in account is not an organisation member")
            current = _grant(snapshot, identity)
            if current.revision != expected_revision:
                raise ResourceConflictError(
                    f"profile expected revision {expected_revision}, found {current.revision}"
                )
            changed = current.model_copy(
                update={
                    "display_name": display_name,
                    "email": current.email or identity.email,
                    "connected_via": current.connected_via or identity.connected_via,
                    "updated_at": updated_at,
                    "revision": current.revision + 1,
                }
            )
            transaction.set(reference, encode(changed))
            return changed

        return await apply(self._client.transaction(max_attempts=5))

    async def list_principals(self, organisation_id: str) -> tuple[PrincipalGrant, ...]:
        values: list[PrincipalGrant] = []
        async for snapshot in (
            self._client.collection(FirestorePaths.principal_collection(organisation_id))
            .limit(200)
            .stream()
        ):
            values.append(PrincipalGrant.model_validate(_data(snapshot)))
        return tuple(values)

    async def list_invitations(self, organisation_id: str) -> tuple[TeamInvitation, ...]:
        values: list[TeamInvitation] = []
        async for snapshot in (
            self._client.collection(FirestorePaths.invitation_collection(organisation_id))
            .limit(200)
            .stream()
        ):
            values.append(TeamInvitation.model_validate(_data(snapshot)))
        return tuple(values)

    async def save_invitation(self, invitation: TeamInvitation) -> TeamInvitation:
        reference = self._client.document(
            FirestorePaths.invitation(invitation.organisation_id, invitation.id)
        )

        @async_transactional
        async def apply(transaction: AsyncTransaction) -> TeamInvitation:
            snapshot = await reference.get(transaction=transaction)
            if not snapshot.exists:
                transaction.create(reference, encode(invitation))
                return invitation
            current = TeamInvitation.model_validate(_data(snapshot))
            if (
                current.accepted_at is None
                and current.revoked_at is None
                and current.expires_at > invitation.created_at
            ):
                raise ResourceConflictError("this email already has a pending invitation")
            changed = invitation.model_copy(update={"revision": current.revision + 1})
            transaction.set(reference, encode(changed))
            return changed

        return await apply(self._client.transaction(max_attempts=5))

    async def update_member(
        self,
        organisation_id: str,
        member_id: str,
        expected_revision: int,
        role: Role,
        enabled: bool,
        updated_at: datetime,
    ) -> PrincipalGrant:
        document_id = member_id.removeprefix("principal_")
        reference = self._client.document(FirestorePaths.principal(organisation_id, document_id))

        @async_transactional
        async def apply(transaction: AsyncTransaction) -> PrincipalGrant:
            snapshot = await reference.get(transaction=transaction)
            if not snapshot.exists:
                raise ResourceNotFoundError(f"team member {member_id} was not found")
            current = PrincipalGrant.model_validate(_data(snapshot))
            if current.revision != expected_revision:
                raise ResourceConflictError(
                    f"team member expected revision {expected_revision}, found {current.revision}"
                )
            changed = current.model_copy(
                update={
                    "roles": frozenset({role}),
                    "enabled": enabled,
                    "updated_at": updated_at,
                    "revision": current.revision + 1,
                }
            )
            transaction.set(reference, encode(changed))
            return changed

        return await apply(self._client.transaction(max_attempts=5))

    async def revoke_invitation(
        self,
        organisation_id: str,
        invitation_id_value: str,
        expected_revision: int,
        revoked_at: datetime,
    ) -> TeamInvitation:
        reference = self._client.document(
            FirestorePaths.invitation(organisation_id, invitation_id_value)
        )

        @async_transactional
        async def apply(transaction: AsyncTransaction) -> TeamInvitation:
            snapshot = await reference.get(transaction=transaction)
            if not snapshot.exists:
                raise ResourceNotFoundError(f"team invitation {invitation_id_value} was not found")
            current = TeamInvitation.model_validate(_data(snapshot))
            if current.revision != expected_revision:
                raise ResourceConflictError(
                    "team invitation expected revision "
                    f"{expected_revision}, found {current.revision}"
                )
            if current.accepted_at is not None:
                raise ResourceConflictError("an accepted invitation cannot be cancelled")
            changed = current.model_copy(
                update={
                    "revoked_at": revoked_at,
                    "updated_at": revoked_at,
                    "revision": current.revision + 1,
                }
            )
            transaction.set(reference, encode(changed))
            return changed

        return await apply(self._client.transaction(max_attempts=5))


def _grant(snapshot: DocumentSnapshot, identity: AuthenticatedIdentity) -> PrincipalGrant:
    grant = PrincipalGrant.model_validate(_data(snapshot))
    if grant.subject != identity.subject:
        raise StorageIntegrityError("principal subject does not match its document path")
    return grant


def _organisation_id(snapshot: DocumentSnapshot) -> str:
    parts = snapshot.reference.path.split("/")
    if len(parts) < 4 or parts[0] != "organisations":
        raise StorageIntegrityError(
            f"document {snapshot.reference.path} is outside an organisation"
        )
    return str(parts[1])


def _human_role(grant: PrincipalGrant) -> MemberRole:
    for role in (Role.ADMINISTRATOR, Role.OPERATOR, Role.VIEWER):
        if role in grant.roles:
            return MemberRole(role.value)
    raise StorageIntegrityError("organisation membership has no human role")


def _data(snapshot: DocumentSnapshot) -> dict[str, Any]:
    data = snapshot.to_dict()
    if data is None:
        raise StorageIntegrityError(f"document {snapshot.id} has no data")
    return data
