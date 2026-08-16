import base64
import binascii
import json
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from time import monotonic
from typing import Annotated, Any
from uuid import uuid4

from connectors.google import GoogleRestClient
from connectors.notification import NotificationConnector
from connectors.secrets import SecretManagerConnector
from contracts import Contract, RunEvent
from core.auth import GoogleTokenVerifier
from core.errors import AuthenticationError
from core.notification import NotificationDispatcher, NotificationService
from core.storage import FirestoreNotificationRepository
from fastapi import FastAPI, Header, HTTPException, Request, status
from google.cloud.firestore_v1 import AsyncClient
from telemetry import instrument, record

from notification.config import NotificationSettings
from notification.events import run_notification


class DrainResponse(Contract):
    claimed: int
    sent: int
    failed: int


class EventResponse(Contract):
    notification_id: str | None = None
    applied: bool = False


class Runtime:
    def __init__(self, settings: NotificationSettings) -> None:
        self.settings = settings
        self.firestore = AsyncClient(
            project=settings.project_id,
            database=settings.firestore_database,
        )
        self.google = GoogleRestClient()
        self.repository = FirestoreNotificationRepository(self.firestore)
        self.service = NotificationService(self.repository, _now)
        self.connector = NotificationConnector(
            SecretManagerConnector(self.google), settings.app_url
        )
        self.tokens = GoogleTokenVerifier(settings.oidc_audience)
        self.instance = os.getenv("K_REVISION", "notification")

    async def drain(self) -> DrainResponse:
        started = monotonic()
        dispatcher = NotificationDispatcher(
            self.repository,
            self.connector,
            f"{self.instance}-{uuid4().hex}",
            _now,
            timedelta(seconds=self.settings.lease_seconds),
            self.settings.batch_size,
        )
        summary = await dispatcher.drain(self.settings.maximum_deliveries)
        response = DrainResponse(
            claimed=summary.claimed,
            sent=summary.sent,
            failed=summary.failed,
        )
        record(
            "notification.deliver",
            "failed" if response.failed else "succeeded",
            monotonic() - started,
        )
        return response

    async def close(self) -> None:
        self.firestore.close()  # type: ignore[no-untyped-call]
        await self.connector.close()
        await self.google.close()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    runtime = Runtime(NotificationSettings())
    app.state.runtime = runtime
    try:
        yield
    finally:
        await runtime.close()


app = FastAPI(title="FireKey Notification Worker", docs_url=None, lifespan=lifespan)
instrument(app, "firekey-notification")


@app.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/events", response_model=EventResponse)
async def events(
    request: Request,
    authorization: Annotated[str, Header(min_length=8)],
) -> EventResponse:
    runtime: Runtime = request.app.state.runtime
    await _trusted(runtime, authorization)
    envelope = await _json_body(request, runtime.settings.maximum_body_bytes)
    event = RunEvent.model_validate(_pubsub_data(envelope))
    mapped = run_notification(event)
    if mapped is None:
        return EventResponse()
    kind, severity, title, body = mapped
    notification, applied = await runtime.service.emit(
        event.id,
        event.organisation_id,
        kind,
        severity,
        title,
        body,
        f"/organisations/{event.organisation_id}/runs/{event.run_id}",
        event.run_id,
        run_id=event.run_id,
    )
    return EventResponse(notification_id=notification.id, applied=applied)


@app.post("/drain", response_model=DrainResponse)
async def drain(
    request: Request,
) -> DrainResponse:
    runtime: Runtime = request.app.state.runtime
    return await runtime.drain()


async def _trusted(runtime: Runtime, authorization: str) -> None:
    scheme, separator, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not separator or not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "bearer identity token is required")
    try:
        identity = await runtime.tokens.verify(token)
    except AuthenticationError as error:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(error)) from error
    if identity.email != runtime.settings.trusted_push_service_account:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "notification caller is not authorised")


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
    if not isinstance(message, dict):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Pub/Sub message is missing")
    encoded = message.get("data")
    if not isinstance(encoded, str) or not encoded:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Pub/Sub message data is missing")
    try:
        payload = json.loads(base64.b64decode(encoded, validate=True))
    except (ValueError, binascii.Error, json.JSONDecodeError) as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Pub/Sub payload is invalid") from error
    if not isinstance(payload, dict):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Pub/Sub payload must be an object")
    return payload


def _now() -> datetime:
    return datetime.now(UTC)
