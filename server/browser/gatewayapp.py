from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from broker import CapabilitySigner
from connectors.google import GoogleRestClient
from connectors.secrets import SecretManagerConnector
from core.auth import AccessControl, FirestoreAccessRepository, IapTokenVerifier
from fastapi import FastAPI, WebSocket
from google.cloud.firestore_v1 import AsyncClient
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from browser.gateway import BrowserSessionGateway
from browser.gateway_storage import FirestoreGatewayRepository


class GatewaySettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FIREKEY_", extra="ignore")

    project_id: str = Field(min_length=4)
    firestore_database: str = "(default)"
    iap_audience: str = Field(min_length=8)
    capability_key_version: str = Field(pattern=r"^projects/.+/secrets/.+/versions/\d+$")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = GatewaySettings()  # type: ignore[call-arg]
    firestore = AsyncClient(project=settings.project_id, database=settings.firestore_database)
    google = GoogleRestClient()
    secret = await SecretManagerConnector(google).access(settings.capability_key_version)
    try:
        signer = CapabilitySigner(secret.bytes())
    finally:
        secret.clear()
    app.state.gateway = BrowserSessionGateway(
        FirestoreGatewayRepository(firestore),
        AccessControl(FirestoreAccessRepository(firestore)),
        IapTokenVerifier(settings.iap_audience),
        signer,
    )
    yield
    firestore.close()  # type: ignore[no-untyped-call]
    await google.close()


app = FastAPI(title="FireKey Browser Gateway", docs_url=None, lifespan=lifespan)


@app.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@app.websocket("/v1/live")
async def browser_live(websocket: WebSocket) -> None:
    gateway: BrowserSessionGateway = websocket.app.state.gateway
    await gateway.bridge(websocket)
