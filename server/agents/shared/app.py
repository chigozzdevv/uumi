import os
from collections.abc import Collection
from copy import deepcopy
from typing import Any

import vertexai
from google.adk.apps import App
from vertexai.agent_engines.templates.a2a import A2aAgent, create_agent_card


class UumiA2aAgent(A2aAgent):
    def clone(self) -> "UumiA2aAgent":
        return UumiA2aAgent(
            agent_card=deepcopy(self.agent_card),
            task_store_builder=self._tmpl_attrs["task_store_builder"],
            task_store_kwargs=self._tmpl_attrs["task_store_kwargs"],
            agent_executor_kwargs=self._tmpl_attrs["agent_executor_kwargs"],
            agent_executor_builder=self._tmpl_attrs["agent_executor_builder"],
            request_handler_kwargs=self._tmpl_attrs["request_handler_kwargs"],
            request_handler_builder=self._tmpl_attrs["request_handler_builder"],
            extended_agent_card=self._tmpl_attrs["extended_agent_card"],
        )

    def set_up(self) -> None:
        super().set_up()  # type: ignore[no-untyped-call]
        primary_url = self.agent_card.supported_interfaces[0].url
        for interface in self.agent_card.supported_interfaces:
            interface.url = primary_url
        self._tmpl_attrs["agent_card"] = self.agent_card

    async def on_message_send(self, request: Any, context: Any) -> Any:
        _bind_request_tenant(request, context)
        return await super().on_message_send(request, context)


def managed_app(app: App, skills: Collection[str]) -> Any:
    vertexai.init(
        project=os.environ.get("GOOGLE_CLOUD_PROJECT", "uumi-local"),
        location=os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"),
    )
    from a2a.types import AgentInterface, AgentSkill
    from a2a.utils.constants import TransportProtocol

    agent = app.root_agent
    if agent is None:
        raise ValueError("managed A2A application requires a root agent")
    card = create_agent_card(
        agent_name=agent.name,
        description=agent.description,
        skills=[
            AgentSkill(
                id=skill,
                name=skill.replace("_", " ").title(),
                description=f"Uumi {skill.replace('_', ' ')} capability.",
                tags=["uumi", skill],
            )
            for skill in sorted(skills)
        ],
        streaming=False,
    )
    card.supported_interfaces.append(
        AgentInterface(
            url=card.supported_interfaces[0].url,
            protocol_binding=TransportProtocol.HTTP_JSON,
            protocol_version="0.3",
        )
    )
    return UumiA2aAgent(
        agent_card=card,
        agent_executor_builder=_executor,
        agent_executor_kwargs={"app": app},
        task_store_builder=_task_store,
        extended_agent_card=card,
    )


def _bind_request_tenant(request: Any, context: Any) -> str:
    from a2a.utils.errors import InvalidParamsError
    from google.adk.a2a import _compat

    message = getattr(request, "message", None)
    metadata = _compat.meta_to_dict(getattr(message, "metadata", None))
    organisation_id = metadata.get("uumi_organisation_id")
    if not isinstance(organisation_id, str) or not organisation_id:
        raise InvalidParamsError(message="A2A request is missing its Uumi organisation binding")
    tenant = getattr(context, "tenant", "")
    if tenant and tenant != organisation_id:
        raise InvalidParamsError(message="A2A request tenant does not match its Uumi organisation")
    context.tenant = organisation_id
    return organisation_id


def _executor(app: App) -> Any:
    from google.adk.a2a.executor.a2a_agent_executor import A2aAgentExecutor
    from google.adk.a2a.executor.config import A2aAgentExecutorConfig
    from google.adk.memory import VertexAiMemoryBankService
    from google.adk.runners import Runner
    from google.adk.sessions import VertexAiSessionService

    project = _required_environment("GOOGLE_CLOUD_PROJECT")
    location = _required_environment("GOOGLE_CLOUD_LOCATION")
    engine_id = _required_environment("GOOGLE_CLOUD_AGENT_ENGINE_ID", "test-agent-engine")
    runner = Runner(
        app=app,
        session_service=VertexAiSessionService(project, location, engine_id),
        memory_service=VertexAiMemoryBankService(project, location, engine_id),
    )
    return A2aAgentExecutor(
        runner=runner,
        config=A2aAgentExecutorConfig(request_converter=_request),
    )


def _request(context: Any, part_converter: Any) -> Any:
    from google.adk.a2a.converters.request_converter import (
        convert_a2a_request_to_agent_run_request,
    )

    request = convert_a2a_request_to_agent_run_request(context, part_converter)
    organisation_id = _bind_request_tenant(context, context.call_context)
    request.user_id = organisation_id
    return request


def _task_store() -> Any:
    from agents.shared.tasks import FirestoreTaskStore

    return FirestoreTaskStore()


def _required_environment(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if not value:
        raise RuntimeError(f"managed agent environment is missing {name}")
    return value
