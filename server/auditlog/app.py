import base64
import binascii
import hashlib
import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
from uuid import uuid4

from connectors.google import GoogleRestClient
from connectors.logging import CloudLoggingConnector
from contracts import Contract, RunEvent
from core.audit import AuditPublisher, AuditWriter
from core.auth import GoogleTokenVerifier
from core.errors import AuthenticationError
from core.storage import FirestoreAuditOutboxRepository, FirestoreAuditRepository
from fastapi import FastAPI, Header, HTTPException, Request, status
from google.cloud.firestore_v1 import AsyncClient

from auditlog.config import AuditLogSettings


class DrainResponse(Contract):
    claimed: int
    logged: int
    failed: int


class EventResponse(Contract):
    audit_id: str


class Runtime:
    def __init__(self, settings: AuditLogSettings) -> None:
        self.settings = settings
        self.firestore = AsyncClient(
            project=settings.project_id, database=settings.firestore_database
        )
        self.google = GoogleRestClient()
        self.repository = FirestoreAuditOutboxRepository(self.firestore)
        self.writer = AuditWriter(
            FirestoreAuditRepository(self.firestore), settings.region, lambda: datetime.now(UTC)
        )
        self.transport = CloudLoggingConnector(self.google, settings.project_id)
        self.tokens = GoogleTokenVerifier(settings.oidc_audience)
        self.instance = os.getenv("K_REVISION", "auditlog")

    async def drain(self) -> DrainResponse:
        publisher = AuditPublisher(
            self.repository,
            self.transport,
            f"{self.instance}-{uuid4().hex}",
            lambda: datetime.now(UTC),
            timedelta(seconds=self.settings.lease_seconds),
            self.settings.batch_size,
        )
        summary = await publisher.drain(self.settings.maximum_events)
        return DrainResponse(
            claimed=summary.claimed,
            logged=summary.logged,
            failed=summary.failed,
        )

    async def close(self) -> None:
        self.firestore.close()  # type: ignore[no-untyped-call]
        await self.google.close()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    runtime = Runtime(AuditLogSettings())
    app.state.runtime = runtime
    try:
        yield
    finally:
        await runtime.close()


app = FastAPI(title="FireKey Audit Log Publisher", docs_url=None, lifespan=lifespan)


@app.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/drain", response_model=DrainResponse)
async def drain(request: Request) -> DrainResponse:
    runtime: Runtime = request.app.state.runtime
    return await runtime.drain()


@app.post("/events", response_model=EventResponse)
async def events(
    request: Request,
    authorization: Annotated[str, Header(min_length=8)],
) -> EventResponse:
    runtime: Runtime = request.app.state.runtime
    await _trusted(runtime, authorization)
    envelope = await _json_body(request, 1_048_576)
    event = RunEvent.model_validate(_pubsub_data(envelope))
    identity = hashlib.sha256(f"{event.organisation_id}\0{event.id}".encode()).hexdigest()
    audit_id = f"audit_{identity[:40]}"
    await runtime.writer.append(
        audit_id,
        event.organisation_id,
        event.kind.value,
        event.actor_id,
        f"runs/{event.run_id}",
        {
            "credential_id": event.credential_id,
            "revision": event.revision,
            "stage": event.stage.value,
            "status": event.status.value,
            **event.payload,
        },
        run_id=event.run_id,
        occurred_at=event.occurred_at,
    )
    return EventResponse(audit_id=audit_id)


async def _trusted(runtime: Runtime, authorization: str) -> None:
    scheme, separator, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not separator or not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "bearer identity token is required")
    try:
        identity = await runtime.tokens.verify(token)
    except AuthenticationError as error:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(error)) from error
    if identity.email != runtime.settings.trusted_push_service_account:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "audit caller is not authorised")


async def _json_body(request: Request, limit: int) -> dict[str, Any]:
    body = await request.body()
    if len(body) > limit:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "request body is too large")
    try:
        value = json.loads(body)
    except json.JSONDecodeError as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "request body is invalid JSON") from error
    if not isinstance(value, dict):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "request body must be an object")
    return value


def _pubsub_data(envelope: dict[str, Any]) -> dict[str, Any]:
    message = envelope.get("message")
    encoded = message.get("data") if isinstance(message, dict) else None
    if not isinstance(encoded, str) or not encoded:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Pub/Sub message data is missing")
    try:
        payload = json.loads(base64.b64decode(encoded, validate=True))
    except (ValueError, binascii.Error, json.JSONDecodeError) as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Pub/Sub payload is invalid") from error
    if not isinstance(payload, dict):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Pub/Sub payload must be an object")
    return payload
