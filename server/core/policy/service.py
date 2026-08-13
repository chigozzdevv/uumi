from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from contracts import Policy, PolicyDefinition, PolicyState, PolicyVersion, Stage
from policy import REQUIRED_CHECKS, GatePolicy, digest

from core.errors import ResourceConflictError


class PolicyRepository(Protocol):
    async def create(self, policy: Policy) -> Policy: ...

    async def create_version(
        self,
        organisation_id: str,
        policy_id: str,
        factory: Callable[[int], PolicyVersion],
    ) -> PolicyVersion: ...

    async def activate(
        self,
        organisation_id: str,
        policy_id: str,
        version_id: str,
        actor_id: str,
        now: datetime,
    ) -> PolicyVersion: ...


class PolicyService:
    def __init__(
        self,
        repository: PolicyRepository,
        clock: Callable[[], datetime],
    ) -> None:
        self._repository = repository
        self._clock = clock

    async def create(self, organisation_id: str, policy_id: str, name: str) -> Policy:
        now = self._clock()
        return await self._repository.create(
            Policy(
                id=policy_id,
                organisation_id=organisation_id,
                name=name,
                created_at=now,
                updated_at=now,
            )
        )

    async def create_version(
        self,
        organisation_id: str,
        policy_id: str,
        version_id: str,
        definition: PolicyDefinition,
        actor_id: str,
    ) -> PolicyVersion:
        _validate_definition(definition)
        now = self._clock()

        def factory(number: int) -> PolicyVersion:
            return PolicyVersion(
                id=version_id,
                organisation_id=organisation_id,
                policy_id=policy_id,
                number=number,
                definition=definition,
                digest=digest(definition),
                state=PolicyState.DRAFT,
                created_by=actor_id,
                created_at=now,
            )

        return await self._repository.create_version(organisation_id, policy_id, factory)

    async def activate(
        self,
        organisation_id: str,
        policy_id: str,
        version_id: str,
        actor_id: str,
    ) -> PolicyVersion:
        return await self._repository.activate(
            organisation_id, policy_id, version_id, actor_id, self._clock()
        )


def _validate_definition(definition: PolicyDefinition) -> None:
    GatePolicy(definition.required_checks)
    for stage in Stage:
        missing = REQUIRED_CHECKS[stage].difference(definition.required_checks[stage])
        if missing:
            raise ResourceConflictError(
                f"policy cannot remove mandatory {stage.value} checks: {', '.join(sorted(missing))}"
            )
