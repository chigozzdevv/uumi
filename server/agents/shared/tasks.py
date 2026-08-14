from typing import Any, cast

from a2a.server.context import ServerCallContext
from a2a.server.tasks import TaskStore
from a2a.types import ListTasksRequest, ListTasksResponse, Task, TaskState
from core.storage.paths import FirestorePaths
from google.cloud.firestore_v1 import AsyncClient
from google.protobuf.json_format import MessageToDict, ParseDict  # type: ignore[import-untyped]


class FirestoreTaskStore(TaskStore):
    def __init__(self) -> None:
        self._client = AsyncClient(
            project=_environment("GOOGLE_CLOUD_PROJECT"),
            database=_environment("FIRESTORE_DATABASE", "(default)"),
        )

    async def save(self, task: Task, context: ServerCallContext) -> None:
        owner = _owner(context)
        await self._client.document(_path(owner, task.id)).set(
            {
                "owner": owner,
                "task": MessageToDict(task, preserving_proto_field_name=False),
                "context_id": task.context_id,
                "status": TaskState.Name(task.status.state),
            }
        )

    async def get(self, task_id: str, context: ServerCallContext) -> Task | None:
        snapshot = await self._client.document(_path(_owner(context), task_id)).get()
        if not snapshot.exists:
            return None
        return _task(snapshot.to_dict())

    async def list(self, params: ListTasksRequest, context: ServerCallContext) -> ListTasksResponse:
        query: Any = self._client.collection(_collection(_owner(context)))
        if params.context_id:
            query = query.where("context_id", "==", params.context_id)
        if params.status:
            query = query.where("status", "==", TaskState.Name(params.status))
        values = []
        async for snapshot in query.limit(params.page_size or 100).stream():
            task = _task(snapshot.to_dict())
            if task is not None:
                values.append(task)
        return ListTasksResponse(
            tasks=values,
            page_size=params.page_size or 100,
            total_size=len(values),
        )

    async def delete(self, task_id: str, context: ServerCallContext) -> None:
        await self._client.document(_path(_owner(context), task_id)).delete()


def _owner(context: ServerCallContext) -> str:
    organisation_id = context.tenant
    if not organisation_id:
        raise ValueError("A2A task access requires the FireKey organisation tenant")
    return organisation_id


def _collection(organisation_id: str) -> str:
    return f"{FirestorePaths.organisation(organisation_id)}/agent-tasks"


def _path(organisation_id: str, task_id: str) -> str:
    return f"{_collection(organisation_id)}/{task_id}"


def _task(value: dict[str, Any] | None) -> Task | None:
    raw = value.get("task") if value is not None else None
    if not isinstance(raw, dict):
        return None
    return cast(Task, ParseDict(raw, Task()))


def _environment(name: str, default: str | None = None) -> str:
    import os

    value = os.environ.get(name, default)
    if not value:
        raise RuntimeError(f"managed agent environment is missing {name}")
    return value
