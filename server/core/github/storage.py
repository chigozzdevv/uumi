from datetime import datetime
from typing import Any

from contracts import (
    GitHubInstallation,
    GitHubInstallationIndex,
    GitHubOnboardingSession,
    GitHubOnboardingStatus,
    GitHubRepository,
    GitHubRepositoryCandidate,
    GitHubWebhookReceipt,
)
from google.cloud.firestore_v1 import AsyncClient
from google.cloud.firestore_v1.async_transaction import AsyncTransaction, async_transactional

from core.errors import ResourceConflictError, ResourceNotFoundError, StorageIntegrityError
from core.storage.codec import encode
from core.storage.paths import FirestorePaths


class FirestoreGitHubRepository:
    def __init__(self, client: AsyncClient) -> None:
        self._client = client

    async def create_session(self, session: GitHubOnboardingSession) -> GitHubOnboardingSession:
        await self._client.document(
            FirestorePaths.github_onboarding(session.organisation_id, session.id)
        ).create(encode(session))
        return session

    async def get_session(self, organisation_id: str, session_id: str) -> GitHubOnboardingSession:
        snapshot = await self._client.document(
            FirestorePaths.github_onboarding(organisation_id, session_id)
        ).get()
        if not snapshot.exists:
            raise ResourceNotFoundError(f"GitHub onboarding {session_id} was not found")
        return GitHubOnboardingSession.model_validate(_data(snapshot))

    async def receipt(self, installation_id: int) -> GitHubWebhookReceipt | None:
        snapshot = await self._client.document(
            FirestorePaths.github_webhook_receipt(installation_id)
        ).get()
        return GitHubWebhookReceipt.model_validate(_data(snapshot)) if snapshot.exists else None

    async def stage(
        self,
        session: GitHubOnboardingSession,
        installation: GitHubInstallation,
        repositories: tuple[GitHubRepositoryCandidate, ...],
    ) -> GitHubOnboardingSession:
        session_ref = self._client.document(
            FirestorePaths.github_onboarding(session.organisation_id, session.id)
        )
        index_ref = self._client.document(
            FirestorePaths.github_installation_index(installation.installation_id)
        )

        @async_transactional
        async def apply(transaction: AsyncTransaction) -> GitHubOnboardingSession:
            session_snapshot = await session_ref.get(transaction=transaction)
            index_snapshot = await index_ref.get(transaction=transaction)
            if not session_snapshot.exists:
                raise StorageIntegrityError("GitHub onboarding disappeared before discovery")
            current = GitHubOnboardingSession.model_validate(_data(session_snapshot))
            if current.status in {
                GitHubOnboardingStatus.DISCOVERED,
                GitHubOnboardingStatus.COMPLETE,
            }:
                return current
            if current != session:
                raise ResourceConflictError("GitHub onboarding changed before discovery")
            if index_snapshot.exists:
                index = GitHubInstallationIndex.model_validate(_data(index_snapshot))
                if index.organisation_id != session.organisation_id:
                    raise ResourceConflictError(
                        "GitHub installation is already connected to another organisation"
                    )
                if index.deleted:
                    raise ResourceConflictError("GitHub installation has been deleted")
                if index.onboarding_id != session.id:
                    raise ResourceConflictError(
                        "GitHub installation is already connected to this organisation"
                    )
            else:
                index = GitHubInstallationIndex(
                    installation_id=installation.installation_id,
                    organisation_id=session.organisation_id,
                    onboarding_id=session.id,
                    created_at=installation.created_at,
                )
                transaction.create(index_ref, encode(index))
            staged = current.model_copy(
                update={
                    "status": GitHubOnboardingStatus.DISCOVERED,
                    "installation_id": installation.installation_id,
                    "installation": installation,
                    "repositories": repositories,
                }
            )
            transaction.set(session_ref, encode(staged))
            return staged

        return await apply(self._client.transaction(max_attempts=5))

    async def complete(
        self,
        session: GitHubOnboardingSession,
        installation: GitHubInstallation,
        repositories: tuple[GitHubRepository, ...],
    ) -> GitHubOnboardingSession:
        index_ref = self._client.document(
            FirestorePaths.github_installation_index(installation.installation_id)
        )

        @async_transactional
        async def reserve(transaction: AsyncTransaction) -> GitHubInstallationIndex:
            snapshot = await index_ref.get(transaction=transaction)
            if snapshot.exists:
                current = GitHubInstallationIndex.model_validate(_data(snapshot))
                if current.organisation_id != session.organisation_id:
                    raise ResourceConflictError(
                        "GitHub installation is already connected to another organisation"
                    )
                if current.deleted:
                    raise ResourceConflictError("GitHub installation has been deleted")
                if current.onboarding_id != session.id:
                    if current.ready:
                        raise ResourceConflictError(
                            "GitHub installation is already connected to this organisation"
                        )
                    current = GitHubInstallationIndex(
                        installation_id=installation.installation_id,
                        organisation_id=session.organisation_id,
                        onboarding_id=session.id,
                        created_at=installation.created_at,
                    )
                    transaction.set(index_ref, encode(current))
                return current
            index = GitHubInstallationIndex(
                installation_id=installation.installation_id,
                organisation_id=session.organisation_id,
                onboarding_id=session.id,
                created_at=installation.created_at,
            )
            transaction.create(index_ref, encode(index))
            return index

        index = await reserve(self._client.transaction(max_attempts=5))
        existing = await self.repositories(session.organisation_id, installation.installation_id)
        selected_ids = {item.repository_id for item in repositories}
        for offset in range(0, len(repositories), 400):
            batch = self._client.batch()
            for repository in repositories[offset : offset + 400]:
                batch.set(
                    self._client.document(
                        FirestorePaths.github_repository(
                            repository.organisation_id, repository.repository_id
                        )
                    ),
                    encode(repository),
                )
            await batch.commit()
        stale = tuple(item for item in existing if item.repository_id not in selected_ids)
        for offset in range(0, len(stale), 400):
            batch = self._client.batch()
            for repository in stale[offset : offset + 400]:
                batch.delete(
                    self._client.document(
                        FirestorePaths.github_repository(
                            repository.organisation_id, repository.repository_id
                        )
                    )
                )
            await batch.commit()
        session_ref = self._client.document(
            FirestorePaths.github_onboarding(session.organisation_id, session.id)
        )
        installation_ref = self._client.document(
            FirestorePaths.github_installation(
                installation.organisation_id, installation.installation_id
            )
        )

        @async_transactional
        async def finish(transaction: AsyncTransaction) -> GitHubOnboardingSession:
            session_snapshot = await session_ref.get(transaction=transaction)
            index_snapshot = await index_ref.get(transaction=transaction)
            if not session_snapshot.exists or not index_snapshot.exists:
                raise StorageIntegrityError("GitHub onboarding reservation disappeared")
            current = GitHubOnboardingSession.model_validate(_data(session_snapshot))
            current_index = GitHubInstallationIndex.model_validate(_data(index_snapshot))
            if current.status is GitHubOnboardingStatus.COMPLETE:
                return current
            if current != session or current_index != index:
                raise ResourceConflictError("GitHub onboarding changed before completion")
            completed = current.model_copy(
                update={
                    "status": GitHubOnboardingStatus.COMPLETE,
                    "installation_id": installation.installation_id,
                    "completed_at": installation.updated_at,
                }
            )
            transaction.set(installation_ref, encode(installation))
            transaction.set(session_ref, encode(completed))
            transaction.set(
                index_ref,
                encode(index.model_copy(update={"ready": installation.ready})),
            )
            return completed

        return await finish(self._client.transaction(max_attempts=5))

    async def installation(self, organisation_id: str, installation_id: int) -> GitHubInstallation:
        snapshot = await self._client.document(
            FirestorePaths.github_installation(organisation_id, installation_id)
        ).get()
        if not snapshot.exists:
            raise ResourceNotFoundError(f"GitHub installation {installation_id} was not found")
        return GitHubInstallation.model_validate(_data(snapshot))

    async def repositories(
        self, organisation_id: str, installation_id: int
    ) -> tuple[GitHubRepository, ...]:
        values: list[GitHubRepository] = []
        query = self._client.collection(
            f"organisations/{organisation_id}/github-repositories"
        ).where("installation_id", "==", installation_id)
        async for snapshot in query.stream():
            values.append(GitHubRepository.model_validate(_data(snapshot)))
        return tuple(sorted(values, key=lambda value: value.full_name))

    async def record_receipt(self, receipt: GitHubWebhookReceipt) -> None:
        reference = self._client.document(
            FirestorePaths.github_webhook_receipt(receipt.installation_id)
        )
        index_ref = self._client.document(
            FirestorePaths.github_installation_index(receipt.installation_id)
        )

        @async_transactional
        async def apply(transaction: AsyncTransaction) -> None:
            index_snapshot = await index_ref.get(transaction=transaction)
            if not index_snapshot.exists:
                transaction.set(reference, encode(receipt))
                return
            index = GitHubInstallationIndex.model_validate(_data(index_snapshot))
            installation_ref = self._client.document(
                FirestorePaths.github_installation(index.organisation_id, receipt.installation_id)
            )
            installation_snapshot = await installation_ref.get(transaction=transaction)
            if not installation_snapshot.exists:
                transaction.set(reference, encode(receipt))
                return
            installation = GitHubInstallation.model_validate(_data(installation_snapshot))
            active = index.active or (receipt.action == "unsuspend" and not index.deleted)
            transaction.set(reference, encode(receipt))
            transaction.set(
                installation_ref,
                encode(
                    installation.model_copy(
                        update={
                            "webhook_verified_at": receipt.received_at,
                            "active": active,
                            "ready": active and installation.repositories_ready,
                            "updated_at": receipt.received_at,
                        }
                    )
                ),
            )
            transaction.set(
                index_ref,
                encode(
                    index.model_copy(
                        update={
                            "active": active,
                            "ready": active and installation.repositories_ready,
                        }
                    )
                ),
            )

        await apply(self._client.transaction(max_attempts=5))

    async def deactivate(
        self, installation_id: int, occurred_at: datetime, deleted: bool = False
    ) -> None:
        index_ref = self._client.document(FirestorePaths.github_installation_index(installation_id))

        @async_transactional
        async def apply(transaction: AsyncTransaction) -> None:
            index_snapshot = await index_ref.get(transaction=transaction)
            if not index_snapshot.exists:
                return
            index = GitHubInstallationIndex.model_validate(_data(index_snapshot))
            installation_ref = self._client.document(
                FirestorePaths.github_installation(index.organisation_id, installation_id)
            )
            installation_snapshot = await installation_ref.get(transaction=transaction)
            if installation_snapshot.exists:
                installation = GitHubInstallation.model_validate(_data(installation_snapshot))
                transaction.set(
                    installation_ref,
                    encode(
                        installation.model_copy(
                            update={
                                "active": False,
                                "deleted": installation.deleted or deleted,
                                "ready": False,
                                "updated_at": occurred_at,
                            }
                        )
                    ),
                )
            transaction.set(
                index_ref,
                encode(
                    index.model_copy(
                        update={
                            "active": False,
                            "deleted": index.deleted or deleted,
                            "ready": False,
                        }
                    )
                ),
            )

        await apply(self._client.transaction(max_attempts=5))

    async def invalidate_repositories(self, installation_id: int, occurred_at: datetime) -> None:
        index_ref = self._client.document(FirestorePaths.github_installation_index(installation_id))

        @async_transactional
        async def apply(transaction: AsyncTransaction) -> None:
            index_snapshot = await index_ref.get(transaction=transaction)
            if not index_snapshot.exists:
                return
            index = GitHubInstallationIndex.model_validate(_data(index_snapshot))
            installation_ref = self._client.document(
                FirestorePaths.github_installation(index.organisation_id, installation_id)
            )
            installation_snapshot = await installation_ref.get(transaction=transaction)
            if installation_snapshot.exists:
                installation = GitHubInstallation.model_validate(_data(installation_snapshot))
                transaction.set(
                    installation_ref,
                    encode(
                        installation.model_copy(
                            update={
                                "repositories_ready": False,
                                "ready": False,
                                "updated_at": occurred_at,
                            }
                        )
                    ),
                )
            transaction.set(index_ref, encode(index.model_copy(update={"ready": False})))

        await apply(self._client.transaction(max_attempts=5))

    async def route(
        self, installation_id: int, repository_id: int
    ) -> tuple[str, GitHubRepository] | None:
        index_snapshot = await self._client.document(
            FirestorePaths.github_installation_index(installation_id)
        ).get()
        if not index_snapshot.exists:
            raise ResourceNotFoundError(f"GitHub installation {installation_id} is not connected")
        index = GitHubInstallationIndex.model_validate(_data(index_snapshot))
        if not index.active:
            return None
        if not index.ready:
            raise ResourceConflictError("GitHub installation onboarding is incomplete")
        repository_snapshot = await self._client.document(
            FirestorePaths.github_repository(index.organisation_id, repository_id)
        ).get()
        if not repository_snapshot.exists:
            raise ResourceNotFoundError(f"GitHub repository {repository_id} is not connected")
        repository = GitHubRepository.model_validate(_data(repository_snapshot))
        if repository.installation_id != installation_id:
            raise StorageIntegrityError("GitHub repository installation mapping is invalid")
        return index.organisation_id, repository


def _data(snapshot: Any) -> dict[str, Any]:
    value: dict[str, Any] | None = snapshot.to_dict()
    if value is None:
        raise StorageIntegrityError(f"GitHub document {snapshot.id} has no data")
    return value
