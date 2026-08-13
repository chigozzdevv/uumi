import json
from collections.abc import Callable
from datetime import datetime
from typing import Any, cast

import vertexai
from contracts import AgentResult, AgentTask

from agents.continuity import AgentContinuityService
from agents.fleet import AgentFleetService


class AgentRuntimeService:
    def __init__(
        self,
        fleet: AgentFleetService,
        continuity: AgentContinuityService,
        project_id: str,
        clock: Callable[[], datetime],
    ) -> None:
        self._fleet = fleet
        self._continuity = continuity
        self._project = project_id
        self._clock = clock

    async def execute(self, task: AgentTask) -> AgentResult:
        try:
            registration = await self._fleet.resolve(task.organisation_id, task.agent, task.skill)
            session = await self._continuity.create_session(
                registration,
                f"session_{task.id}",
                task.run_id,
                f"{task.skill}: {task.objective[:160]}",
            )
            memories = await self._continuity.retrieve(registration, task.objective, count=5)
            client = vertexai.Client(project=self._project, location=registration.region)
            engine = client.agent_engines.get(name=registration.deployment)
            method = getattr(engine, "async_stream_query", None)
            if not callable(method):
                raise RuntimeError("Agent Runtime deployment does not expose async_stream_query")
            events: list[dict[str, Any]] = []
            stream = method(
                message=_prompt(task, memories),
                user_id=task.organisation_id,
                session_id=session.remote_session.rsplit("/", 1)[-1],
            )
            async for event in stream:
                events.append(_event(event))
            output = _last_output(events)
            return AgentResult(
                task_id=task.id,
                agent=task.agent,
                skill=task.skill,
                succeeded=True,
                output=output,
                evidence_ids=task.evidence_ids,
                completed_at=self._clock(),
            )
        except Exception as error:
            return AgentResult(
                task_id=task.id,
                agent=task.agent,
                skill=task.skill,
                succeeded=False,
                error=f"{type(error).__name__}: {error}".replace("\n", " ")[:1024],
                completed_at=self._clock(),
            )


def _prompt(task: AgentTask, memories: tuple[dict[str, Any], ...] = ()) -> str:
    return json.dumps(
        {
            "task_id": task.id,
            "skill": task.skill,
            "objective": task.objective,
            "context": task.context,
            "evidence_ids": task.evidence_ids,
            "approved_memory": memories,
        },
        separators=(",", ":"),
        sort_keys=True,
    )


def _event(event: Any) -> dict[str, Any]:
    if isinstance(event, dict):
        return event
    dump = getattr(event, "model_dump", None)
    if callable(dump):
        value = dump(mode="json")
        if isinstance(value, dict):
            return cast(dict[str, Any], value)
    return {"value": str(event)}


def _last_output(events: list[dict[str, Any]]) -> dict[str, Any]:
    for event in reversed(events):
        content = event.get("content")
        if isinstance(content, dict):
            parts = content.get("parts")
            if isinstance(parts, list):
                for part in reversed(parts):
                    if isinstance(part, dict) and isinstance(part.get("text"), str):
                        text = part["text"]
                        try:
                            value = json.loads(text)
                        except json.JSONDecodeError:
                            return {"text": text}
                        return value if isinstance(value, dict) else {"value": value}
    raise ValueError("Agent Runtime returned no final structured output")
