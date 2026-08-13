from datetime import datetime
from typing import Any

from contracts import (
    BrowserAction,
    BrowserActionRecord,
    BrowserActionStatus,
    BrowserSession,
    ReplayCheckpoint,
    SecureCaptureResult,
)
from core.errors import ResourceConflictError, ResourceNotFoundError, StorageIntegrityError
from core.storage.codec import encode
from core.storage.paths import FirestorePaths
from google.cloud.firestore_v1 import AsyncClient
from google.cloud.firestore_v1.async_transaction import AsyncTransaction, async_transactional
from google.cloud.firestore_v1.base_document import DocumentSnapshot


class FirestoreBrowserRepository:
    def __init__(self, client: AsyncClient) -> None:
        self._client = client

    async def create(self, session: BrowserSession) -> BrowserSession:
        reference = self._client.document(
            FirestorePaths.browser(session.organisation_id, session.id)
        )

        @async_transactional
        async def apply(transaction: AsyncTransaction) -> BrowserSession:
            snapshot = await reference.get(transaction=transaction)
            if snapshot.exists:
                current = BrowserSession.model_validate(_data(snapshot))
                if current == session:
                    return current
                raise ResourceConflictError(f"browser session {session.id} already exists")
            transaction.create(reference, encode(session))
            return session

        return await apply(self._client.transaction(max_attempts=5))

    async def get(self, organisation_id: str, session_id: str) -> BrowserSession:
        snapshot = await self._client.document(
            FirestorePaths.browser(organisation_id, session_id)
        ).get()
        if not snapshot.exists:
            raise ResourceNotFoundError(f"browser session {session_id} was not found")
        return BrowserSession.model_validate(_data(snapshot))

    async def update(
        self,
        organisation_id: str,
        session_id: str,
        expected_revision: int,
        changed: BrowserSession,
    ) -> BrowserSession:
        reference = self._client.document(FirestorePaths.browser(organisation_id, session_id))

        @async_transactional
        async def apply(transaction: AsyncTransaction) -> BrowserSession:
            snapshot = await reference.get(transaction=transaction)
            if not snapshot.exists:
                raise ResourceNotFoundError(f"browser session {session_id} was not found")
            current = BrowserSession.model_validate(_data(snapshot))
            if current.revision != expected_revision:
                raise ResourceConflictError(
                    f"browser expected revision {expected_revision}, found {current.revision}"
                )
            if changed.revision != expected_revision + 1:
                raise StorageIntegrityError("browser update did not advance revision once")
            if changed.id != current.id or changed.organisation_id != current.organisation_id:
                raise StorageIntegrityError("browser update changed session identity")
            transaction.set(reference, encode(changed))
            return changed

        return await apply(self._client.transaction(max_attempts=5))

    async def save_capture(self, result: SecureCaptureResult) -> SecureCaptureResult:
        path = FirestorePaths.capture(result.organisation_id, result.session_id, result.id)
        return await self._create_immutable(path, result, SecureCaptureResult)

    async def save_checkpoint(self, checkpoint: ReplayCheckpoint) -> ReplayCheckpoint:
        path = FirestorePaths.replay(
            checkpoint.organisation_id, checkpoint.session_id, checkpoint.id
        )
        return await self._create_immutable(path, checkpoint, ReplayCheckpoint)

    async def begin_action(
        self,
        current: BrowserSession,
        changed: BrowserSession,
        action: BrowserAction,
        authorised_at: datetime,
    ) -> BrowserSession:
        session_ref = self._client.document(
            FirestorePaths.browser(current.organisation_id, current.id)
        )
        action_ref = self._client.document(
            FirestorePaths.browser_action(current.organisation_id, current.id, action.id)
        )
        record = BrowserActionRecord(
            id=action.id,
            organisation_id=current.organisation_id,
            session_id=current.id,
            action=action,
            status=BrowserActionStatus.AUTHORIZED,
            authorised_at=authorised_at,
        )

        @async_transactional
        async def apply(transaction: AsyncTransaction) -> BrowserSession:
            session_snapshot = await session_ref.get(transaction=transaction)
            action_snapshot = await action_ref.get(transaction=transaction)
            if not session_snapshot.exists:
                raise ResourceNotFoundError(f"browser session {current.id} was not found")
            stored = BrowserSession.model_validate(_data(session_snapshot))
            if stored != current:
                raise ResourceConflictError("browser changed before action authorisation")
            if action_snapshot.exists:
                previous = BrowserActionRecord.model_validate(_data(action_snapshot))
                if previous == record:
                    raise ResourceConflictError(
                        "browser action was authorised but has no execution outcome"
                    )
                raise ResourceConflictError(f"browser action {action.id} already exists")
            transaction.create(action_ref, encode(record))
            transaction.set(session_ref, encode(changed))
            return changed

        return await apply(self._client.transaction(max_attempts=5))

    async def finish_action(
        self,
        organisation_id: str,
        session_id: str,
        action_id: str,
        status: BrowserActionStatus,
        error: str | None,
        completed_at: datetime,
    ) -> BrowserActionRecord:
        reference = self._client.document(
            FirestorePaths.browser_action(organisation_id, session_id, action_id)
        )

        @async_transactional
        async def apply(transaction: AsyncTransaction) -> BrowserActionRecord:
            snapshot = await reference.get(transaction=transaction)
            if not snapshot.exists:
                raise ResourceNotFoundError(f"browser action {action_id} was not found")
            current = BrowserActionRecord.model_validate(_data(snapshot))
            if current.status is not BrowserActionStatus.AUTHORIZED:
                if current.status is status and current.error == error:
                    return current
                raise ResourceConflictError(f"browser action {action_id} already completed")
            changed = current.model_copy(
                update={"status": status, "error": error, "completed_at": completed_at}
            )
            transaction.set(reference, encode(changed))
            return changed

        return await apply(self._client.transaction(max_attempts=5))

    async def _create_immutable[T: SecureCaptureResult | ReplayCheckpoint](
        self, path: str, value: T, model: type[T]
    ) -> T:
        reference = self._client.document(path)

        @async_transactional
        async def apply(transaction: AsyncTransaction) -> T:
            snapshot = await reference.get(transaction=transaction)
            if snapshot.exists:
                current = model.model_validate(_data(snapshot))
                if current == value:
                    return current
                raise ResourceConflictError(f"immutable browser record {path} changed")
            transaction.create(reference, encode(value))
            return value

        return await apply(self._client.transaction(max_attempts=5))


def _data(snapshot: DocumentSnapshot) -> dict[str, Any]:
    value = snapshot.to_dict()
    if value is None:
        raise StorageIntegrityError(f"browser document {snapshot.id} has no data")
    return value
