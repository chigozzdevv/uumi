from copy import deepcopy
from typing import Any

from google.adk.agents.context import Context as ToolContext

from agents.redact import redact


class AgentContext:
    def __init__(self, tool_context: ToolContext) -> None:
        self._state = tool_context.state
        self.organisation_id = self._required("organisation_id")
        self.run_id = self._required("run_id")
        task_context = self._state.get("task_context")
        if not isinstance(task_context, dict):
            raise ValueError("managed session state is missing task_context")
        safe = redact(deepcopy(task_context))
        if not isinstance(safe, dict):
            raise ValueError("managed task context did not remain an object")
        self._context = safe

    def object(self, name: str, *, required: bool = True) -> dict[str, Any] | None:
        value = self._context.get(name)
        if value is None and not required:
            return None
        if not isinstance(value, dict):
            raise ValueError(f"managed task context {name} must be an object")
        return deepcopy(value)

    def objects(self, name: str) -> tuple[dict[str, Any], ...]:
        value = self._context.get(name, ())
        if not isinstance(value, (list, tuple)) or any(
            not isinstance(item, dict) for item in value
        ):
            raise ValueError(f"managed task context {name} must be an object list")
        return tuple(deepcopy(item) for item in value)

    def value(self, name: str) -> Any:
        return deepcopy(self._context.get(name))

    def _required(self, name: str) -> str:
        value = self._state.get(name)
        if not isinstance(value, str) or not value:
            raise ValueError(f"managed session state is missing {name}")
        return value
