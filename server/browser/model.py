import base64
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from connectors.base.errors import ConnectorError
from connectors.google import GoogleRestClient


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
        model: str = "gemini-3.7-flash",
        location: str = "global",
    ) -> None:
        self._client = client
        self._project = project_id
        self._model = model
        self._location = location
        self._model_armor_template = model_armor_template
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
                "candidateCount": 1,
                "thinkingConfig": {"includeThoughts": True},
            },
            "modelArmorConfig": {
                "promptTemplateName": self._model_armor_template,
                "responseTemplateName": self._model_armor_template,
            },
            "tools": [
                {
                    "computerUse": {
                        "environment": "ENVIRONMENT_BROWSER",
                        "excludedPredefinedFunctions": sorted(_EXCLUDED),
                        "enablePromptInjectionDetection": True,
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
        stream = getattr(self._client, "stream", None)
        if stream is None:
            response = await self._client.request("POST", f"{base_url}:generateContent", json=body)
            content = _content(response)
            await _emit_visible(content, on_event)
            return _proposal(content)

        parts: list[dict[str, Any]] = []
        async for response in stream(
            "POST",
            f"{base_url}:streamGenerateContent",
            json=body,
            params={"alt": "sse"},
        ):
            content = _content(response)
            chunk_parts = content.get("parts")
            if not isinstance(chunk_parts, list):
                continue
            safe_parts = [part for part in chunk_parts if isinstance(part, dict)]
            parts.extend(safe_parts)
            await _emit_visible({"role": "model", "parts": safe_parts}, on_event)
        if not parts:
            raise ConnectorError("computer-use-response", "Gemini returned no response parts")
        return _proposal({"role": "model", "parts": parts})


def _content(response: dict[str, Any]) -> dict[str, Any]:
    candidates = response.get("candidates")
    if not isinstance(candidates, list) or len(candidates) != 1:
        raise ConnectorError("computer-use-response", "Gemini returned no unique candidate")
    candidate = candidates[0]
    content = candidate.get("content") if isinstance(candidate, dict) else None
    if not isinstance(content, dict):
        raise ConnectorError("computer-use-response", "Gemini candidate content is invalid")
    return content


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
            "computer-use-parallel-action", "FireKey executes one browser proposal at a time"
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


_ALLOWED = frozenset({"click", "press_key", "scroll", "type", "wait"})
_SYSTEM_INSTRUCTION = (
    "Navigate only toward the stated objective. Never authenticate, handle secrets, choose "
    "scopes, solve CAPTCHA or MFA, or execute irreversible controls. FireKey validates every "
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
