import hashlib
import hmac
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from contracts import Approval, ApprovalDecision, ProtectedAction
from policy import digest

from core.errors import ApprovalError


@dataclass(frozen=True, slots=True)
class ApprovalCapability:
    approval: Approval
    token: str


class ApprovalRepository(Protocol):
    async def create(self, approval: Approval, action: ProtectedAction) -> Approval: ...

    async def decide(
        self,
        organisation_id: str,
        approval_id: str,
        expected_revision: int,
        decision: ApprovalDecision,
        actor_id: str,
        decided_at: datetime,
    ) -> Approval: ...

    async def consume(
        self,
        organisation_id: str,
        approval_id: str,
        capability_hash: str,
        action_digest: str,
        plan_hash: str,
        evidence_hash: str,
        consumed_at: datetime,
    ) -> Approval: ...


class ApprovalService:
    def __init__(
        self,
        repository: ApprovalRepository,
        clock: Callable[[], datetime],
    ) -> None:
        self._repository = repository
        self._clock = clock

    async def request(
        self,
        approval_id: str,
        action: ProtectedAction,
        plan_hash: str,
        evidence_hash: str,
        requester_id: str,
        expires_at: datetime,
    ) -> ApprovalCapability:
        now = self._clock()
        if expires_at <= now:
            raise ApprovalError("approval expiry must be in the future")
        token = secrets.token_urlsafe(32)
        approval = Approval(
            id=approval_id,
            organisation_id=action.organisation_id,
            run_id=action.run_id,
            action_id=action.id,
            action_digest=digest(action),
            plan_hash=plan_hash,
            evidence_hash=evidence_hash,
            generation_id=action.generation_id,
            requested_by=requester_id,
            capability_hash=_hash(token),
            expires_at=expires_at,
            created_at=now,
        )
        stored = await self._repository.create(approval, action)
        return ApprovalCapability(approval=stored, token=token)

    async def decide(
        self,
        organisation_id: str,
        approval_id: str,
        expected_revision: int,
        decision: ApprovalDecision,
        actor_id: str,
    ) -> Approval:
        if decision is ApprovalDecision.PENDING:
            raise ApprovalError("a pending decision cannot be submitted")
        return await self._repository.decide(
            organisation_id,
            approval_id,
            expected_revision,
            decision,
            actor_id,
            self._clock(),
        )

    async def consume(
        self,
        organisation_id: str,
        approval_id: str,
        capability: str,
        action: ProtectedAction,
        plan_hash: str,
        evidence_hash: str,
    ) -> Approval:
        return await self._repository.consume(
            organisation_id,
            approval_id,
            _hash(capability),
            digest(action),
            plan_hash,
            evidence_hash,
            self._clock(),
        )


def verify_capability(token: str, expected_hash: str) -> bool:
    return hmac.compare_digest(_hash(token), expected_hash)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
