import base64
import binascii
import hashlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated, Any

from connectors.github import GitHubWebhook
from connectors.google import GoogleRestClient
from connectors.scc import SecurityCommandCenterFinding
from connectors.secrets import SecretManagerConnector
from contracts import Contract, Identifier, Incident, IngestionEvent
from core.audit import AuditWriter
from core.auth import AuthenticatedIdentity, GoogleTokenVerifier
from core.errors import AuthenticationError
from core.incident import IncidentService
from core.notification import NotificationService
from core.storage import (
    FirestoreAuditRepository,
    FirestoreIncidentRepository,
    FirestoreInventoryRepository,
    FirestoreNotificationRepository,
    FirestorePolicyRepository,
    FirestoreRunRepository,
)
from core.workflow import RunWorkflow
from fastapi import FastAPI, Header, HTTPException, Request, status
from google.cloud.firestore_v1 import AsyncClient

from ingestion.automation import IncidentAutomation
from ingestion.config import IngestionSettings
from ingestion.sources import ProviderSource, ScheduleSource, SecretManagerSource


class IngestionResponse(Contract):
    incident: Incident
    applied: bool


class Runtime:
    def __init__(
        self,
        settings: IngestionSettings,
        firestore: AsyncClient,
        google: GoogleRestClient,
    ) -> None:
        self.settings = settings
        self.firestore = firestore
        self.google = google
        self.secrets = SecretManagerConnector(google)
        self.tokens = GoogleTokenVerifier(settings.oidc_audience)
        inventory = FirestoreInventoryRepository(firestore)
        notifications = NotificationService(FirestoreNotificationRepository(firestore), _now)
        audit = AuditWriter(FirestoreAuditRepository(firestore), settings.region, _now)
        self.incidents = IncidentService(
            FirestoreIncidentRepository(firestore),
            _now,
            inventory,
            RunWorkflow(FirestoreRunRepository(firestore)),
            notifications,
            audit,
        )
        self.automation = IncidentAutomation(
            self.incidents,
            inventory,
            FirestorePolicyRepository(firestore),
        )

    async def close(self) -> None:
        self.firestore.close()  # type: ignore[no-untyped-call]
        await self.google.close()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = IngestionSettings()  # type: ignore[call-arg]
    firestore = AsyncClient(project=settings.project_id, database=settings.firestore_database)
    runtime = Runtime(settings, firestore, GoogleRestClient())
    app.state.runtime = runtime
    try:
        yield
    finally:
        await runtime.close()


app = FastAPI(title="FireKey Incident Ingestion", docs_url=None, lifespan=lifespan)


@app.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/github/{organisation_id}", response_model=IngestionResponse)
async def github(
    organisation_id: Identifier,
    request: Request,
    signature: Annotated[str, Header(alias="X-Hub-Signature-256", min_length=71)],
    delivery_id: Annotated[str, Header(alias="X-GitHub-Delivery", min_length=1, max_length=256)],
    event_type: Annotated[str, Header(alias="X-GitHub-Event", min_length=1, max_length=64)],
) -> IngestionResponse:
    runtime: Runtime = request.app.state.runtime
    body = await request.body()
    if len(body) > runtime.settings.max_body_bytes:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "webhook body is too large")
    secret_name = (
        f"projects/{runtime.settings.github_secret_project}/secrets/"
        f"firekey-{organisation_id}-github-webhook/versions/latest"
    )
    secret = await runtime.secrets.access(secret_name)
    try:
        GitHubWebhook().verify(body, signature, secret.bytes())
    except ValueError as error:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(error)) from error
    finally:
        secret.clear()
    try:
        event = GitHubWebhook().normalise(organisation_id, event_type, body, _now())
    except (ValueError, json.JSONDecodeError) as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error
    return await _ingest(runtime, event)


@app.post("/v1/scc/{organisation_id}", response_model=IngestionResponse)
async def scc(
    organisation_id: Identifier,
    request: Request,
    authorization: Annotated[str, Header(min_length=8)],
) -> IngestionResponse:
    runtime: Runtime = request.app.state.runtime
    identity = await _identity(runtime, authorization)
    if identity.email != runtime.settings.scc_push_service_account:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "SCC push identity is not authorised")
    envelope = await _json_body(request, runtime.settings.max_body_bytes)
    _, _, payload, _ = _pubsub(envelope)
    try:
        event = SecurityCommandCenterFinding().normalise(organisation_id, payload, _now())
    except ValueError as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error
    return await _ingest(runtime, event)


@app.post("/v1/schedules/{organisation_id}/{schedule_id}", response_model=IngestionResponse)
async def schedule(
    organisation_id: Identifier,
    schedule_id: Identifier,
    request: Request,
    authorization: Annotated[str, Header(min_length=8)],
    schedule_time: Annotated[
        str,
        Header(alias="X-CloudScheduler-ScheduleTime", min_length=20, max_length=64),
    ],
) -> IngestionResponse:
    runtime: Runtime = request.app.state.runtime
    await _trusted_identity(runtime, authorization)
    payload = await _json_body(request, runtime.settings.max_body_bytes)
    payload["due_at"] = schedule_time
    try:
        event = ScheduleSource().normalise(organisation_id, schedule_id, payload, _now())
    except ValueError as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error
    return await _ingest(runtime, event)


@app.post("/v1/secrets/{organisation_id}", response_model=IngestionResponse)
async def secret_manager(
    organisation_id: Identifier,
    request: Request,
    authorization: Annotated[str, Header(min_length=8)],
) -> IngestionResponse:
    runtime: Runtime = request.app.state.runtime
    await _trusted_identity(runtime, authorization)
    envelope = await _json_body(request, runtime.settings.max_body_bytes)
    message_id, _, payload, attributes = _pubsub(envelope)
    try:
        event = SecretManagerSource().normalise(
            organisation_id, payload, attributes, message_id, _now()
        )
    except ValueError as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error
    return await _ingest(runtime, event)


@app.post("/v1/providers/{organisation_id}/{provider}", response_model=IngestionResponse)
async def provider(
    organisation_id: Identifier,
    provider: Identifier,
    request: Request,
    signature: Annotated[str, Header(alias="X-FireKey-Signature", min_length=71)],
    timestamp: Annotated[str, Header(alias="X-FireKey-Timestamp", min_length=20, max_length=64)],
) -> IngestionResponse:
    runtime: Runtime = request.app.state.runtime
    body = await request.body()
    if len(body) > runtime.settings.max_body_bytes:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "webhook body is too large")
    secret_name = (
        f"projects/{runtime.settings.project_id}/secrets/"
        f"{runtime.settings.provider_secret_prefix}-{organisation_id}-{provider}/versions/latest"
    )
    secret = await runtime.secrets.access(secret_name)
    try:
        _verify_hmac(
            body,
            signature,
            timestamp,
            secret.bytes(),
            _now(),
            runtime.settings.webhook_replay_seconds,
        )
    except ValueError as error:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(error)) from error
    finally:
        secret.clear()
    try:
        payload = json.loads(body)
        if not isinstance(payload, dict):
            raise ValueError("provider payload must be an object")
        event = ProviderSource().normalise(organisation_id, provider, payload, _now())
    except (ValueError, json.JSONDecodeError) as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error
    return await _ingest(runtime, event)


async def _identity(runtime: Runtime, authorization: str) -> AuthenticatedIdentity:
    scheme, separator, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not separator or not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "bearer identity token is required")
    try:
        return await runtime.tokens.verify(token)
    except AuthenticationError as error:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(error)) from error


async def _trusted_identity(runtime: Runtime, authorization: str) -> AuthenticatedIdentity:
    identity = await _identity(runtime, authorization)
    if identity.email not in runtime.settings.trusted_push_service_accounts:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "push identity is not authorised")
    return identity


async def _ingest(runtime: Runtime, event: IngestionEvent) -> IngestionResponse:
    incident, applied = await runtime.automation.ingest(event)
    return IngestionResponse(incident=incident, applied=applied)


def _pubsub(
    envelope: dict[str, Any],
) -> tuple[str, datetime, dict[str, Any], dict[str, str]]:
    message = envelope.get("message")
    if not isinstance(message, dict):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Pub/Sub message is missing")
    message_id = message.get("messageId")
    encoded = message.get("data")
    published = message.get("publishTime")
    raw_attributes = message.get("attributes", {})
    if not all(isinstance(value, str) and value for value in (message_id, encoded, published)):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Pub/Sub message is incomplete")
    assert isinstance(message_id, str) and isinstance(encoded, str) and isinstance(published, str)
    try:
        payload = json.loads(base64.b64decode(encoded, validate=True))
        published_at = datetime.fromisoformat(published.replace("Z", "+00:00"))
    except (ValueError, binascii.Error, json.JSONDecodeError) as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Pub/Sub payload is invalid") from error
    if not isinstance(payload, dict):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Pub/Sub payload must be an object")
    if published_at.tzinfo is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Pub/Sub publish time has no timezone")
    if not isinstance(raw_attributes, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in raw_attributes.items()
    ):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Pub/Sub attributes are invalid")
    return message_id, published_at, payload, raw_attributes


def _verify_hmac(
    body: bytes,
    signature: str,
    timestamp: str,
    secret: bytes,
    now: datetime,
    replay_seconds: int,
) -> None:
    import hmac

    if not signature.startswith("sha256="):
        raise ValueError("provider signature must use sha256")
    try:
        signed_at = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("provider timestamp is invalid") from error
    if signed_at.tzinfo is None or abs((now - signed_at).total_seconds()) > replay_seconds:
        raise ValueError("provider webhook is outside the replay window")
    message = timestamp.encode() + b"." + body
    expected = "sha256=" + hmac.new(secret, message, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise ValueError("provider signature is invalid")


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


def _now() -> datetime:
    return datetime.now(UTC)
