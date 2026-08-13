import base64
from dataclasses import dataclass
from typing import Any

from connectors.base.errors import ConnectorError
from connectors.google import GoogleRestClient


@dataclass(frozen=True, slots=True)
class ComputerProposal:
    name: str
    arguments: dict[str, Any]
    requires_confirmation: bool
    safety_explanation: str | None
    response_content: dict[str, Any]


class ComputerUseClient:
    def __init__(
        self,
        client: GoogleRestClient,
        project_id: str,
        model: str = "gemini-3.5-flash",
        location: str = "global",
    ) -> None:
        self._client = client
        self._project = project_id
        self._model = model
        self._location = location
        self._contents: list[dict[str, Any]] = []

    async def propose(
        self,
        objective: str,
        screenshot: bytes,
        previous: ComputerProposal | None = None,
        outcome: dict[str, str | int | bool] | None = None,
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
        response = await self._client.request(
            "POST",
            (
                "https://aiplatform.googleapis.com/v1/projects/"
                f"{self._project}/locations/{self._location}/publishers/google/models/"
                f"{self._model}:generateContent"
            ),
            json={
                "contents": self._contents,
                "generationConfig": {"candidateCount": 1},
                "tools": [
                    {
                        "computerUse": {
                            "environment": "ENVIRONMENT_BROWSER",
                            "enablePromptInjectionDetection": True,
                            "excludedPredefinedFunctions": sorted(_EXCLUDED),
                        }
                    }
                ],
                "systemInstruction": {
                    "parts": [
                        {
                            "text": (
                                "Navigate only toward the stated objective. Never authenticate, "
                                "handle secrets, choose scopes, solve CAPTCHA or MFA, or execute "
                                "irreversible controls. FireKey validates every proposal."
                            )
                        }
                    ]
                },
            },
        )
        return _proposal(response)


def _proposal(response: dict[str, Any]) -> ComputerProposal | None:
    candidates = response.get("candidates")
    if not isinstance(candidates, list) or len(candidates) != 1:
        raise ConnectorError("computer-use-response", "Gemini returned no unique candidate")
    candidate = candidates[0]
    content = candidate.get("content") if isinstance(candidate, dict) else None
    parts = content.get("parts") if isinstance(content, dict) else None
    if not isinstance(parts, list):
        raise ConnectorError("computer-use-response", "Gemini candidate has no parts")
    if not isinstance(content, dict):
        raise ConnectorError("computer-use-response", "Gemini candidate content is invalid")
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
    explanation = safety.get("explanation") if isinstance(safety, dict) else None
    if explanation is not None and not isinstance(explanation, str):
        explanation = None
    return ComputerProposal(
        name=name,
        arguments=arguments,
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


_ALLOWED = frozenset({"click", "navigate", "press_key", "scroll", "type", "wait"})
_EXCLUDED = frozenset(
    {
        "double_click",
        "drag_and_drop",
        "go_back",
        "go_forward",
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
