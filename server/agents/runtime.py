import json
import re
from collections.abc import Callable
from datetime import datetime
from time import monotonic
from typing import Any

from connectors.base.errors import ConnectorError
from connectors.google import GoogleRestClient
from contracts import AgentRegistration, AgentResult, AgentTask
from telemetry import record

from agents.armor import ContentGuard, ModelArmorError
from agents.continuity import AgentContinuityService
from agents.fleet import AgentFleetService
from agents.redact import redact


class AgentRuntimeService:
    def __init__(
        self,
        fleet: AgentFleetService,
        continuity: AgentContinuityService,
        google: GoogleRestClient,
        guard: ContentGuard | None,
        clock: Callable[[], datetime],
    ) -> None:
        self._fleet = fleet
        self._continuity = continuity
        self._google = google
        self._guard = guard
        self._clock = clock

    async def execute(self, task: AgentTask) -> AgentResult:
        started = monotonic()
        evidence_ids = list(task.evidence_ids)
        try:
            registration = await self._fleet.resolve(task.organisation_id, task.agent, task.skill)
            task_context = redact(task.context)
            if not isinstance(task_context, dict):
                raise ValueError("agent task context redaction changed its object shape")
            try:
                session = await self._continuity.create_session(
                    registration,
                    f"session_{task.id}",
                    task.run_id,
                    f"{task.skill}: {task.objective[:160]}",
                    task_context,
                )
            except ConnectorError as error:
                raise _stage_error(error, "session-create") from error
            try:
                memories = await self._continuity.retrieve(registration, task.objective, count=5)
            except ConnectorError as error:
                raise _stage_error(error, "memory-retrieve") from error
            prompt = _prompt(task, memories)
            if self._guard is None:
                raise ConnectorError(
                    "model-armor-unconfigured",
                    "Managed agent content screening is not configured",
                    safe_detail="prompt",
                )
            try:
                evidence_ids.append(await self._guard.screen_prompt(task, prompt))
            except ModelArmorError as error:
                evidence_ids.append(error.evidence_id)
                raise _stage_error(error, "model-armor-prompt") from error
            try:
                response = await self._google.request(
                    "POST",
                    _a2a_endpoint(registration),
                    headers={"A2A-Version": "0.3"},
                    json={
                        "message": {
                            "messageId": task.id,
                            "contextId": session.remote_session.rsplit("/", 1)[-1],
                            "role": "1",
                            "content": [{"text": prompt}],
                            "metadata": {
                                "uumi_organisation_id": task.organisation_id,
                                "uumi_run_id": task.run_id,
                                "uumi_task_context": task_context,
                            },
                        },
                        "configuration": {"blocking": True},
                    },
                )
            except ConnectorError as error:
                raise _stage_error(error, "a2a-send") from error
            output = _a2a_output(_event(response))
            try:
                evidence_ids.append(
                    await self._guard.screen_response(
                        task,
                        json.dumps(output, separators=(",", ":"), sort_keys=True),
                    )
                )
            except ModelArmorError as error:
                evidence_ids.append(error.evidence_id)
                raise _stage_error(error, "model-armor-response") from error
            safe_output = redact(output)
            if not isinstance(safe_output, dict):
                raise ValueError("agent output redaction changed its object shape")
            result = AgentResult(
                task_id=task.id,
                agent=task.agent,
                skill=task.skill,
                succeeded=True,
                output=safe_output,
                evidence_ids=tuple(dict.fromkeys(evidence_ids)),
                completed_at=self._clock(),
            )
        except Exception as error:
            code = error.code if isinstance(error, ConnectorError) else type(error).__name__
            detail = (
                f".{error.safe_detail}"
                if isinstance(error, ConnectorError) and error.safe_detail
                else ""
            )
            result = AgentResult(
                task_id=task.id,
                agent=task.agent,
                skill=task.skill,
                succeeded=False,
                evidence_ids=tuple(dict.fromkeys(evidence_ids)),
                error=f"{code}{detail}: agent execution failed",
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


def _stage_error(error: ConnectorError, stage: str) -> ConnectorError:
    detail = stage if error.safe_detail is None else f"{stage}.{error.safe_detail}"
    return ConnectorError(
        error.code,
        "Managed agent stage failed",
        retryable=error.retryable,
        safe_detail=detail,
    )


def _prompt(task: AgentTask, memories: tuple[dict[str, Any], ...] = ()) -> str:
    safe = redact(
        {
            "task_id": task.id,
            "skill": task.skill,
            "objective": task.objective,
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
        state = status.get("state")
        if state in {
            "TASK_STATE_FAILED",
            "TASK_STATE_CANCELED",
            "TASK_STATE_REJECTED",
        }:
            raise ConnectorError(
                "agent-runtime-terminal",
                "Agent Runtime returned a terminal task failure",
            )
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


def _a2a_endpoint(registration: AgentRegistration) -> str:
    deployment = _runtime_deployment(registration)
    return (
        f"https://{registration.region}-aiplatform.googleapis.com/v1beta1/"
        f"{deployment}/a2a/v1/message:send"
    )


def _runtime_deployment(registration: AgentRegistration) -> str:
    identity = registration.identity.removeprefix("principal://")
    identity_match = re.fullmatch(
        r"agents\.global\.(?:org|project)-\d+\.system\.id\.goog/resources/aiplatform/"
        r"projects/(?P<project>\d+)/locations/(?P<region>[a-z0-9-]+)/"
        r"reasoningEngines/(?P<engine>\d+)",
        identity,
    )
    deployment_match = re.fullmatch(
        r"projects/[^/]+/locations/(?P<region>[a-z0-9-]+)/"
        r"reasoningEngines/(?P<engine>\d+)",
        registration.deployment,
    )
    if (
        identity_match is None
        or deployment_match is None
        or identity_match.group("region") != registration.region
        or deployment_match.group("region") != registration.region
        or identity_match.group("engine") != deployment_match.group("engine")
    ):
        raise ValueError("agent deployment does not match its managed identity and region")
    return (
        f"projects/{identity_match.group('project')}/locations/{registration.region}/"
        f"reasoningEngines/{identity_match.group('engine')}"
    )
