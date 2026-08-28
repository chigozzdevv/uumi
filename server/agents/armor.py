import hashlib
import json
import re
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any, Protocol

from connectors.base.errors import ConnectorError
from connectors.google import GoogleRestClient
from contracts import AgentTask, Evidence


class EvidenceSink(Protocol):
    async def store(
        self,
        organisation_id: str,
        run_id: str,
        kind: str,
        content: bytes,
        content_type: str,
        now: datetime,
    ) -> Evidence: ...


class ContentGuard(Protocol):
    async def screen_prompt(self, task: AgentTask, content: str) -> str: ...

    async def screen_response(
        self,
        task: AgentTask,
        content: str,
        associated_prompt: str,
    ) -> str: ...


class ModelArmorError(ConnectorError):
    def __init__(
        self,
        code: str,
        message: str,
        evidence_id: str,
        *,
        retryable: bool = False,
        safe_detail: str | None = None,
    ) -> None:
        super().__init__(code, message, retryable=retryable, safe_detail=safe_detail)
        self.evidence_id = evidence_id


class ModelArmorGuard:
    def __init__(
        self,
        client: GoogleRestClient,
        prompt_template: str,
        evidence: EvidenceSink,
        clock: Callable[[], datetime],
        response_template: str | None = None,
    ) -> None:
        prompt_match = re.fullmatch(
            r"projects/[a-z0-9-]+/locations/(?P<region>[a-z0-9-]+)/"
            r"templates/[A-Za-z0-9_-]+",
            prompt_template,
        )
        response_template = response_template or prompt_template
        response_match = re.fullmatch(
            r"projects/[a-z0-9-]+/locations/(?P<region>[a-z0-9-]+)/"
            r"templates/[A-Za-z0-9_-]+",
            response_template,
        )
        if prompt_match is None or response_match is None:
            raise ValueError("Model Armor templates must be full regional resource names")
        if prompt_match.group("region") != response_match.group("region"):
            raise ValueError("Model Armor prompt and response templates must share a region")
        self._client = client
        self._prompt_template = prompt_template
        self._response_template = response_template
        self._region = prompt_match.group("region")
        self._evidence = evidence
        self._clock = clock

    async def screen_prompt(self, task: AgentTask, content: str) -> str:
        return await self._screen(task, content, "prompt")

    async def screen_response(
        self,
        task: AgentTask,
        content: str,
        associated_prompt: str,
    ) -> str:
        return await self._screen(
            task,
            content,
            "response",
            associated_prompt=associated_prompt,
        )

    async def _screen(
        self,
        task: AgentTask,
        content: str,
        direction: str,
        *,
        associated_prompt: str | None = None,
    ) -> str:
        method = "sanitizeUserPrompt" if direction == "prompt" else "sanitizeModelResponse"
        field = "userPromptData" if direction == "prompt" else "modelResponseData"
        template = self._prompt_template if direction == "prompt" else self._response_template
        request_body: dict[str, Any] = {field: {"text": content}}
        if direction == "response":
            if not associated_prompt:
                raise ValueError("Model Armor response screening requires its associated prompt")
            request_body["userPrompt"] = associated_prompt
        try:
            response = await self._client.request(
                "POST",
                f"https://modelarmor.{self._region}.rep.googleapis.com/v1/{template}:{method}",
                json=request_body,
            )
        except ConnectorError as error:
            evidence_id = await self._store(
                task,
                direction,
                content,
                "ERROR",
                "INVOCATION_FAILED",
                {},
                error.code,
            )
            raise ModelArmorError(
                "model-armor-unavailable",
                "Model Armor did not complete content screening",
                evidence_id,
                retryable=error.retryable,
                safe_detail=direction,
            ) from error

        result = response.get("sanitizationResult")
        if not isinstance(result, Mapping):
            evidence_id = await self._store(
                task,
                direction,
                content,
                "ERROR",
                "INVALID_RESPONSE",
                {},
                "missing-sanitization-result",
            )
            raise ModelArmorError(
                "model-armor-invalid",
                "Model Armor returned no sanitization result",
                evidence_id,
                safe_detail=direction,
            )

        match_state = result.get("filterMatchState")
        invocation = result.get("invocationResult")
        states = _filter_states(result.get("filterResults"))
        if invocation != "SUCCESS" or match_state not in {"MATCH_FOUND", "NO_MATCH_FOUND"}:
            evidence_id = await self._store(
                task,
                direction,
                content,
                "ERROR",
                str(invocation or "INVOCATION_UNSPECIFIED"),
                states,
                str(match_state or "MATCH_STATE_UNSPECIFIED"),
                template,
            )
            raise ModelArmorError(
                "model-armor-invalid",
                "Model Armor did not return a conclusive decision",
                evidence_id,
                safe_detail=direction,
            )

        decision = "BLOCK" if match_state == "MATCH_FOUND" else "ALLOW"
        evidence_id = await self._store(
            task,
            direction,
            content,
            decision,
            invocation,
            states,
            match_state,
            template,
        )
        if decision == "BLOCK":
            raise ModelArmorError(
                "model-armor-blocked",
                "Model Armor blocked agent content",
                evidence_id,
                safe_detail=direction,
            )
        return evidence_id

    async def _store(
        self,
        task: AgentTask,
        direction: str,
        content: str,
        decision: str,
        invocation: str,
        states: dict[str, str],
        outcome: str,
        template: str | None = None,
    ) -> str:
        now = self._clock()
        payload = json.dumps(
            {
                "schema": "uumi.model-armor.v1",
                "task_id": task.id,
                "agent": task.agent.value,
                "skill": task.skill,
                "direction": direction,
                "decision": decision,
                "invocation_result": invocation,
                "outcome": outcome,
                "filter_states": states,
                "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
                "template": template
                or (self._prompt_template if direction == "prompt" else self._response_template),
                "recorded_at": now.isoformat(),
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        evidence = await self._evidence.store(
            task.organisation_id,
            task.run_id,
            f"model-armor-{direction}",
            payload,
            "application/json",
            now,
        )
        return evidence.id


def _filter_states(value: Any, path: str = "") -> dict[str, str]:
    states: dict[str, str] = {}
    if isinstance(value, Mapping):
        for key, nested in value.items():
            current = f"{path}.{key}" if path else str(key)
            if key in {"confidenceLevel", "executionState", "matchState"} and isinstance(
                nested, str
            ):
                states[current] = nested
            else:
                states.update(_filter_states(nested, current))
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            states.update(_filter_states(nested, f"{path}[{index}]"))
    return dict(sorted(states.items()))
