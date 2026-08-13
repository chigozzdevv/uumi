from collections.abc import Callable
from datetime import datetime

from contracts import Policy, PolicyState, PolicyVersion
from google.cloud.firestore_v1 import AsyncClient
from google.cloud.firestore_v1.async_transaction import AsyncTransaction, async_transactional
from policy import digest

from core.errors import ResourceConflictError, ResourceNotFoundError, StorageIntegrityError
from core.storage.codec import encode
from core.storage.paths import FirestorePaths


class FirestorePolicyRepository:
    def __init__(self, client: AsyncClient) -> None:
        self._client = client

    async def create(self, policy: Policy) -> Policy:
        reference = self._client.document(FirestorePaths.policy(policy.organisation_id, policy.id))
        try:
            await reference.create(encode(policy))
        except Exception as error:
            from google.api_core.exceptions import AlreadyExists

            if isinstance(error, AlreadyExists):
                raise ResourceConflictError(f"policy {policy.id} already exists") from error
            raise
        return policy

    async def create_version(
        self,
        organisation_id: str,
        policy_id: str,
        factory: Callable[[int], PolicyVersion],
    ) -> PolicyVersion:
        policy_ref = self._client.document(FirestorePaths.policy(organisation_id, policy_id))

        @async_transactional
        async def apply(transaction: AsyncTransaction) -> PolicyVersion:
            snapshot = await policy_ref.get(transaction=transaction)
            if not snapshot.exists:
                raise ResourceNotFoundError(f"policy {policy_id} was not found")
            data = snapshot.to_dict()
            if data is None:
                raise StorageIntegrityError(f"policy {policy_id} has no data")
            policy = Policy.model_validate(data)
            version = factory(policy.latest_version + 1)
            if (
                version.organisation_id != organisation_id
                or version.policy_id != policy_id
                or version.number != policy.latest_version + 1
                or version.digest != digest(version.definition)
                or version.state is not PolicyState.DRAFT
            ):
                raise StorageIntegrityError("policy version factory changed immutable inputs")
            version_ref = self._client.document(
                FirestorePaths.policy_version(organisation_id, version.id)
            )
            existing = await version_ref.get(transaction=transaction)
            if existing.exists:
                raise ResourceConflictError(f"policy version {version.id} already exists")
            transaction.create(version_ref, encode(version))
            transaction.set(
                policy_ref,
                encode(
                    policy.model_copy(
                        update={
                            "latest_version": version.number,
                            "updated_at": version.created_at,
                            "revision": policy.revision + 1,
                        }
                    )
                ),
            )
            return version

        return await apply(self._client.transaction(max_attempts=5))

    async def activate(
        self,
        organisation_id: str,
        policy_id: str,
        version_id: str,
        actor_id: str,
        now: datetime,
    ) -> PolicyVersion:
        policy_ref = self._client.document(FirestorePaths.policy(organisation_id, policy_id))
        version_ref = self._client.document(
            FirestorePaths.policy_version(organisation_id, version_id)
        )

        @async_transactional
        async def apply(transaction: AsyncTransaction) -> PolicyVersion:
            policy_snapshot = await policy_ref.get(transaction=transaction)
            version_snapshot = await version_ref.get(transaction=transaction)
            if not policy_snapshot.exists or not version_snapshot.exists:
                raise ResourceNotFoundError("policy or policy version was not found")
            policy_data = policy_snapshot.to_dict()
            version_data = version_snapshot.to_dict()
            if policy_data is None or version_data is None:
                raise StorageIntegrityError("policy activation data is missing")
            policy = Policy.model_validate(policy_data)
            version = PolicyVersion.model_validate(version_data)
            if version.policy_id != policy.id or version.state is not PolicyState.DRAFT:
                raise ResourceConflictError("only a draft version of this policy can activate")
            active = version.model_copy(
                update={
                    "state": PolicyState.ACTIVE,
                    "approved_by": actor_id,
                    "approved_at": now,
                }
            )
            if policy.active_version_id is not None:
                old_ref = self._client.document(
                    FirestorePaths.policy_version(organisation_id, policy.active_version_id)
                )
                old_snapshot = await old_ref.get(transaction=transaction)
                if not old_snapshot.exists or old_snapshot.to_dict() is None:
                    raise StorageIntegrityError("active policy version disappeared")
                old = PolicyVersion.model_validate(old_snapshot.to_dict())
                transaction.set(
                    old_ref, encode(old.model_copy(update={"state": PolicyState.SUPERSEDED}))
                )
            transaction.set(version_ref, encode(active))
            transaction.set(
                policy_ref,
                encode(
                    policy.model_copy(
                        update={
                            "active_version_id": active.id,
                            "updated_at": now,
                            "revision": policy.revision + 1,
                        }
                    )
                ),
            )
            return active

        return await apply(self._client.transaction(max_attempts=5))
