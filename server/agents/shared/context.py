import asyncio
from typing import Any

from core.errors import ResourceNotFoundError
from core.storage.paths import FirestorePaths
from google.adk.agents.context import Context as ToolContext
from google.cloud.firestore_v1.base_query import FieldFilter

from agents.redact import redact
from agents.shared.firestore import rest_client


class AgentContext:
    def __init__(self, tool_context: ToolContext) -> None:
        self._state = tool_context.state
        self.organisation_id = self._required("organisation_id")
        self.run_id = self._required("run_id")
        project_id = self._required("project_id")
        database = self._state.get("firestore_database", "(default)")
        if not isinstance(database, str):
            raise ValueError("firestore_database must be a string")
        self._client = rest_client(project_id, database)

    async def document(self, path: str) -> dict[str, Any]:
        snapshot = await asyncio.to_thread(self._client.document(path).get)
        if not snapshot.exists:
            raise ResourceNotFoundError(f"agent context resource {path} was not found")
        data = snapshot.to_dict()
        if data is None:
            raise ResourceNotFoundError(f"agent context resource {path} has no data")
        redacted = redact(data)
        if not isinstance(redacted, dict):
            raise ValueError("agent context document did not remain an object")
        return redacted

    async def collection(self, name: str, field: str, value: str) -> tuple[dict[str, Any], ...]:
        query = self._client.collection(name).where(filter=FieldFilter(field, "==", value))
        snapshots = await asyncio.to_thread(lambda: list(query.stream()))
        values = []
        for snapshot in snapshots:
            data = snapshot.to_dict()
            if data is None:
                continue
            safe = redact(data)
            if not isinstance(safe, dict):
                raise ValueError("agent context collection item did not remain an object")
            values.append(safe)
        return tuple(values)

    async def run(self) -> dict[str, Any]:
        return await self.document(FirestorePaths.run(self.organisation_id, self.run_id))

    def _required(self, name: str) -> str:
        value = self._state.get(name)
        if not isinstance(value, str) or not value:
            raise ValueError(f"managed session state is missing {name}")
        return value
