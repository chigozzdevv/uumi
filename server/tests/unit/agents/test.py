from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from agents.continuity import AgentContinuityService
from agents.deploy import (
    _canonical_deployment,
    _deployment_config,
    _deployment_credentials,
    _effective_identity,
    _grant_callers,
    _staged_agent_source,
)
from agents.fleet import _SKILLS, AgentFleetService
from agents.redact import redact
from agents.runtime import AgentRuntimeService, _a2a_endpoint, _a2a_output, _prompt
from agents.shared.app import _bind_request_tenant, _required_environment, managed_app
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
        owner="Uumi",
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
        "projects/project-one/locations/us-central1/keyRings/uumi/cryptoKeys/agents",
        "projects/project-one/locations/us-central1/agentGateways/ingress",
        "projects/project-one/locations/us-central1/agentGateways/egress",
    )

    assert config["identity_type"] is types.IdentityType.AGENT_IDENTITY
    assert "service_account" not in config
    assert "env_vars" not in config
    assert config["agent_gateway_config"] == {
        "client_to_agent_config": {
            "agent_gateway": "projects/project-one/locations/us-central1/agentGateways/ingress"
        },
        "agent_to_anywhere_config": {
            "agent_gateway": "projects/project-one/locations/us-central1/agentGateways/egress"
        },
    }


def test_agent_deployment_stages_importable_top_level_packages() -> None:
    previous = Path.cwd()

    with _staged_agent_source() as packages:
        staged = Path.cwd()
        assert packages == (
            "agents",
            "browser",
            "connectors",
            "core",
            "contracts",
            "policy",
            "telemetry",
        )
        assert (staged / "agents" / "shared" / "app.py").is_file()
        assert (staged / "contracts" / "agent.py").is_file()
        assert not tuple(staged.rglob("__pycache__"))

    assert Path.cwd() == previous
    assert not staged.exists()


def test_agent_build_uses_non_resource_sentinel_before_runtime_injection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GOOGLE_CLOUD_AGENT_ENGINE_ID", raising=False)

    assert (
        _required_environment("GOOGLE_CLOUD_AGENT_ENGINE_ID", "test-agent-engine")
        == "test-agent-engine"
    )


def test_agent_deployment_requires_effective_identity() -> None:
    resource = type("Resource", (), {"spec": type("Spec", (), {"effective_identity": None})()})()

    with pytest.raises(RuntimeError, match="no managed Agent Identity"):
        _effective_identity(resource)


def test_agent_deployment_normalises_managed_effective_identity() -> None:
    identity = (
        "agents.global.org-485216906701.system.id.goog/resources/aiplatform/"
        "projects/256626005636/locations/us-east1/reasoningEngines/942888395422564352"
    )
    resource = type(
        "Resource",
        (),
        {"spec": type("Spec", (), {"effective_identity": identity})()},
    )()

    assert _effective_identity(resource) == f"principal://{identity}"


def test_agent_deployment_preserves_google_canonical_resource() -> None:
    assert (
        _canonical_deployment(
            "projects/256626005636/locations/us-east1/reasoningEngines/942888395422564352",
            "us-east1",
        )
        == "projects/256626005636/locations/us-east1/reasoningEngines/942888395422564352"
    )


def test_agent_deployment_impersonates_explicit_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    source = object()
    created: dict[str, object] = {}

    monkeypatch.setattr(
        "agents.deploy.google.auth.default",
        lambda **kwargs: (source, None),
    )

    def credentials(**kwargs: object) -> object:
        created.update(kwargs)
        return object()

    monkeypatch.setattr("agents.deploy.impersonated_credentials.Credentials", credentials)

    result = _deployment_credentials("uumi-agents@project-one.iam.gserviceaccount.com")

    assert result is not None
    assert created == {
        "source_credentials": source,
        "target_principal": "uumi-agents@project-one.iam.gserviceaccount.com",
        "target_scopes": ("https://www.googleapis.com/auth/cloud-platform",),
        "lifetime": 3600,
    }


@pytest.mark.anyio
async def test_agent_deployment_grants_only_declared_callers() -> None:
    google = PolicyGoogle()

    await _grant_callers(
        google,  # type: ignore[arg-type]
        "test",
        "us-central1",
        "projects/test/locations/us-central1/reasoningEngines/123",
        "projects/test/roles/uumiAgentCaller",
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
            "role": "projects/test/roles/uumiAgentCaller",
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
            "role": "projects/test/roles/uumiAgentCaller",
            "members": ["serviceAccount:obsolete@test.iam.gserviceaccount.com"],
        }
    ]

    await _grant_callers(
        google,  # type: ignore[arg-type]
        "test",
        "us-central1",
        "projects/test/locations/us-central1/reasoningEngines/123",
        "projects/test/roles/uumiAgentCaller",
        frozenset({"serviceAccount:coordinator@test.iam.gserviceaccount.com"}),
    )

    assert google.policy["bindings"] == [
        {
            "role": "projects/test/roles/uumiAgentCaller",
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
    assert message["role"] == "1"
    assert message["metadata"] == {"uumi_organisation_id": "org_acme"}
    assert "do-not-send" not in message["content"][0]["text"]
    assert google.headers == {"A2A-Version": "0.3"}


def test_a2a_endpoint_uses_the_agent_runtime_compatibility_route() -> None:
    value = registration().model_copy(
        update={
            "identity": (
                "principal://agents.global.org-485216906701.system.id.goog/"
                "resources/aiplatform/projects/256626005636/locations/us-east1/"
                "reasoningEngines/123"
            ),
            "deployment": "projects/useuumi/locations/us-east1/reasoningEngines/123",
            "region": "us-east1",
        }
    )
    assert _a2a_endpoint(value) == (
        "https://us-east1-aiplatform.googleapis.com/v1beta1/projects/256626005636/locations/"
        "us-east1/reasoningEngines/123/a2a/v1/message:send"
    )


def test_a2a_request_binds_and_rejects_mismatched_tenants() -> None:
    from a2a.server.context import ServerCallContext
    from a2a.types import SendMessageRequest
    from a2a.utils.errors import InvalidParamsError
    from google.protobuf.json_format import ParseDict  # type: ignore[import-untyped]

    request = ParseDict(
        {
            "message": {
                "messageId": "message_one",
                "role": "ROLE_USER",
                "parts": [{"text": "Plan a rotation"}],
                "metadata": {"uumi_organisation_id": "org_acme"},
            }
        },
        SendMessageRequest(),
    )
    context = ServerCallContext()

    assert _bind_request_tenant(request, context) == "org_acme"
    assert context.tenant == "org_acme"

    context.tenant = "org_other"
    with pytest.raises(InvalidParamsError, match="does not match"):
        _bind_request_tenant(request, context)


def test_managed_agent_enables_runtime_v03_compatibility() -> None:
    from google.adk.agents import LlmAgent
    from google.adk.apps import App

    value = managed_app(
        App(
            name="test_app",
            root_agent=LlmAgent(
                name="test_agent",
                description="Test agent",
                model="gemini-2.5-flash",
            ),
        ),
        {"test_skill"},
    )

    assert [item.protocol_version for item in value.agent_card.supported_interfaces] == [
        "1.0",
        "0.3",
    ]
    assert value._tmpl_attrs["extended_agent_card"] is value.agent_card


def test_managed_agent_accepts_the_deployed_runtime_http_contract() -> None:
    from a2a.server.agent_execution import AgentExecutor, RequestContext
    from a2a.server.context import ServerCallContext
    from a2a.server.events import EventQueue
    from a2a.server.tasks import TaskStore
    from a2a.types import ListTasksRequest, ListTasksResponse, Task, TaskState
    from google.adk.agents import LlmAgent
    from google.adk.apps import App
    from starlette.applications import Starlette
    from starlette.testclient import TestClient

    owners: list[str] = []

    class Executor(AgentExecutor):
        async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
            task = Task(id=context.task_id, context_id=context.context_id)
            task.status.state = TaskState.TASK_STATE_COMPLETED
            await event_queue.enqueue_event(task)

        async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
            del context, event_queue

    class Store(TaskStore):
        async def save(self, task: Task, context: ServerCallContext) -> None:
            del task
            owners.append(context.tenant)

        async def get(self, task_id: str, context: ServerCallContext) -> Task | None:
            del task_id, context
            return None

        async def list(
            self, params: ListTasksRequest, context: ServerCallContext
        ) -> ListTasksResponse:
            del params, context
            return ListTasksResponse()

        async def delete(self, task_id: str, context: ServerCallContext) -> None:
            del task_id, context

    value = managed_app(
        App(
            name="contract_app",
            root_agent=LlmAgent(
                name="contract_agent",
                description="Contract agent",
                model="gemini-2.5-flash",
            ),
        ),
        {"contract_test"},
    )
    value._tmpl_attrs["agent_executor_builder"] = Executor
    value._tmpl_attrs["agent_executor_kwargs"] = {}
    value._tmpl_attrs["task_store_builder"] = Store
    value._tmpl_attrs["task_store_kwargs"] = {}
    value.set_up()

    response = TestClient(Starlette(routes=value.rest_routes)).post(
        "/a2a/v1/message:send",
        headers={"A2A-Version": "0.3"},
        json={
            "message": {
                "messageId": "message_one",
                "role": "1",
                "content": [{"text": "Plan a rotation"}],
                "metadata": {"uumi_organisation_id": "org_acme"},
            }
        },
    )

    assert response.status_code == 200
    assert response.json()["task"]["status"]["state"] == "TASK_STATE_COMPLETED"
    assert owners == ["org_acme"]


class RuntimeGoogle:
    def __init__(self) -> None:
        self.body: dict[str, object] = {}
        self.headers: dict[str, str] = {}

    async def request(self, method: str, url: str, **kwargs: object) -> dict[str, object]:
        assert method == "POST"
        assert url.endswith("/a2a/v1/message:send")
        body = kwargs.get("json")
        assert isinstance(body, dict)
        self.body = body
        headers = kwargs.get("headers")
        assert isinstance(headers, dict)
        self.headers = headers
        return {"task": {"artifacts": [{"parts": [{"text": '{"decision":"plan"}'}]}]}}


class RuntimeFleet:
    async def resolve(self, organisation_id: str, kind: AgentKind, skill: str) -> AgentRegistration:
        return registration().model_copy(
            update={
                "identity": (
                    "principal://agents.global.org-485216906701.system.id.goog/"
                    "resources/aiplatform/projects/123/locations/us-central1/"
                    "reasoningEngines/123"
                ),
                "deployment": "projects/test/locations/us-central1/reasoningEngines/123",
            }
        )


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


@pytest.mark.anyio
async def test_agent_runtime_surfaces_safe_connector_error_code() -> None:
    class FailingGoogle:
        async def request(self, method: str, url: str, **kwargs: object) -> dict[str, object]:
            raise ConnectorError(
                "google-api-404",
                "upstream details must not escape",
                safe_detail="invalid-argument.field-message.messageId",
            )

    runtime = AgentRuntimeService(
        RuntimeFleet(),  # type: ignore[arg-type]
        RuntimeContinuity(),  # type: ignore[arg-type]
        FailingGoogle(),  # type: ignore[arg-type]
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
            requested_at=NOW,
        )
    )

    assert result.succeeded is False
    assert result.error == (
        "google-api-404.a2a-send.invalid-argument.field-message.messageId: agent execution failed"
    )
    assert "upstream details" not in result.error


@pytest.mark.anyio
async def test_agent_runtime_surfaces_safe_memory_stage() -> None:
    class FailingContinuity(RuntimeContinuity):
        async def retrieve(
            self, registration: AgentRegistration, query: str, count: int
        ) -> tuple[dict[str, object], ...]:
            raise ConnectorError("google-api-404", "upstream details must not escape")

    runtime = AgentRuntimeService(
        RuntimeFleet(),  # type: ignore[arg-type]
        FailingContinuity(),  # type: ignore[arg-type]
        RuntimeGoogle(),  # type: ignore[arg-type]
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
            requested_at=NOW,
        )
    )

    assert result.succeeded is False
    assert result.error == "google-api-404.memory-retrieve: agent execution failed"
    assert "upstream details" not in result.error


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
    assert session.purpose == "plan rotation"
    assert "displayName" not in google.body
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


class MemoryDocumentSnapshot:
    def __init__(self, data: dict[str, object] | None, exists: bool = True) -> None:
        self._data = data
        self.exists = exists

    def to_dict(self) -> dict[str, object] | None:
        return self._data


class MemoryDocumentReference:
    def __init__(self, store: dict[str, dict[str, object]], path: str) -> None:
        self._store = store
        self._path = path

    async def set(self, data: dict[str, object]) -> None:
        self._store[self._path] = data

    async def get(self) -> MemoryDocumentSnapshot:
        if self._path in self._store:
            return MemoryDocumentSnapshot(self._store[self._path], exists=True)
        return MemoryDocumentSnapshot(None, exists=False)

    async def delete(self) -> None:
        self._store.pop(self._path, None)


class MemoryQuery:
    def __init__(self, items: list[dict[str, object]]) -> None:
        self._items = items

    def where(self, field: str, op: str, value: object) -> "MemoryQuery":
        filtered = [item for item in self._items if item.get(field) == value]
        return MemoryQuery(filtered)

    def limit(self, count: int) -> "MemoryQuery":
        return MemoryQuery(self._items[:count])

    async def stream(self) -> AsyncGenerator[MemoryDocumentSnapshot, None]:
        for item in self._items:
            yield MemoryDocumentSnapshot(item, exists=True)


class MemoryCollectionReference:
    def __init__(self, store: dict[str, dict[str, object]], prefix: str) -> None:
        self._store = store
        self._prefix = prefix

    def where(self, field: str, op: str, value: object) -> MemoryQuery:
        items = [v for k, v in self._store.items() if k.startswith(self._prefix)]
        return MemoryQuery(items).where(field, op, value)

    def limit(self, count: int) -> MemoryQuery:
        items = [v for k, v in self._store.items() if k.startswith(self._prefix)]
        return MemoryQuery(items).limit(count)

    async def stream(self) -> AsyncGenerator[MemoryDocumentSnapshot, None]:
        items = [v for k, v in self._store.items() if k.startswith(self._prefix)]
        for item in items:
            yield MemoryDocumentSnapshot(item, exists=True)


class MemoryFirestoreClient:
    def __init__(self) -> None:
        self._store: dict[str, dict[str, object]] = {}

    def document(self, path: str) -> MemoryDocumentReference:
        return MemoryDocumentReference(self._store, path)

    def collection(self, path: str) -> MemoryCollectionReference:
        return MemoryCollectionReference(self._store, path)


@pytest.mark.anyio
async def test_firestore_task_store_lifecycle() -> None:
    from a2a.server.context import ServerCallContext
    from a2a.types import ListTasksRequest, Task, TaskState
    from agents.shared.tasks import FirestoreTaskStore

    client = MemoryFirestoreClient()
    store = FirestoreTaskStore(client=client)  # type: ignore[arg-type]

    context = ServerCallContext()
    context.tenant = "org_test"

    task = Task(id="task_1", context_id="ctx_1")
    task.status.state = TaskState.TASK_STATE_WORKING

    await store.save(task, context)

    fetched = await store.get("task_1", context)
    assert fetched is not None
    assert fetched.id == "task_1"
    assert fetched.context_id == "ctx_1"
    assert fetched.status.state == TaskState.TASK_STATE_WORKING

    listing = await store.list(ListTasksRequest(context_id="ctx_1"), context)
    assert listing.total_size == 1
    assert listing.tasks[0].id == "task_1"

    await store.delete("task_1", context)
    assert await store.get("task_1", context) is None
