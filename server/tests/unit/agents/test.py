from datetime import UTC, datetime

import pytest
from agents.continuity import AgentContinuityService
from agents.deploy import _deployment_config, _effective_identity, _grant_callers
from agents.fleet import _SKILLS, AgentFleetService
from agents.runtime import AgentRuntimeService, _a2a_output, _prompt
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


@pytest.mark.anyio
async def test_agent_deployment_grants_only_declared_callers() -> None:
    google = PolicyGoogle()

    await _grant_callers(
        google,  # type: ignore[arg-type]
        "us-central1",
        "projects/test/locations/us-central1/reasoningEngines/planner",
        "projects/test/roles/firekeyAgentCaller",
        frozenset(
            {
                "serviceAccount:api@test.iam.gserviceaccount.com",
                "serviceAccount:coordinator@test.iam.gserviceaccount.com",
            }
        ),
    )

    assert google.policy["bindings"] == [
        {"role": "roles/viewer", "members": ["group:security@example.com"]},
        {
            "role": "projects/test/roles/firekeyAgentCaller",
            "members": [
                "serviceAccount:api@test.iam.gserviceaccount.com",
                "serviceAccount:coordinator@test.iam.gserviceaccount.com",
            ],
        },
    ]


@pytest.mark.anyio
async def test_agent_deployment_removes_obsolete_callers() -> None:
    google = PolicyGoogle()
    google.policy["bindings"] = [
        {
            "role": "projects/test/roles/firekeyAgentCaller",
            "members": ["serviceAccount:obsolete@test.iam.gserviceaccount.com"],
        }
    ]

    await _grant_callers(
        google,  # type: ignore[arg-type]
        "us-central1",
        "projects/test/locations/us-central1/reasoningEngines/planner",
        "projects/test/roles/firekeyAgentCaller",
        frozenset({"serviceAccount:coordinator@test.iam.gserviceaccount.com"}),
    )

    assert google.policy["bindings"] == [
        {
            "role": "projects/test/roles/firekeyAgentCaller",
            "members": ["serviceAccount:coordinator@test.iam.gserviceaccount.com"],
        }
    ]


class PolicyGoogle:
    def __init__(self) -> None:
        self.policy: dict[str, object] = {
            "etag": "etag-one",
            "bindings": [{"role": "roles/viewer", "members": ["group:security@example.com"]}],
        }

    async def request(self, method: str, url: str, **kwargs: object) -> dict[str, object]:
        assert method == "POST"
        if url.endswith(":getIamPolicy"):
            return self.policy
        body = kwargs.get("json")
        assert isinstance(body, dict)
        policy = body.get("policy")
        assert isinstance(policy, dict)
        self.policy = policy
        return policy


def test_managed_agents_publish_their_exact_a2a_skills() -> None:
    from agents.inventory.agent import agent_app as inventory
    from agents.operator.agent import agent_app as operator
    from agents.planner.agent import agent_app as planner
    from agents.playbook.agent import agent_app as playbook

    applications = {
        AgentKind.INVENTORY: inventory,
        AgentKind.PLANNER: planner,
        AgentKind.PLAYBOOK: playbook,
        AgentKind.OPERATOR: operator,
    }

    for kind, application in applications.items():
        assert application.agent_framework == "a2a"
        assert {skill.id for skill in application.agent_card.skills} == _SKILLS[kind]
        assert "on_message_send" in application.register_operations()["a2a_extension"]


def test_a2a_response_returns_only_structured_artifact() -> None:
    output = _a2a_output(
        {
            "artifacts": [{"parts": [{"text": '{"decision":"plan","safe":true}'}]}],
            "history": [{"parts": [{"text": "intermediate prose"}]}],
        }
    )

    assert output == {"decision": "plan", "safe": True}


@pytest.mark.anyio
async def test_agent_runtime_uses_bound_a2a_session(monkeypatch: pytest.MonkeyPatch) -> None:
    del monkeypatch
    google = RuntimeGoogle()
    runtime = AgentRuntimeService(
        RuntimeFleet(),  # type: ignore[arg-type]
        RuntimeContinuity(),  # type: ignore[arg-type]
        google,  # type: ignore[arg-type]
        "project-one",
        lambda: NOW,
    )
    from contracts import AgentTask

    result = await runtime.execute(
        AgentTask(
            id="task_one",
            organisation_id="org_acme",
            run_id="run_one",
            agent=AgentKind.PLANNER,
            skill="plan_rotation",
            objective="Plan a safe rotation",
            context={"credential_id": "credential_one", "api_key": "do-not-send"},
            requested_at=NOW,
        )
    )

    assert result.succeeded
    assert result.output == {"decision": "plan"}
    message = google.body["message"]
    assert isinstance(message, dict)
    assert message["contextId"] == "session-task-one"
    assert message["metadata"] == {"firekey_organisation_id": "org_acme"}
    assert "do-not-send" not in message["parts"][0]["text"]


class RuntimeGoogle:
    def __init__(self) -> None:
        self.body: dict[str, object] = {}

    async def request(self, method: str, url: str, **kwargs: object) -> dict[str, object]:
        assert method == "POST"
        assert url.endswith("/a2a/v1/message:send")
        body = kwargs.get("json")
        assert isinstance(body, dict)
        self.body = body
        return {"task": {"artifacts": [{"parts": [{"text": '{"decision":"plan"}'}]}]}}


class RuntimeFleet:
    async def resolve(self, organisation_id: str, kind: AgentKind, skill: str) -> AgentRegistration:
        return registration()


class RuntimeContinuity:
    async def create_session(
        self, value: AgentRegistration, session_id: str, run_id: str, purpose: str
    ) -> AgentSession:
        return AgentSession(
            id=session_id,
            organisation_id=value.organisation_id,
            run_id=run_id,
            agent=value.kind,
            remote_session=f"{value.deployment}/sessions/session-task-one",
            region=value.region,
            purpose=purpose,
            created_at=NOW,
            expires_at=datetime(2026, 8, 14, 12, tzinfo=UTC),
        )

    async def retrieve(
        self, registration: AgentRegistration, query: str, count: int
    ) -> tuple[dict[str, object], ...]:
        return ()


def test_agent_context_recursively_redacts_secret_material() -> None:
    value = redact(
        {
            "name": "production",
            "secret_value": "must-not-escape",
            "credential_id": "credential_one",
            "secret_reference": "projects/test/secrets/mail/versions/2",
            "nested": {"authorization": "Bearer credential", "safe": 3},
        }
    )

    assert value == {
        "name": "production",
        "secret_value": "[REDACTED]",
        "credential_id": "credential_one",
        "secret_reference": "projects/test/secrets/mail/versions/2",
        "nested": {"authorization": "[REDACTED]", "safe": 3},
    }


def test_agent_prompt_preserves_references_and_removes_values() -> None:
    from contracts import AgentTask

    prompt = _prompt(
        AgentTask(
            id="task_one",
            organisation_id="org_acme",
            run_id="run_one",
            agent=AgentKind.PLANNER,
            skill="plan_rotation",
            objective="Plan a safe rotation",
            context={
                "credential_id": "credential_one",
                "secret_reference": "projects/test/secrets/mail/versions/2",
                "api_key": "must-not-escape",
            },
            requested_at=NOW,
        )
    )

    assert '"credential_id":"credential_one"' in prompt
    assert '"secret_reference":"projects/test/secrets/mail/versions/2"' in prompt
    assert "must-not-escape" not in prompt


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
