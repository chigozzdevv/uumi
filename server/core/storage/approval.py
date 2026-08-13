import hmac
from datetime import datetime
from typing import Any

from contracts import Approval, ApprovalDecision, ProtectedAction
from google.cloud.firestore_v1 import AsyncClient
from google.cloud.firestore_v1.async_transaction import AsyncTransaction, async_transactional
from google.cloud.firestore_v1.base_document import DocumentSnapshot

from core.errors import ApprovalError, ResourceConflictError, ResourceNotFoundError
from core.storage.codec import encode
from core.storage.paths import FirestorePaths


class FirestoreApprovalRepository:
    def __init__(self, client: AsyncClient) -> None:
        self._client = client

    async def create(self, approval: Approval, action: ProtectedAction) -> Approval:
        approval_ref = self._client.document(
            FirestorePaths.approval(approval.organisation_id, approval.id)
        )
        action_ref = self._client.document(
            FirestorePaths.action(approval.organisation_id, action.id)
        )

        @async_transactional
        async def apply(transaction: AsyncTransaction) -> Approval:
            existing_approval = await approval_ref.get(transaction=transaction)
            existing_action = await action_ref.get(transaction=transaction)
            if existing_approval.exists:
                current = Approval.model_validate(_data(existing_approval))
                stored_action = (
                    ProtectedAction.model_validate(_data(existing_action))
                    if existing_action.exists
                    else None
                )
                replay = approval.model_copy(
                    update={
                        "created_at": current.created_at,
                        "revision": current.revision,
                    }
                )
                if current == replay and stored_action == action:
                    return current
                raise ResourceConflictError(f"approval {approval.id} already exists")
            if existing_action.exists:
                raise ResourceConflictError(f"action {action.id} is already awaiting approval")
            transaction.create(action_ref, encode(action))
            transaction.create(approval_ref, encode(approval))
            return approval

        return await apply(self._client.transaction(max_attempts=5))

    async def decide(
        self,
        organisation_id: str,
        approval_id: str,
        expected_revision: int,
        decision: ApprovalDecision,
        actor_id: str,
        decided_at: datetime,
    ) -> Approval:
        reference = self._client.document(FirestorePaths.approval(organisation_id, approval_id))

        @async_transactional
        async def apply(transaction: AsyncTransaction) -> Approval:
            snapshot = await reference.get(transaction=transaction)
            if not snapshot.exists:
                raise ResourceNotFoundError(f"approval {approval_id} was not found")
            current = Approval.model_validate(_data(snapshot))
            if current.revision != expected_revision:
                raise ResourceConflictError(
                    f"approval expected revision {expected_revision}, found {current.revision}"
                )
            if current.decision is not ApprovalDecision.PENDING:
                if current.decision is decision and current.approver_id == actor_id:
                    return current
                raise ApprovalError("approval already has a decision")
            if decided_at >= current.expires_at:
                raise ApprovalError("approval has expired")
            changed = current.model_copy(
                update={
                    "decision": decision,
                    "approver_id": actor_id,
                    "decided_at": decided_at,
                    "revision": current.revision + 1,
                }
            )
            transaction.set(reference, encode(changed))
            return changed

        return await apply(self._client.transaction(max_attempts=5))

    async def consume(
        self,
        organisation_id: str,
        approval_id: str,
        capability_hash: str,
        action_digest: str,
        plan_hash: str,
        evidence_hash: str,
        consumed_at: datetime,
    ) -> Approval:
        reference = self._client.document(FirestorePaths.approval(organisation_id, approval_id))

        @async_transactional
        async def apply(transaction: AsyncTransaction) -> Approval:
            snapshot = await reference.get(transaction=transaction)
            if not snapshot.exists:
                raise ResourceNotFoundError(f"approval {approval_id} was not found")
            current = Approval.model_validate(_data(snapshot))
            if current.consumed_at is not None:
                replay_values = (
                    (capability_hash, current.capability_hash),
                    (action_digest, current.action_digest),
                    (plan_hash, current.plan_hash),
                    (evidence_hash, current.evidence_hash),
                )
                if all(hmac.compare_digest(actual, expected) for actual, expected in replay_values):
                    return current
                raise ApprovalError("approval capability was consumed with different bindings")
            if current.decision is not ApprovalDecision.APPROVED:
                raise ApprovalError("approval was not granted")
            if consumed_at >= current.expires_at:
                raise ApprovalError("approval has expired")
            values = (
                (capability_hash, current.capability_hash, "capability"),
                (action_digest, current.action_digest, "action"),
                (plan_hash, current.plan_hash, "plan"),
                (evidence_hash, current.evidence_hash, "evidence"),
            )
            for actual, expected, label in values:
                if not hmac.compare_digest(actual, expected):
                    raise ApprovalError(f"approval {label} binding changed")
            changed = current.model_copy(
                update={
                    "consumed_at": consumed_at,
                    "revision": current.revision + 1,
                }
            )
            transaction.set(reference, encode(changed))
            return changed

        return await apply(self._client.transaction(max_attempts=5))


def _data(snapshot: DocumentSnapshot) -> dict[str, Any]:
    data = snapshot.to_dict()
    if data is None:
        raise ApprovalError(f"approval document {snapshot.id} has no data")
    return data
