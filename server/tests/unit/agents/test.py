from datetime import UTC, datetime

import pytest
from agents.continuity import AgentContinuityService
from agents.deploy import _deployment_config, _effective_identity
from agents.fleet import AgentFleetService
from agents.shared.context import redact
from connectors.base.errors import ConnectorError
from contracts import AgentKind, AgentMemory, AgentRegistration, AgentSession, AgentStatus
from vertexai import types

NOW = datetime(2026, 8, 13, 12, tzinfo=UTC)


class Repository:
    def __init__(self, values: tuple[AgentRegistration, ...]) -> None:
        self.values = values

    async def register(self, value: AgentRegistration) -> AgentRegistration:
        return value

    async def list(self, organisation_id: str) -> tuple[AgentRegistration, ...]:
        return tuple(value for value in self.values if value.organisation_id == organisation_id)


def registration() -> AgentRegistration:
    return AgentRegistration(
        id="planner_v1",
        organisation_id="org_acme",
        kind=AgentKind.PLANNER,
        display_name="Planner",
        version="1.0.0",
        skills=frozenset(
            {
                "plan_rotation",
                "select_strategy",
                "bind_playbook",
                "diagnose_failed_stage",
                "recommend_authorised_recovery",
            }
        ),
        owner="FireKey",
        identity="principal://iam.googleapis.com/projects/123/locations/global/workloadIdentityPools/agents/subject/planner",
        endpoint="https://us-central1-aiplatform.googleapis.com",
        deployment="projects/test/locations/us-central1/reasoningEngines/planner",
        registry="//agentregistry.googleapis.com/projects/test/locations/us-central1",
        ingress_gateway="projects/test/locations/us-central1/agentGateways/ingress",
        egress_gateway="projects/test/locations/us-central1/agentGateways/egress",
        region="us-central1",
        approved_callers=frozenset({"workflow@example.iam.gserviceaccount.com"}),
        tool_destinations=frozenset({"firestore"}),
        status=AgentStatus.READY,
        registered_at=datetime.now(UTC),
    )


@pytest.mark.anyio
async def test_fleet_resolves_exact_registered_skill() -> None:
    value = registration()
    service = AgentFleetService(Repository((value,)))  # type: ignore[arg-type]

    resolved = await service.resolve("org_acme", AgentKind.PLANNER, "plan_rotation")

    assert resolved == value


@pytest.mark.anyio
async def test_fleet_rejects_unregistered_skill_boundary() -> None:
    value = registration().model_copy(update={"skills": frozenset({"plan_rotation"})})
    service = AgentFleetService(Repository(()))  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="invalid skill boundary"):
        await service.register(value)


def test_agent_deployment_uses_identity_and_both_gateways() -> None:
    config = _deployment_config(
        "project-one",
        AgentKind.PLANNER,
        "1.2.3",
        "gs://staging",
        "projects/project-one/locations/us-central1/keyRings/firekey/cryptoKeys/agents",
        "projects/project-one/locations/us-central1/agentGateways/ingress",
        "projects/project-one/locations/us-central1/agentGateways/egress",
    )

    assert config["identity_type"] is types.IdentityType.AGENT_IDENTITY
    assert "service_account" not in config
    assert config["agent_gateway_config"] == {
        "client_to_agent_config": {
            "agent_gateway": "projects/project-one/locations/us-central1/agentGateways/ingress"
        },
        "agent_to_anywhere_config": {
            "agent_gateway": "projects/project-one/locations/us-central1/agentGateways/egress"
        },
    }


def test_agent_deployment_requires_effective_identity() -> None:
    resource = type("Resource", (), {"spec": type("Spec", (), {"effective_identity": None})()})()

    with pytest.raises(RuntimeError, match="no managed Agent Identity"):
        _effective_identity(resource)


def test_agent_context_recursively_redacts_secret_material() -> None:
    value = redact(
        {
            "name": "production",
            "secret_value": "must-not-escape",
            "nested": {"authorization": "Bearer credential", "safe": 3},
        }
    )

    assert value == {
        "name": "production",
        "secret_value": "[redacted]",
        "nested": {"authorization": "[redacted]", "safe": 3},
    }


@pytest.mark.anyio
async def test_managed_session_retry_reconciles_exact_remote_binding() -> None:
    repository = ContinuityRepository()
    google = ExistingGoogle("session")
    continuity = AgentContinuityService(
        repository,  # type: ignore[arg-type]
        google,  # type: ignore[arg-type]
        "project-one",
        "(default)",
        lambda: NOW,
    )

    session = await continuity.create_session(
        registration(), "session_task_one", "run_one", "plan rotation"
    )

    assert session.remote_session.endswith("/sessions/session-task-one")
    assert repository.session == session


@pytest.mark.anyio
async def test_memory_retry_reconciles_exact_approved_fact() -> None:
    repository = ContinuityRepository()
    google = ExistingGoogle("memory")
    continuity = AgentContinuityService(
        repository,  # type: ignore[arg-type]
        google,  # type: ignore[arg-type]
        "project-one",
        "(default)",
        lambda: NOW,
    )

    memory = await continuity.remember(
        registration(),
        "memory_one",
        "Provider key names may take ten seconds to appear.",
        ("evidence_one",),
        "administrator_one",
    )

    assert memory.remote_memory.endswith("/memories/memory-one")
    assert repository.memory == memory


class ContinuityRepository:
    def __init__(self) -> None:
        self.session: AgentSession | None = None
        self.memory: AgentMemory | None = None

    async def save_session(self, value: AgentSession) -> AgentSession:
        self.session = value
        return value

    async def save_memory(self, value: AgentMemory) -> AgentMemory:
        self.memory = value
        return value


class ExistingGoogle:
    def __init__(self, resource: str) -> None:
        self.resource = resource
        self.body: dict[str, object] = {}

    async def request(self, method: str, url: str, **kwargs: object) -> dict[str, object]:
        if method == "POST":
            body = kwargs.get("json")
            assert isinstance(body, dict)
            self.body = body
            raise ConnectorError("google-api-409", "already exists")
        assert method == "GET"
        name = (
            "projects/project-one/locations/us-central1/reasoningEngines/planner/"
            f"{self.resource}s/{self.resource}-task-one"
        )
        if self.resource == "session":
            return {
                "name": name,
                "userId": "org_acme",
                "sessionState": self.body["sessionState"],
            }
        return {
            "name": (
                "projects/project-one/locations/us-central1/reasoningEngines/planner/"
                "memories/memory-one"
            ),
            "fact": self.body["fact"],
            "scope": self.body["scope"],
            "revisionLabels": self.body["revisionLabels"],
        }
