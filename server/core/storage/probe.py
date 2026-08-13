from collections.abc import Callable
from datetime import datetime

from contracts import Probe, ProbeState, ProbeVersion
from google.cloud.firestore_v1 import AsyncClient
from google.cloud.firestore_v1.async_transaction import AsyncTransaction, async_transactional
from policy import digest

from core.errors import ResourceConflictError, ResourceNotFoundError, StorageIntegrityError
from core.storage.codec import encode
from core.storage.paths import FirestorePaths


class FirestoreProbeRepository:
    def __init__(self, client: AsyncClient) -> None:
        self._client = client

    async def create(self, probe: Probe) -> Probe:
        reference = self._client.document(FirestorePaths.probe(probe.organisation_id, probe.id))
        try:
            await reference.create(encode(probe))
        except Exception as error:
            from google.api_core.exceptions import AlreadyExists

            if isinstance(error, AlreadyExists):
                raise ResourceConflictError(f"probe {probe.id} already exists") from error
            raise
        return probe

    async def create_version(
        self,
        organisation_id: str,
        probe_id: str,
        factory: Callable[[int], ProbeVersion],
    ) -> ProbeVersion:
        probe_ref = self._client.document(FirestorePaths.probe(organisation_id, probe_id))

        @async_transactional
        async def apply(transaction: AsyncTransaction) -> ProbeVersion:
            snapshot = await probe_ref.get(transaction=transaction)
            if not snapshot.exists or snapshot.to_dict() is None:
                raise ResourceNotFoundError(f"probe {probe_id} was not found")
            probe = Probe.model_validate(snapshot.to_dict())
            version = factory(probe.latest_version + 1)
            if (
                version.probe_id != probe.id
                or version.number != probe.latest_version + 1
                or version.state is not ProbeState.DRAFT
                or version.digest != digest(version.definition)
            ):
                raise StorageIntegrityError("probe version factory changed immutable inputs")
            version_ref = self._client.document(
                FirestorePaths.probe_version(organisation_id, version.id)
            )
            if (await version_ref.get(transaction=transaction)).exists:
                raise ResourceConflictError(f"probe version {version.id} already exists")
            transaction.create(version_ref, encode(version))
            transaction.set(
                probe_ref,
                encode(
                    probe.model_copy(
                        update={
                            "latest_version": version.number,
                            "updated_at": version.created_at,
                            "revision": probe.revision + 1,
                        }
                    )
                ),
            )
            return version

        return await apply(self._client.transaction(max_attempts=5))

    async def activate(
        self,
        organisation_id: str,
        probe_id: str,
        version_id: str,
        actor_id: str,
        now: datetime,
    ) -> ProbeVersion:
        probe_ref = self._client.document(FirestorePaths.probe(organisation_id, probe_id))
        version_ref = self._client.document(
            FirestorePaths.probe_version(organisation_id, version_id)
        )

        @async_transactional
        async def apply(transaction: AsyncTransaction) -> ProbeVersion:
            probe_snapshot = await probe_ref.get(transaction=transaction)
            version_snapshot = await version_ref.get(transaction=transaction)
            if (
                not probe_snapshot.exists
                or not version_snapshot.exists
                or probe_snapshot.to_dict() is None
                or version_snapshot.to_dict() is None
            ):
                raise ResourceNotFoundError("probe or probe version was not found")
            probe = Probe.model_validate(probe_snapshot.to_dict())
            version = ProbeVersion.model_validate(version_snapshot.to_dict())
            if version.probe_id != probe.id or version.state is not ProbeState.DRAFT:
                raise ResourceConflictError("only a draft version of this probe can activate")
            active = version.model_copy(
                update={
                    "state": ProbeState.ACTIVE,
                    "approved_by": actor_id,
                    "approved_at": now,
                }
            )
            if probe.active_version_id is not None:
                old_ref = self._client.document(
                    FirestorePaths.probe_version(organisation_id, probe.active_version_id)
                )
                old_snapshot = await old_ref.get(transaction=transaction)
                if not old_snapshot.exists or old_snapshot.to_dict() is None:
                    raise StorageIntegrityError("active probe version disappeared")
                old = ProbeVersion.model_validate(old_snapshot.to_dict())
                transaction.set(
                    old_ref, encode(old.model_copy(update={"state": ProbeState.SUPERSEDED}))
                )
            transaction.set(version_ref, encode(active))
            transaction.set(
                probe_ref,
                encode(
                    probe.model_copy(
                        update={
                            "active_version_id": active.id,
                            "updated_at": now,
                            "revision": probe.revision + 1,
                        }
                    )
                ),
            )
            return active

        return await apply(self._client.transaction(max_attempts=5))
