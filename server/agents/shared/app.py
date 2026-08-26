import os
from collections.abc import Collection
from contextvars import ContextVar
from copy import deepcopy
from typing import Any

import vertexai
from google.adk.apps import App
from google.adk.sessions import BaseSessionService, InMemorySessionService
from vertexai.agent_engines.templates.a2a import A2aAgent, create_agent_card

from agents.redact import redact

_request_session: ContextVar[InMemorySessionService | None] = ContextVar(
    "uumi_request_session", default=None
)


class RequestSessionService(BaseSessionService):
    def _current(self) -> InMemorySessionService:
        service = _request_session.get()
        if service is None:
            raise RuntimeError("managed agent session is outside an A2A request")
        return service

    async def create_session(self, **kwargs: Any) -> Any:
        return await self._current().create_session(**kwargs)

    async def get_session(self, **kwargs: Any) -> Any:
        return await self._current().get_session(**kwargs)

    async def list_sessions(self, **kwargs: Any) -> Any:
        return await self._current().list_sessions(**kwargs)

    async def delete_session(self, **kwargs: Any) -> None:
        await self._current().delete_session(**kwargs)

    async def append_event(self, session: Any, event: Any) -> Any:
        return await self._current().append_event(session, event)

    async def get_user_state(self, **kwargs: Any) -> dict[str, Any]:
        return await self._current().get_user_state(**kwargs)


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
        token = _request_session.set(InMemorySessionService())  # type: ignore[no-untyped-call]
        try:
            return await super().on_message_send(request, context)
        finally:
            _request_session.reset(token)


def managed_app(app: App, skills: Collection[str]) -> Any:
    vertexai.init(
        project=_required_environment("UUMI_GOOGLE_CLOUD_PROJECT"),
        location=_required_environment("UUMI_GOOGLE_CLOUD_LOCATION"),
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
    from google.adk.runners import Runner

    runner = Runner(
        app=app,
        session_service=RequestSessionService(),
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
    metadata = _message_metadata(context)
    run_id = metadata.get("uumi_run_id")
    task_context = redact(deepcopy(metadata.get("uumi_task_context")))
    if not isinstance(run_id, str) or not run_id:
        raise ValueError("managed A2A request is missing its run binding")
    if not isinstance(task_context, dict):
        raise ValueError("managed A2A request task context must be an object")
    request.user_id = organisation_id
    request.state_delta = {
        "organisation_id": organisation_id,
        "run_id": run_id,
        "task_context": task_context,
    }
    return request


def _message_metadata(context: Any) -> dict[str, Any]:
    from google.adk.a2a import _compat

    message = getattr(context, "message", None)
    return _compat.meta_to_dict(getattr(message, "metadata", None))


def _task_store() -> Any:
    from a2a.server.tasks import InMemoryTaskStore

    # Uumi persists the authoritative AgentResult in the control plane. The A2A
    # task object only coordinates one synchronous message:send request.
    return InMemoryTaskStore()


def _required_environment(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if not value:
        raise RuntimeError(f"managed agent environment is missing {name}")
    return value
