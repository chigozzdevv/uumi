from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from contracts import WalkthroughAnalysis, WalkthroughSource, WalkthroughStatus

from core.errors import PlaybookError


class WalkthroughRepository(Protocol):
    async def reserve(self, source: WalkthroughSource) -> tuple[WalkthroughSource, bool]: ...

    async def get(
        self,
        organisation_id: str,
        playbook_id: str,
        source_id: str,
    ) -> WalkthroughSource: ...

    async def replace(
        self,
        current: WalkthroughSource,
        changed: WalkthroughSource,
    ) -> WalkthroughSource: ...


class UploadProvider(Protocol):
    async def begin(
        self,
        object_name: str,
        content_type: str,
        size: int,
        crc32c: str,
    ) -> str: ...

    async def verify(
        self,
        object_name: str,
        content_type: str,
        size: int,
        crc32c: str,
    ) -> str: ...


class VideoProvider(Protocol):
    async def start(self, resource: str) -> str: ...

    async def result(
        self,
        operation: str,
        source_id: str,
        now: datetime,
    ) -> WalkthroughAnalysis | None: ...


class WalkthroughService:
    def __init__(
        self,
        repository: WalkthroughRepository,
        uploads: UploadProvider,
        video: VideoProvider,
        bucket: str,
        clock: Callable[[], datetime],
    ) -> None:
        self._repository = repository
        self._uploads = uploads
        self._video = video
        self._bucket = bucket
        self._clock = clock

    async def begin(
        self,
        organisation_id: str,
        playbook_id: str,
        source_id: str,
        content_type: str,
        size: int,
        crc32c: str,
        actor_id: str,
    ) -> tuple[WalkthroughSource, str, bool]:
        now = self._clock()
        object_name = (
            f"organisations/{organisation_id}/playbooks/{playbook_id}/"
            f"walkthroughs/{source_id}/video"
        )
        candidate = WalkthroughSource(
            id=source_id,
            organisation_id=organisation_id,
            playbook_id=playbook_id,
            object_name=object_name,
            resource=f"gs://{self._bucket}/{object_name}",
            content_type=content_type,
            size=size,
            crc32c=crc32c,
            status=WalkthroughStatus.UPLOADING,
            created_by=actor_id,
            created_at=now,
            updated_at=now,
        )
        source, created = await self._repository.reserve(candidate)
        if source.status is not WalkthroughStatus.UPLOADING:
            raise PlaybookError("walkthrough upload is already complete")
        session = await self._uploads.begin(object_name, content_type, size, crc32c)
        return source, session, created

    async def complete(
        self,
        organisation_id: str,
        playbook_id: str,
        source_id: str,
    ) -> WalkthroughSource:
        source = await self._repository.get(organisation_id, playbook_id, source_id)
        if source.status is not WalkthroughStatus.UPLOADING:
            return source
        generation = await self._uploads.verify(
            source.object_name,
            source.content_type,
            source.size,
            source.crc32c,
        )
        immutable = f"{source.resource}#{generation}"
        operation = await self._video.start(source.resource)
        changed = source.model_copy(
            update={
                "resource": immutable,
                "status": WalkthroughStatus.ANALYSING,
                "operation": operation,
                "updated_at": self._clock(),
                "revision": source.revision + 1,
            }
        )
        return await self._repository.replace(source, changed)

    async def refresh(
        self,
        organisation_id: str,
        playbook_id: str,
        source_id: str,
    ) -> WalkthroughSource:
        source = await self._repository.get(organisation_id, playbook_id, source_id)
        if source.status is not WalkthroughStatus.ANALYSING:
            return source
        if source.operation is None:
            raise PlaybookError("walkthrough analysis operation is missing")
        analysis = await self._video.result(source.operation, source.id, self._clock())
        if analysis is None:
            return source
        changed = source.model_copy(
            update={
                "status": WalkthroughStatus.READY,
                "analysis": analysis,
                "updated_at": self._clock(),
                "revision": source.revision + 1,
            }
        )
        return await self._repository.replace(source, changed)

    async def ready(
        self,
        organisation_id: str,
        playbook_id: str,
        source_ids: tuple[str, ...],
    ) -> tuple[WalkthroughSource, ...]:
        values = []
        for source_id in source_ids:
            source = await self.refresh(organisation_id, playbook_id, source_id)
            if source.status is not WalkthroughStatus.READY or source.analysis is None:
                raise PlaybookError(f"walkthrough {source_id} analysis is not ready")
            values.append(source)
        return tuple(values)
