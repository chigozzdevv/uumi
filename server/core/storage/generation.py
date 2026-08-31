from datetime import datetime
from typing import Any

from contracts import (
    ConsumerBinding,
    CredentialGeneration,
    GenerationState,
    ManagedCredential,
    VerificationReport,
    VerificationStatus,
)
from google.cloud.firestore_v1 import AsyncClient
from google.cloud.firestore_v1.async_transaction import AsyncTransaction, async_transactional
from google.cloud.firestore_v1.base_document import DocumentSnapshot

from core.errors import ResourceConflictError, ResourceNotFoundError, StorageIntegrityError
from core.storage.codec import encode
from core.storage.paths import FirestorePaths


class FirestoreGenerationRepository:
    def __init__(self, client: AsyncClient) -> None:
        self._client = client

    async def create(self, generation: CredentialGeneration) -> CredentialGeneration:
        reference = self._client.document(
            FirestorePaths.generation(generation.organisation_id, generation.id)
        )

        @async_transactional
        async def apply(transaction: AsyncTransaction) -> CredentialGeneration:
            existing = await reference.get(transaction=transaction)
            if existing.exists:
                current = CredentialGeneration.model_validate(_data(existing))
                if current == generation:
                    return current
                raise ResourceConflictError(f"generation {generation.id} already exists")
            transaction.create(reference, encode(generation))
            return generation

        return await apply(self._client.transaction(max_attempts=5))

    async def promote(
        self,
        organisation_id: str,
        credential_id: str,
        target_id: str,
        predecessor_id: str,
        report_id: str,
        binding_ids: tuple[str, ...],
    ) -> CredentialGeneration:
        credential_ref = self._client.document(
            FirestorePaths.credential(organisation_id, credential_id)
        )
        target_ref = self._client.document(FirestorePaths.generation(organisation_id, target_id))
        predecessor_ref = self._client.document(
            FirestorePaths.generation(organisation_id, predecessor_id)
        )
        report_ref = self._client.document(FirestorePaths.report(organisation_id, report_id))
        binding_refs = tuple(
            self._client.document(FirestorePaths.binding(organisation_id, binding_id))
            for binding_id in binding_ids
        )

        @async_transactional
        async def apply(transaction: AsyncTransaction) -> CredentialGeneration:
            credential_snapshot = await credential_ref.get(transaction=transaction)
            target_snapshot = await target_ref.get(transaction=transaction)
            predecessor_snapshot = await predecessor_ref.get(transaction=transaction)
            report_snapshot = await report_ref.get(transaction=transaction)
            binding_snapshots = [
                await reference.get(transaction=transaction) for reference in binding_refs
            ]
            if not all(
                snapshot.exists
                for snapshot in (
                    credential_snapshot,
                    target_snapshot,
                    predecessor_snapshot,
                    report_snapshot,
                    *binding_snapshots,
                )
            ):
                raise ResourceNotFoundError("generation promotion inputs are incomplete")
            credential = ManagedCredential.model_validate(_data(credential_snapshot))
            target = CredentialGeneration.model_validate(_data(target_snapshot))
            predecessor = CredentialGeneration.model_validate(_data(predecessor_snapshot))
            report = VerificationReport.model_validate(_data(report_snapshot))
            bindings = tuple(
                ConsumerBinding.model_validate(_data(snapshot)) for snapshot in binding_snapshots
            )
            already_active = (
                credential.active_generation_id == target_id
                and target.state is GenerationState.ACTIVE
            )
            if already_active:
                return target
            if credential.active_generation_id != predecessor_id:
                raise ResourceConflictError("credential predecessor changed before promotion")
            if target.state is not GenerationState.CREATING:
                raise ResourceConflictError("target generation is not awaiting promotion")
            if predecessor.state is not GenerationState.ACTIVE:
                raise ResourceConflictError("predecessor generation is not active")
            if (
                target.credential_id != credential.id
                or predecessor.credential_id != credential.id
                or report.generation_id != target.id
                or report.status is not VerificationStatus.PASSED
            ):
                raise ResourceConflictError("generation lineage or verification is inconsistent")
            required_services = set(credential.consumer_ids)
            if {binding.service_id for binding in bindings} != required_services:
                raise ResourceConflictError(
                    "not every credential consumer is included in promotion"
                )
            for binding in bindings:
                if (
                    binding.credential_id != credential.id
                    or binding.current_generation_id != predecessor.id
                    or binding.target_generation_id != target.id
                    or binding.verification_report_id != report.id
                ):
                    raise ResourceConflictError("consumer binding has not verified the target")

            active = target.model_copy(
                update={
                    "state": GenerationState.ACTIVE,
                    "predecessor_id": predecessor.id,
                }
            )
            superseded = predecessor.model_copy(
                update={
                    "state": GenerationState.SUPERSEDED,
                    "successor_id": target.id,
                }
            )
            changed_credential = credential.model_copy(
                update={
                    "active_generation_id": target.id,
                    "provider_id": target.provider_id,
                    "provider_display_name": target.provider_display_name,
                    "secret_reference": target.secret_reference,
                    "scopes": target.scopes,
                    "expires_at": target.expires_at,
                    "last_observed_at": target.last_observed_at,
                    "metadata_digest": target.metadata_digest,
                    "updated_at": report.completed_at,
                    "revision": credential.revision + 1,
                }
            )
            transaction.set(target_ref, encode(active))
            transaction.set(predecessor_ref, encode(superseded))
            transaction.set(credential_ref, encode(changed_credential))
            for reference, binding in zip(binding_refs, bindings, strict=True):
                transaction.set(
                    reference,
                    encode(
                        binding.model_copy(
                            update={
                                "current_generation_id": target.id,
                                "target_generation_id": None,
                                "revision": binding.revision + 1,
                            }
                        )
                    ),
                )
            return active

        return await apply(self._client.transaction(max_attempts=5))

    async def revoke(
        self,
        organisation_id: str,
        generation_id: str,
        verification_report_id: str,
        revoked_at: datetime,
    ) -> CredentialGeneration:
        reference = self._client.document(FirestorePaths.generation(organisation_id, generation_id))
        report_ref = self._client.document(
            FirestorePaths.report(organisation_id, verification_report_id)
        )

        @async_transactional
        async def apply(transaction: AsyncTransaction) -> CredentialGeneration:
            snapshot = await reference.get(transaction=transaction)
            report_snapshot = await report_ref.get(transaction=transaction)
            if not snapshot.exists or not report_snapshot.exists:
                raise ResourceNotFoundError("generation revocation evidence is incomplete")
            generation = CredentialGeneration.model_validate(_data(snapshot))
            report = VerificationReport.model_validate(_data(report_snapshot))
            if generation.state is GenerationState.REVOKED:
                return generation
            if generation.state is not GenerationState.SUPERSEDED:
                raise ResourceConflictError("only a superseded generation can be revoked")
            if (
                report.status is not VerificationStatus.PASSED
                or report.organisation_id != organisation_id
                or report.generation_id != generation.id
            ):
                raise ResourceConflictError("revocation verification did not pass")
            changed = generation.model_copy(
                update={"state": GenerationState.REVOKED, "revoked_at": revoked_at}
            )
            transaction.set(reference, encode(changed))
            return changed

        return await apply(self._client.transaction(max_attempts=5))

    async def stage_bindings(
        self,
        organisation_id: str,
        credential_id: str,
        target_id: str,
        secret_reference: str,
        binding_ids: tuple[str, ...],
    ) -> tuple[str, ...]:
        references = tuple(
            self._client.document(FirestorePaths.binding(organisation_id, binding_id))
            for binding_id in binding_ids
        )

        @async_transactional
        async def apply(transaction: AsyncTransaction) -> tuple[str, ...]:
            snapshots = [await reference.get(transaction=transaction) for reference in references]
            if not all(snapshot.exists for snapshot in snapshots):
                raise ResourceNotFoundError("consumer deployment bindings are incomplete")
            bindings = tuple(ConsumerBinding.model_validate(_data(item)) for item in snapshots)
            if any(binding.credential_id != credential_id for binding in bindings):
                raise ResourceConflictError("consumer binding belongs to another credential")
            for reference, binding in zip(references, bindings, strict=True):
                if binding.target_generation_id not in {None, target_id}:
                    raise ResourceConflictError("consumer binding targets another generation")
                changed = binding.model_copy(
                    update={
                        "target_generation_id": target_id,
                        "secret_reference": secret_reference,
                        "revision": binding.revision + 1,
                    }
                )
                transaction.set(reference, encode(changed))
            return tuple(binding.id for binding in bindings)

        return await apply(self._client.transaction(max_attempts=5))

    async def verify_bindings(
        self,
        organisation_id: str,
        target_id: str,
        report_id: str,
        binding_ids: tuple[str, ...],
    ) -> tuple[str, ...]:
        report_ref = self._client.document(FirestorePaths.report(organisation_id, report_id))
        references = tuple(
            self._client.document(FirestorePaths.binding(organisation_id, binding_id))
            for binding_id in binding_ids
        )

        @async_transactional
        async def apply(transaction: AsyncTransaction) -> tuple[str, ...]:
            report_snapshot = await report_ref.get(transaction=transaction)
            snapshots = [await reference.get(transaction=transaction) for reference in references]
            if not report_snapshot.exists or not all(snapshot.exists for snapshot in snapshots):
                raise ResourceNotFoundError("consumer verification evidence is incomplete")
            report = VerificationReport.model_validate(_data(report_snapshot))
            if report.status is not VerificationStatus.PASSED or report.generation_id != target_id:
                raise ResourceConflictError("consumer verification report did not pass")
            bindings = tuple(ConsumerBinding.model_validate(_data(item)) for item in snapshots)
            for reference, binding in zip(references, bindings, strict=True):
                if binding.target_generation_id != target_id:
                    raise ResourceConflictError("consumer binding does not target the generation")
                transaction.set(
                    reference,
                    encode(
                        binding.model_copy(
                            update={
                                "verification_report_id": report_id,
                                "revision": binding.revision + 1,
                            }
                        )
                    ),
                )
            return tuple(binding.id for binding in bindings)

        return await apply(self._client.transaction(max_attempts=5))

    async def orphan(
        self,
        organisation_id: str,
        generation_id: str,
    ) -> CredentialGeneration:
        reference = self._client.document(FirestorePaths.generation(organisation_id, generation_id))

        @async_transactional
        async def apply(transaction: AsyncTransaction) -> CredentialGeneration:
            snapshot = await reference.get(transaction=transaction)
            if not snapshot.exists:
                raise ResourceNotFoundError(f"generation {generation_id} was not found")
            generation = CredentialGeneration.model_validate(_data(snapshot))
            if generation.state not in {GenerationState.CREATING, GenerationState.UNKNOWN}:
                raise ResourceConflictError("only unactivated generations can be orphaned")
            changed = generation.model_copy(update={"state": GenerationState.ORPHANED})
            transaction.set(reference, encode(changed))
            return changed

        return await apply(self._client.transaction(max_attempts=5))


def _data(snapshot: DocumentSnapshot) -> dict[str, Any]:
    value = snapshot.to_dict()
    if value is None:
        raise StorageIntegrityError(f"document {snapshot.id} has no data")
    return value
