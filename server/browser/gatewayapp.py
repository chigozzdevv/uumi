from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from broker import CapabilityVerifier
from core.auth import AccessControl, FirestoreAccessRepository, IapTokenVerifier
from fastapi import FastAPI, WebSocket
from google.cloud.firestore_v1 import AsyncClient
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from telemetry import instrument

from browser.gateway import BrowserSessionGateway
from browser.gateway_storage import FirestoreGatewayRepository


class GatewaySettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FIREKEY_", extra="ignore")

    project_id: str = Field(min_length=4)
    firestore_database: str = "(default)"
    iap_audience: str = Field(min_length=8)
    capability_public_key: str = Field(min_length=40, max_length=64)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings = GatewaySettings()  # type: ignore[call-arg]
    firestore = AsyncClient(project=settings.project_id, database=settings.firestore_database)
    verifier = CapabilityVerifier.decode(settings.capability_public_key)
    app.state.gateway = BrowserSessionGateway(
        FirestoreGatewayRepository(firestore),
        AccessControl(FirestoreAccessRepository(firestore)),
        IapTokenVerifier(settings.iap_audience),
        verifier,
    )
    yield
    firestore.close()  # type: ignore[no-untyped-call]


app = FastAPI(title="FireKey Browser Gateway", docs_url=None, lifespan=lifespan)
instrument(app, "firekey-gateway")


@app.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@app.websocket("/v1/live")
async def browser_live(websocket: WebSocket) -> None:
    gateway: BrowserSessionGateway = websocket.app.state.gateway
    await gateway.bridge(websocket)


@app.websocket("/v1/setup/live")
async def setup_live(websocket: WebSocket) -> None:
    gateway: BrowserSessionGateway = websocket.app.state.gateway
    await gateway.bridge_setup(websocket)
