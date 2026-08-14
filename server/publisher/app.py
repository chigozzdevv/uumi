import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from time import monotonic
from uuid import uuid4

from core.events import EventPublisher
from core.events.pubsub import PubSubEventTransport
from core.storage import FirestoreOutboxRepository
from fastapi import FastAPI
from google.cloud.firestore_v1 import AsyncClient
from pydantic import BaseModel
from telemetry import instrument, record

from publisher.config import PublisherSettings


class PublishResponse(BaseModel):
    claimed: int
    published: int
    failed: int


class PublisherRuntime:
    def __init__(self, settings: PublisherSettings) -> None:
        self.settings = settings
        self.firestore = AsyncClient(
            project=settings.project_id,
            database=settings.firestore_database,
        )
        self.repository = FirestoreOutboxRepository(self.firestore)
        self.transport = PubSubEventTransport(
            settings.project_id,
            settings.event_topic,
            settings.region,
            settings.publish_timeout_seconds,
        )
        self.instance_id = os.getenv("K_REVISION", "publisher")

    async def publish(self) -> PublishResponse:
        started = monotonic()
        owner_id = f"{self.instance_id}-{uuid4().hex}"
        publisher = EventPublisher(
            self.repository,
            self.transport,
            owner_id,
            clock=lambda: datetime.now(UTC),
            lease_duration=timedelta(seconds=self.settings.outbox_lease_seconds),
            batch_size=self.settings.publish_batch_size,
        )
        result = await publisher.drain(self.settings.publish_max_events)
        response = PublishResponse(
            claimed=result.claimed,
            published=result.published,
            failed=result.failed,
        )
        record(
            "event.publish",
            "failed" if response.failed else "succeeded",
            monotonic() - started,
        )
        return response

    def close(self) -> None:
        self.transport.close()
        self.firestore.close()  # type: ignore[no-untyped-call]


_runtime: PublisherRuntime | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    del app
    global _runtime
    runtime = PublisherRuntime(PublisherSettings())
    _runtime = runtime
    try:
        yield
    finally:
        runtime.close()
        _runtime = None


app = FastAPI(title="FireKey Event Publisher", version="0.1.0", lifespan=lifespan)
instrument(app, "firekey-publisher")


@app.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/publish", response_model=PublishResponse)
async def publish() -> PublishResponse:
    if _runtime is None:
        raise RuntimeError("publisher runtime is not initialised")
    return await _runtime.publish()
