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
from contracts import Contract, Identifier, Incident
from core.auth import AuthenticatedIdentity, GoogleTokenVerifier
from core.errors import AuthenticationError
from core.incident import IncidentService
from core.storage import FirestoreIncidentRepository, FirestoreInventoryRepository
from fastapi import FastAPI, Header, HTTPException, Request, status
from google.cloud.firestore_v1 import AsyncClient

from ingestion.config import IngestionSettings


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
        self.incidents = IncidentService(
            FirestoreIncidentRepository(firestore),
            _now,
            FirestoreInventoryRepository(firestore),
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
    incident, applied = await runtime.incidents.ingest(
        _incident_id(event.source, delivery_id), event
    )
    return IngestionResponse(incident=incident, applied=applied)


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
    message = envelope.get("message")
    if not isinstance(message, dict):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Pub/Sub message is missing")
    message_id = message.get("messageId")
    encoded = message.get("data")
    if not isinstance(message_id, str) or not isinstance(encoded, str):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Pub/Sub message is incomplete")
    try:
        decoded = base64.b64decode(encoded, validate=True)
        payload = json.loads(decoded)
    except (ValueError, binascii.Error, json.JSONDecodeError) as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "SCC payload is invalid") from error
    if not isinstance(payload, dict):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "SCC payload must be an object")
    try:
        event = SecurityCommandCenterFinding().normalise(organisation_id, payload, _now())
    except ValueError as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error
    incident, applied = await runtime.incidents.ingest(
        _incident_id(event.source, message_id), event
    )
    return IngestionResponse(incident=incident, applied=applied)


async def _identity(runtime: Runtime, authorization: str) -> AuthenticatedIdentity:
    scheme, separator, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not separator or not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "bearer identity token is required")
    try:
        return await runtime.tokens.verify(token)
    except AuthenticationError as error:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(error)) from error


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


def _incident_id(source: str, event_id: str) -> str:
    value = hashlib.sha256(f"{source}\0{event_id}".encode()).hexdigest()[:40]
    return f"incident_{value}"


def _now() -> datetime:
    return datetime.now(UTC)
