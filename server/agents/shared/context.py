from collections.abc import Mapping
from typing import Any

from core.errors import ResourceNotFoundError
from core.storage.paths import FirestorePaths
from google.adk.agents.context import Context as ToolContext
from google.cloud.firestore_v1 import AsyncClient

_SENSITIVE = frozenset(
    {
        "api_key",
        "authorization",
        "cookie",
        "credential",
        "password",
        "private_key",
        "secret",
        "token",
        "value",
    }
)


class AgentContext:
    def __init__(self, tool_context: ToolContext) -> None:
        self._state = tool_context.state
        self.organisation_id = self._required("organisation_id")
        self.run_id = self._required("run_id")
        project_id = self._required("project_id")
        database = self._state.get("firestore_database", "(default)")
        if not isinstance(database, str):
            raise ValueError("firestore_database must be a string")
        self.client = AsyncClient(project=project_id, database=database)

    async def document(self, path: str) -> dict[str, Any]:
        snapshot = await self.client.document(path).get()
        if not snapshot.exists:
            raise ResourceNotFoundError(f"agent context resource {path} was not found")
        data = snapshot.to_dict()
        if data is None:
            raise ResourceNotFoundError(f"agent context resource {path} has no data")
        redacted = redact(data)
        if not isinstance(redacted, dict):
            raise ValueError("agent context document did not remain an object")
        return redacted

    async def run(self) -> dict[str, Any]:
        return await self.document(FirestorePaths.run(self.organisation_id, self.run_id))

    def _required(self, name: str) -> str:
        value = self._state.get(name)
        if not isinstance(value, str) or not value:
            raise ValueError(f"managed session state is missing {name}")
        return value


def redact(value: Any, key: str = "") -> Any:
    if any(part in _SENSITIVE for part in key.lower().split("_")):
        return "[redacted]"
    if isinstance(value, Mapping):
        return {str(name): redact(item, str(name)) for name, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact(item) for item in value)
    if isinstance(value, bytes):
        return "[redacted]"
    return value
