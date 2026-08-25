import json
from collections.abc import Callable
from datetime import datetime
from time import monotonic
from typing import Any
from urllib.parse import quote

from connectors.base.errors import ConnectorError
from connectors.google import GoogleRestClient
from contracts import AgentResult, AgentTask
from telemetry import record

from agents.continuity import AgentContinuityService
from agents.fleet import AgentFleetService
from agents.redact import redact


class AgentRuntimeService:
    def __init__(
        self,
        fleet: AgentFleetService,
        continuity: AgentContinuityService,
        google: GoogleRestClient,
        project_id: str,
        clock: Callable[[], datetime],
    ) -> None:
        self._fleet = fleet
        self._continuity = continuity
        self._google = google
        self._project = project_id
        self._clock = clock

    async def execute(self, task: AgentTask) -> AgentResult:
        started = monotonic()
        try:
            registration = await self._fleet.resolve(task.organisation_id, task.agent, task.skill)
            session = await self._continuity.create_session(
                registration,
                f"session_{task.id}",
                task.run_id,
                f"{task.skill}: {task.objective[:160]}",
            )
            memories = await self._continuity.retrieve(registration, task.objective, count=5)
            response = await self._google.request(
                "POST",
                _a2a_endpoint(
                    registration.region,
                    registration.deployment,
                    task.organisation_id,
                ),
                headers={"A2A-Version": "1.0"},
                json={
                    "message": {
                        "messageId": task.id,
                        "contextId": session.remote_session.rsplit("/", 1)[-1],
                        "role": "ROLE_USER",
                        "parts": [{"text": _prompt(task, memories)}],
                        "metadata": {"uumi_organisation_id": task.organisation_id},
                    },
                    "tenant": task.organisation_id,
                },
            )
            event = redact(_event(response))
            if not isinstance(event, dict):
                raise ValueError("agent response redaction changed its object shape")
            output = _a2a_output(event)
            safe_output = redact(output)
            if not isinstance(safe_output, dict):
                raise ValueError("agent output redaction changed its object shape")
            result = AgentResult(
                task_id=task.id,
                agent=task.agent,
                skill=task.skill,
                succeeded=True,
                output=safe_output,
                evidence_ids=task.evidence_ids,
                completed_at=self._clock(),
            )
        except Exception as error:
            code = error.code if isinstance(error, ConnectorError) else type(error).__name__
            result = AgentResult(
                task_id=task.id,
                agent=task.agent,
                skill=task.skill,
                succeeded=False,
                error=f"{code}: agent execution failed",
                completed_at=self._clock(),
            )
        record(
            "agent.invoke",
            "succeeded" if result.succeeded else "failed",
            monotonic() - started,
            agent=task.agent.value,
            skill=task.skill,
        )
        return result


def _prompt(task: AgentTask, memories: tuple[dict[str, Any], ...] = ()) -> str:
    safe = redact(
        {
            "task_id": task.id,
            "skill": task.skill,
            "objective": task.objective,
            "context": task.context,
            "evidence_ids": task.evidence_ids,
            "approved_memory": memories,
        }
    )
    return json.dumps(
        safe,
        separators=(",", ":"),
        sort_keys=True,
    )


def _event(event: Any) -> dict[str, Any]:
    if isinstance(event, dict):
        return event
    return {"value": str(event)}


def _a2a_output(response: dict[str, Any]) -> dict[str, Any]:
    payload = response.get("task", response.get("message", response))
    if not isinstance(payload, dict):
        raise ValueError("Agent Runtime returned an invalid A2A payload")
    texts: list[str] = []
    _collect_text(payload.get("artifacts"), texts)
    status = payload.get("status")
    if isinstance(status, dict):
        _collect_text(status.get("message"), texts)
    _collect_text(payload.get("history"), texts)
    _collect_text(payload.get("parts"), texts)
    for text in reversed(texts):
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ValueError("Agent Runtime returned no final structured A2A output")


def _collect_text(value: Any, output: list[str]) -> None:
    if isinstance(value, dict):
        text = value.get("text")
        if isinstance(text, str):
            output.append(text)
        for nested in value.values():
            _collect_text(nested, output)
    elif isinstance(value, list):
        for nested in value:
            _collect_text(nested, output)


def _a2a_endpoint(region: str, deployment: str, tenant: str) -> str:
    expected = f"projects/{deployment.split('/')[1]}/locations/{region}/reasoningEngines/"
    if not deployment.startswith(expected):
        raise ValueError("agent deployment does not match its registered project and region")
    return (
        f"https://{region}-aiplatform.googleapis.com/v1beta1/{deployment}/a2a/"
        f"{quote(tenant, safe='')}/message:send"
    )
