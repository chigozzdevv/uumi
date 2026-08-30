from datetime import datetime
from typing import Any

from contracts import (
    GoogleCloudOnboardingSession,
    GoogleCloudOnboardingStatus,
    GoogleCloudProject,
)
from google.cloud.firestore_v1 import AsyncClient
from google.cloud.firestore_v1.async_transaction import AsyncTransaction, async_transactional

from core.errors import ResourceConflictError, ResourceNotFoundError
from core.storage.codec import encode
from core.storage.paths import FirestorePaths


class FirestoreGoogleCloudRepository:
    def __init__(self, client: AsyncClient) -> None:
        self._client = client

    async def create_session(
        self, session: GoogleCloudOnboardingSession
    ) -> GoogleCloudOnboardingSession:
        await self._client.document(
            FirestorePaths.google_cloud_onboarding(session.organisation_id, session.id)
        ).create(encode(session))
        return session

    async def get_session(
        self, organisation_id: str, session_id: str
    ) -> GoogleCloudOnboardingSession:
        snapshot = await self._client.document(
            FirestorePaths.google_cloud_onboarding(organisation_id, session_id)
        ).get()
        if not snapshot.exists:
            raise ResourceNotFoundError(f"Google Cloud onboarding {session_id} was not found")
        return GoogleCloudOnboardingSession.model_validate(_data(snapshot))

    async def complete_session(
        self,
        session: GoogleCloudOnboardingSession,
        projects: tuple[GoogleCloudProject, ...],
        completed_at: datetime,
        authorization_ciphertext: str,
        authorization_expires_at: datetime,
    ) -> GoogleCloudOnboardingSession:
        reference = self._client.document(
            FirestorePaths.google_cloud_onboarding(session.organisation_id, session.id)
        )

        @async_transactional
        async def finish(transaction: AsyncTransaction) -> GoogleCloudOnboardingSession:
            snapshot = await reference.get(transaction=transaction)
            if not snapshot.exists:
                raise ResourceNotFoundError(f"Google Cloud onboarding {session.id} was not found")
            current = GoogleCloudOnboardingSession.model_validate(_data(snapshot))
            if current != session:
                raise ResourceConflictError("Google Cloud onboarding changed before completion")
            completed = current.model_copy(
                update={
                    "status": GoogleCloudOnboardingStatus.COMPLETE,
                    "projects": projects,
                    "completed_at": completed_at,
                    "authorization_ciphertext": authorization_ciphertext,
                    "authorization_expires_at": authorization_expires_at,
                }
            )
            transaction.set(reference, encode(completed))
            return completed

        return await finish(self._client.transaction(max_attempts=5))

    async def attach_connection(
        self,
        session: GoogleCloudOnboardingSession,
        connection_id: str,
    ) -> GoogleCloudOnboardingSession:
        reference = self._client.document(
            FirestorePaths.google_cloud_onboarding(session.organisation_id, session.id)
        )

        @async_transactional
        async def attach(transaction: AsyncTransaction) -> GoogleCloudOnboardingSession:
            snapshot = await reference.get(transaction=transaction)
            if not snapshot.exists:
                raise ResourceNotFoundError(f"Google Cloud onboarding {session.id} was not found")
            current = GoogleCloudOnboardingSession.model_validate(_data(snapshot))
            if current != session:
                raise ResourceConflictError("Google Cloud onboarding changed before connection")
            if current.connection_id is not None:
                raise ResourceConflictError("Google Cloud onboarding already has a connection")
            changed = current.model_copy(update={"connection_id": connection_id})
            transaction.set(reference, encode(changed))
            return changed

        return await attach(self._client.transaction(max_attempts=5))

    async def authorize_session(
        self,
        session: GoogleCloudOnboardingSession,
        authorized_at: datetime,
    ) -> GoogleCloudOnboardingSession:
        reference = self._client.document(
            FirestorePaths.google_cloud_onboarding(session.organisation_id, session.id)
        )

        @async_transactional
        async def authorize(transaction: AsyncTransaction) -> GoogleCloudOnboardingSession:
            snapshot = await reference.get(transaction=transaction)
            if not snapshot.exists:
                raise ResourceNotFoundError(f"Google Cloud onboarding {session.id} was not found")
            current = GoogleCloudOnboardingSession.model_validate(_data(snapshot))
            if current != session:
                if current.authorized_at is not None:
                    return current
                raise ResourceConflictError("Google Cloud onboarding changed before authorization")
            changed = current.model_copy(
                update={
                    "authorization_ciphertext": None,
                    "authorization_expires_at": None,
                    "authorized_at": authorized_at,
                }
            )
            transaction.set(reference, encode(changed))
            return changed

        return await authorize(self._client.transaction(max_attempts=5))


def _data(snapshot: Any) -> dict[str, Any]:
    value = snapshot.to_dict()
    if not isinstance(value, dict):
        raise ResourceNotFoundError("Google Cloud onboarding data is unavailable")
    return value
