from contracts import AgentKind, AgentRegistration, AgentStatus

from agents.storage import AgentRepository


class AgentFleetService:
    def __init__(self, repository: AgentRepository) -> None:
        self._repository = repository

    async def register(self, registration: AgentRegistration) -> AgentRegistration:
        expected = _SKILLS[registration.kind]
        if registration.skills != expected:
            raise ValueError(f"{registration.kind.value} agent has an invalid skill boundary")
        if registration.status is not AgentStatus.READY:
            raise ValueError("only ready Agent Runtime deployments may enter the fleet")
        if not registration.deployment.startswith("projects/"):
            raise ValueError("agent deployment must be a managed Agent Runtime resource")
        expected_registry = (
            f"//agentregistry.googleapis.com/projects/"
            f"{registration.deployment.split('/')[1]}/locations/{registration.region}"
        )
        if registration.registry != expected_registry:
            raise ValueError("agent must use the regional registry paired with its deployment")
        for gateway in (registration.ingress_gateway, registration.egress_gateway):
            if not gateway.startswith(
                f"projects/{registration.deployment.split('/')[1]}/locations/"
                f"{registration.region}/agentGateways/"
            ):
                raise ValueError("agent gateways must match the deployment project and region")
        if not registration.identity.startswith("principal://"):
            raise ValueError("agent deployment must expose a managed Agent Identity")
        return await self._repository.activate(registration)

    async def resolve(self, organisation_id: str, kind: AgentKind, skill: str) -> AgentRegistration:
        candidates = [
            item
            for item in await self._repository.list(organisation_id)
            if item.kind is kind and item.status is AgentStatus.READY and skill in item.skills
        ]
        if len(candidates) != 1:
            raise ValueError(
                f"expected one ready {kind.value} agent for {skill}, found {len(candidates)}"
            )
        return candidates[0]


_SKILLS: dict[AgentKind, frozenset[str]] = {
    AgentKind.INVENTORY: frozenset(
        {"correlate_exposure", "resolve_consumers", "detect_stale_mapping"}
    ),
    AgentKind.PLANNER: frozenset(
        {
            "plan_rotation",
            "select_strategy",
            "bind_playbook",
            "diagnose_failed_stage",
            "recommend_authorised_recovery",
        }
    ),
    AgentKind.PLAYBOOK: frozenset({"build_playbook", "analyse_walkthrough", "validate_playbook"}),
    AgentKind.OPERATOR: frozenset({"execute_console_playbook", "detect_interface_drift"}),
}
