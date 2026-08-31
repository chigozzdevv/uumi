import base64
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from connectors.base.errors import ConnectorError
from connectors.google import GoogleRestClient

MODEL_ID = "gemini-3.7-flash"


@dataclass(frozen=True, slots=True)
class ComputerProposal:
    name: str
    arguments: dict[str, Any]
    intent: str | None
    safety_decision: str | None
    requires_confirmation: bool
    safety_explanation: str | None
    response_content: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ModelStreamEvent:
    kind: str
    content: str


class ComputerUseClient:
    def __init__(
        self,
        client: GoogleRestClient,
        project_id: str,
        model_armor_template: str,
        model_armor_response_template: str | None = None,
        model: str = MODEL_ID,
        location: str = "global",
    ) -> None:
        self._client = client
        self._project = project_id
        self._model = model
        self._location = location
        self._model_armor_template = model_armor_template
        self._model_armor_response_template = model_armor_response_template or model_armor_template
        self._model_armor_region = _template_region(model_armor_template)
        if _template_region(self._model_armor_response_template) != self._model_armor_region:
            raise ValueError("Model Armor templates must share a region")
        self._contents: list[dict[str, Any]] = []

    @property
    def instruction(self) -> str:
        return _SYSTEM_INSTRUCTION

    async def propose(
        self,
        objective: str,
        screenshot: bytes,
        previous: ComputerProposal | None = None,
        outcome: dict[str, str | int | bool] | None = None,
        on_event: Callable[[ModelStreamEvent], Awaitable[None]] | None = None,
    ) -> ComputerProposal | None:
        await self._screen("prompt-text", objective, template=self._model_armor_response_template)
        if previous is None:
            self._contents = [
                {
                    "role": "user",
                    "parts": [
                        {"text": objective},
                        _image(screenshot),
                    ],
                }
            ]
        else:
            self._contents.append(previous.response_content)
            self._contents.append(
                {
                    "role": "user",
                    "parts": [
                        {
                            "functionResponse": {
                                "name": previous.name,
                                "response": outcome or {"status": "succeeded"},
                                "parts": [_image(screenshot)],
                            }
                        }
                    ],
                }
            )
        body = {
            "contents": self._contents,
            "generationConfig": {
                "thinkingConfig": {"includeThoughts": True},
            },
            "tools": [
                {
                    "computerUse": {
                        "environment": "ENVIRONMENT_BROWSER",
                        "enable_prompt_injection_detection": True,
                        "excludedPredefinedFunctions": sorted(_EXCLUDED),
                    }
                }
            ],
            "systemInstruction": {"parts": [{"text": _SYSTEM_INSTRUCTION}]},
        }
        base_url = (
            "https://aiplatform.googleapis.com/v1/projects/"
            f"{self._project}/locations/{self._location}/publishers/google/models/"
            f"{self._model}"
        )
        response = await self._client.request("POST", f"{base_url}:generateContent", json=body)
        content = _content(response)
        await self._screen(
            "response",
            json.dumps(content, separators=(",", ":"), sort_keys=True),
            associated_prompt=objective,
        )
        await _emit_visible(content, on_event)
        return _proposal(content)

    async def _screen(
        self,
        direction: str,
        content: str,
        *,
        associated_prompt: str | None = None,
        template: str | None = None,
    ) -> None:
        prompt = direction.startswith("prompt")
        selected_template = template or (
            self._model_armor_template if prompt else self._model_armor_response_template
        )
        body: dict[str, Any] = {
            "userPromptData" if prompt else "modelResponseData": {"text": content}
        }
        if not prompt:
            if not associated_prompt:
                raise ValueError("Model Armor response screening requires its prompt")
            body["userPrompt"] = associated_prompt
        response = await self._client.request(
            "POST",
            (
                f"https://modelarmor.{self._model_armor_region}.rep.googleapis.com/v1/"
                f"{selected_template}:"
                f"{'sanitizeUserPrompt' if prompt else 'sanitizeModelResponse'}"
            ),
            json=body,
        )
        self._validate_screen_result(direction, response)

    @staticmethod
    def _validate_screen_result(direction: str, response: dict[str, Any]) -> None:
        result = response.get("sanitizationResult")
        if not isinstance(result, dict):
            raise ConnectorError(
                "model-armor-invalid",
                "Model Armor returned no sanitization result",
                safe_detail=direction,
            )
        invocation = result.get("invocationResult")
        match = result.get("filterMatchState")
        if invocation != "SUCCESS" or match not in {"MATCH_FOUND", "NO_MATCH_FOUND"}:
            raise ConnectorError(
                "model-armor-invalid",
                "Model Armor did not return a conclusive decision",
                safe_detail=direction,
            )
        if match == "MATCH_FOUND":
            filters = _matched_filters(result)
            raise ConnectorError(
                "model-armor-blocked",
                "Model Armor blocked browser model content",
                safe_detail=f"{direction}:{','.join(filters) or 'unknown'}",
            )


def _content(response: dict[str, Any]) -> dict[str, Any]:
    candidates = response.get("candidates")
    if not isinstance(candidates, list) or len(candidates) != 1:
        raise ConnectorError("computer-use-response", "Gemini returned no unique candidate")
    candidate = candidates[0]
    content = candidate.get("content") if isinstance(candidate, dict) else None
    if not isinstance(content, dict):
        raise ConnectorError("computer-use-response", "Gemini candidate content is invalid")
    return content


def _matched_filters(result: dict[str, Any]) -> tuple[str, ...]:
    raw = result.get("filterResults")
    if not isinstance(raw, dict):
        return ()
    return tuple(
        sorted(
            name for name, value in raw.items() if isinstance(name, str) and _contains_match(value)
        )
    )


def _contains_match(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            (key == "matchState" and item == "MATCH_FOUND") or _contains_match(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_match(item) for item in value)
    return False


async def _emit_visible(
    content: dict[str, Any],
    on_event: Callable[[ModelStreamEvent], Awaitable[None]] | None,
) -> None:
    if on_event is None:
        return
    parts = content.get("parts")
    if not isinstance(parts, list):
        return
    for part in parts:
        if not isinstance(part, dict):
            continue
        value = part.get("text")
        if not isinstance(value, str) or not value:
            continue
        await on_event(
            ModelStreamEvent(
                kind="thought" if part.get("thought") is True else "response",
                content=value,
            )
        )


def _proposal(content: dict[str, Any]) -> ComputerProposal | None:
    parts = content.get("parts")
    if not isinstance(parts, list):
        raise ConnectorError("computer-use-response", "Gemini candidate has no parts")
    calls: list[dict[str, Any]] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        call = part.get("functionCall")
        if isinstance(call, dict):
            calls.append(call)
    if not calls:
        return None
    if len(calls) != 1:
        raise ConnectorError(
            "computer-use-parallel-action", "Uumi executes one browser proposal at a time"
        )
    call = calls[0]
    name = call.get("name")
    arguments = call.get("args", {})
    if not isinstance(name, str) or name not in _ALLOWED or not isinstance(arguments, dict):
        raise ConnectorError("computer-use-action", "Gemini proposed an unsupported browser action")
    safety = arguments.get("safety_decision") or arguments.get("safetyDecision")
    decision = safety.get("decision") if isinstance(safety, dict) else None
    raw_explanation = safety.get("explanation") if isinstance(safety, dict) else None
    explanation = raw_explanation if isinstance(raw_explanation, str) else None
    intent = arguments.get("intent")
    return ComputerProposal(
        name=name,
        arguments=arguments,
        intent=intent if isinstance(intent, str) else None,
        safety_decision=decision if isinstance(decision, str) else None,
        requires_confirmation=decision == "require_confirmation",
        safety_explanation=explanation,
        response_content=content,
    )


def _image(value: bytes) -> dict[str, Any]:
    return {
        "inlineData": {
            "mimeType": "image/png",
            "data": base64.b64encode(value).decode(),
        }
    }


def _template_region(template: str) -> str:
    match = re.fullmatch(
        r"projects/[a-z0-9-]+/locations/(?P<region>[a-z0-9-]+)/templates/[A-Za-z0-9_-]+",
        template,
    )
    if match is None:
        raise ValueError("Model Armor templates must be full regional resource names")
    return match.group("region")


_ALLOWED = frozenset({"click", "press_key", "scroll", "type", "wait"})
_SYSTEM_INSTRUCTION = (
    "Navigate only toward the stated objective. Never authenticate, handle secrets, choose "
    "scopes, solve CAPTCHA or MFA, or execute irreversible controls. Uumi validates every "
    "proposal."
)
_EXCLUDED = frozenset(
    {
        "double_click",
        "drag_and_drop",
        "hotkey",
        "key_down",
        "key_up",
        "middle_click",
        "mouse_down",
        "mouse_up",
        "move",
        "right_click",
        "take_screenshot",
        "triple_click",
    }
)
