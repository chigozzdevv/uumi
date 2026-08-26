import asyncio
from typing import Any, cast

from a2a.server.context import ServerCallContext
from a2a.server.tasks import TaskStore
from a2a.types import ListTasksRequest, ListTasksResponse, Task, TaskState
from core.storage.paths import FirestorePaths
from google.cloud.firestore_v1 import AsyncClient, Client
from google.cloud.firestore_v1.base_query import FieldFilter
from google.protobuf.json_format import MessageToDict, ParseDict  # type: ignore[import-untyped]

from agents.shared.firestore import rest_client


class FirestoreTaskStore(TaskStore):
    def __init__(self, client: AsyncClient | None = None) -> None:
        self._client: Any
        if client is not None:
            self._client = client
            self._synchronous = False
        else:
            self._client = rest_client(
                project=_environment("GOOGLE_CLOUD_PROJECT", "uumi-local"),
                database=_environment("FIRESTORE_DATABASE", "(default)"),
            )
            self._synchronous = True

    async def save(self, task: Task, context: ServerCallContext) -> None:
        owner = _owner(context)
        payload = {
            "owner": owner,
            "task": MessageToDict(task, preserving_proto_field_name=False),
            "context_id": task.context_id,
            "status": TaskState.Name(task.status.state),
        }
        document = self._client.document(_path(owner, task.id))
        if self._synchronous:
            await asyncio.to_thread(cast(Client, self._client).document(document.path).set, payload)
        else:
            await document.set(payload)

    async def get(self, task_id: str, context: ServerCallContext) -> Task | None:
        document = self._client.document(_path(_owner(context), task_id))
        snapshot = (
            await asyncio.to_thread(cast(Client, self._client).document(document.path).get)
            if self._synchronous
            else await document.get()
        )
        if not snapshot.exists:
            return None
        return _task(snapshot.to_dict())

    async def list(self, params: ListTasksRequest, context: ServerCallContext) -> ListTasksResponse:
        query: Any = self._client.collection(_collection(_owner(context)))
        if params.context_id:
            query = (
                query.where(filter=FieldFilter("context_id", "==", params.context_id))
                if self._synchronous
                else query.where("context_id", "==", params.context_id)
            )
        if params.status:
            query = (
                query.where(filter=FieldFilter("status", "==", TaskState.Name(params.status)))
                if self._synchronous
                else query.where("status", "==", TaskState.Name(params.status))
            )
        values = []
        limited = query.limit(params.page_size or 100)
        snapshots = (
            await asyncio.to_thread(lambda: list(limited.stream()))
            if self._synchronous
            else [snapshot async for snapshot in limited.stream()]
        )
        for snapshot in snapshots:
            task = _task(snapshot.to_dict())
            if task is not None:
                values.append(task)
        return ListTasksResponse(
            tasks=values,
            page_size=params.page_size or 100,
            total_size=len(values),
        )

    async def delete(self, task_id: str, context: ServerCallContext) -> None:
        document = self._client.document(_path(_owner(context), task_id))
        if self._synchronous:
            await asyncio.to_thread(cast(Client, self._client).document(document.path).delete)
        else:
            await document.delete()


def _owner(context: ServerCallContext) -> str:
    organisation_id = context.tenant
    if not organisation_id:
        raise ValueError("A2A task access requires the Uumi organisation tenant")
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
