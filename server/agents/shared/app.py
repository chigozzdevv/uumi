import os
from collections.abc import Collection
from typing import Any

import vertexai
from google.adk.apps import App


def managed_app(app: App, skills: Collection[str]) -> Any:
    vertexai.init(
        project=os.environ.get("GOOGLE_CLOUD_PROJECT", "firekey-local"),
        location=os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"),
    )
    from a2a.types import AgentSkill
    from vertexai.agent_engines.templates.a2a import A2aAgent, create_agent_card

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
                description=f"FireKey {skill.replace('_', ' ')} capability.",
                tags=["firekey", skill],
            )
            for skill in sorted(skills)
        ],
        streaming=False,
    )
    return A2aAgent(
        agent_card=card,
        agent_executor_builder=_executor,
        agent_executor_kwargs={"app": app},
        task_store_builder=_task_store,
    )


def _executor(app: App) -> Any:
    from google.adk.a2a.executor.a2a_agent_executor import A2aAgentExecutor
    from google.adk.a2a.executor.config import A2aAgentExecutorConfig
    from google.adk.memory import VertexAiMemoryBankService
    from google.adk.runners import Runner
    from google.adk.sessions import VertexAiSessionService

    project = _required_environment("GOOGLE_CLOUD_PROJECT")
    location = _required_environment("GOOGLE_CLOUD_LOCATION")
    engine_id = _required_environment("GOOGLE_CLOUD_AGENT_ENGINE_ID")
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
    from google.adk.a2a import _compat
    from google.adk.a2a.converters.request_converter import (
        convert_a2a_request_to_agent_run_request,
    )

    request = convert_a2a_request_to_agent_run_request(context, part_converter)
    message = getattr(context, "message", None)
    metadata = _compat.meta_to_dict(getattr(message, "metadata", None))
    organisation_id = metadata.get("firekey_organisation_id")
    if not isinstance(organisation_id, str) or not organisation_id:
        raise ValueError("A2A request is missing its FireKey organisation binding")
    request.user_id = organisation_id
    return request


def _task_store() -> Any:
    from agents.shared.tasks import FirestoreTaskStore

    return FirestoreTaskStore()


def _required_environment(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"managed agent environment is missing {name}")
    return value
