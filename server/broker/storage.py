from datetime import datetime
from typing import Any

from contracts import (
    Approval,
    Connection,
    PlaybookAssignment,
    PlaybookVersion,
    ProtectedAction,
    RotationRun,
    ToolAttempt,
    ToolAttemptStatus,
    ToolRequest,
    ToolResult,
)
from core.errors import ResourceConflictError, StorageIntegrityError
from core.storage import FirestoreCatalog
from core.storage.codec import encode
from core.storage.paths import FirestorePaths
from google.cloud.firestore_v1 import AsyncClient
from google.cloud.firestore_v1.async_transaction import AsyncTransaction, async_transactional
from google.cloud.firestore_v1.base_document import DocumentSnapshot


class FirestoreBrokerRepository:
    def __init__(self, client: AsyncClient) -> None:
        self._client = client
        self._catalog = FirestoreCatalog(client)

    async def result(self, request: ToolRequest, request_hash: str) -> ToolResult | None:
        snapshot = await self._client.document(
            FirestorePaths.tool(request.organisation_id, request.id)
        ).get()
        if not snapshot.exists:
            return None
        attempt = ToolAttempt.model_validate(_data(snapshot))
        self._same_attempt(attempt, request, request_hash)
        if attempt.status is ToolAttemptStatus.RUNNING:
            raise ResourceConflictError(f"tool request {request.id} is already running")
        if attempt.result is None:
            raise StorageIntegrityError("terminal tool attempt has no result")
        return attempt.result

    async def begin(self, request: ToolRequest, request_hash: str, now: datetime) -> None:
        reference = self._client.document(FirestorePaths.tool(request.organisation_id, request.id))
        attempt = ToolAttempt(
            id=request.id,
            organisation_id=request.organisation_id,
            run_id=request.run_id,
            request_digest=request_hash,
            tool=request.tool,
            status=ToolAttemptStatus.RUNNING,
            started_at=now,
        )

        @async_transactional
        async def apply(transaction: AsyncTransaction) -> None:
            current = await reference.get(transaction=transaction)
            if current.exists:
                stored = ToolAttempt.model_validate(_data(current))
                self._same_attempt(stored, request, request_hash)
                raise ResourceConflictError(f"tool request {request.id} is already running")
            transaction.create(reference, encode(attempt))

        await apply(self._client.transaction(max_attempts=5))

    async def finish(
        self,
        request: ToolRequest,
        request_hash: str,
        result: ToolResult,
        now: datetime,
    ) -> None:
        reference = self._client.document(FirestorePaths.tool(request.organisation_id, request.id))

        @async_transactional
        async def apply(transaction: AsyncTransaction) -> None:
            snapshot = await reference.get(transaction=transaction)
            if not snapshot.exists:
                raise StorageIntegrityError(f"tool request {request.id} was not started")
            current = ToolAttempt.model_validate(_data(snapshot))
            self._same_attempt(current, request, request_hash)
            if current.status is not ToolAttemptStatus.RUNNING:
                if current.result == result:
                    return
                raise ResourceConflictError(f"tool request {request.id} already completed")
            changed = current.model_copy(
                update={
                    "status": (
                        ToolAttemptStatus.SUCCEEDED
                        if result.succeeded
                        else ToolAttemptStatus.FAILED
                    ),
                    "result": result,
                    "completed_at": now,
                }
            )
            transaction.set(reference, encode(changed))

        await apply(self._client.transaction(max_attempts=5))

    async def run(self, organisation_id: str, run_id: str) -> RotationRun:
        return await self._catalog.get(FirestorePaths.run(organisation_id, run_id), RotationRun)

    async def connection(self, organisation_id: str, connection_id: str) -> Connection:
        return await self._catalog.get(
            FirestorePaths.connection(organisation_id, connection_id), Connection
        )

    async def assignment(self, organisation_id: str, credential_id: str) -> PlaybookAssignment:
        return await self._catalog.get(
            FirestorePaths.assignment(organisation_id, credential_id), PlaybookAssignment
        )

    async def version(
        self, organisation_id: str, playbook_id: str, version_id: str
    ) -> PlaybookVersion:
        return await self._catalog.get(
            FirestorePaths.playbook_version(organisation_id, playbook_id, version_id),
            PlaybookVersion,
        )

    async def approval(self, organisation_id: str, approval_id: str) -> Approval:
        return await self._catalog.get(
            FirestorePaths.approval(organisation_id, approval_id), Approval
        )

    async def action(self, organisation_id: str, action_id: str) -> ProtectedAction:
        return await self._catalog.get(
            FirestorePaths.action(organisation_id, action_id), ProtectedAction
        )

    @staticmethod
    def _same_attempt(attempt: ToolAttempt, request: ToolRequest, request_hash: str) -> None:
        if (
            attempt.organisation_id != request.organisation_id
            or attempt.run_id != request.run_id
            or attempt.tool != request.tool
            or attempt.request_digest != request_hash
        ):
            raise ResourceConflictError(
                f"tool request ID {request.id} is bound to different parameters"
            )


def _data(snapshot: DocumentSnapshot) -> dict[str, Any]:
    value = snapshot.to_dict()
    if value is None:
        raise StorageIntegrityError(f"document {snapshot.id} has no data")
    return value
