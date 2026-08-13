import base64
import binascii
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated, Any

from broker import CapabilitySigner
from broker.capability import request_digest
from broker.evidence import GcsEvidenceSink
from capture import SecureCapture
from connectors.google import GoogleRestClient
from connectors.secrets import SecretManagerConnector
from contracts import (
    BrowserAction,
    BrowserSession,
    BrowserStatus,
    Contract,
    PlaybookStep,
    PlaybookVersion,
    RotationRun,
    SecureCaptureResult,
    Selector,
)
from core.errors import CapabilityError, ResourceConflictError
from core.ids import new_id
from core.storage import FirestoreCatalog
from core.storage.paths import FirestorePaths
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastapi import FastAPI, Header, Request, WebSocket
from google.cloud.firestore_v1 import AsyncClient
from playwright.async_api import Browser, Playwright, async_playwright
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from browser.driver import BrowserDriver
from browser.model import ComputerUseClient
from browser.replay import ReplayRecorder
from browser.service import BrowserService
from browser.storage import FirestoreBrowserRepository
from browser.worker import ComputerUseWorker, ProposedBrowserAction


class WorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FIREKEY_", extra="ignore")

    project_id: str = Field(min_length=4)
    firestore_database: str = "(default)"
    organisation_id: str = Field(min_length=3)
    session_id: str = Field(min_length=3)
    capability_key_version: str = Field(pattern=r"^projects/.+/secrets/.+/versions/\d+$")
    evidence_bucket: str = Field(min_length=3)
    region: str = Field(min_length=3, max_length=32)
    model: str = "gemini-3.5-flash"


class ProposeRequest(Contract):
    step: PlaybookStep
    objective: str = Field(min_length=1, max_length=2048)


class ProposeResponse(Contract):
    done: bool
    action: BrowserAction | None = None
    requires_confirmation: bool = False
    safety_explanation: str | None = Field(default=None, max_length=1024)


class ExecuteRequest(Contract):
    action_id: str = Field(min_length=3, max_length=96)
    confirmed: bool


class ExecuteResponse(Contract):
    session: BrowserSession
    capture: SecureCaptureResult | None = None


class WorkerRuntime:
    def __init__(
        self,
        firestore: AsyncClient,
        google: GoogleRestClient,
        playwright: Playwright,
        browser: Browser,
        session: BrowserSession,
        signer: CapabilitySigner,
        driver: BrowserDriver,
        sessions: BrowserService,
        capture: SecureCapture,
        replay: ReplayRecorder,
        masked_selectors: tuple[Selector, ...],
    ) -> None:
        self.firestore = firestore
        self.google = google
        self.playwright = playwright
        self.browser = browser
        self.session = session
        self.signer = signer
        self.driver = driver
        self.sessions = sessions
        self.capture = capture
        self.replay = replay
        self.masked_selectors = masked_selectors
        self.private_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        self.pending: dict[str, tuple[ProposedBrowserAction, PlaybookStep]] = {}

    async def close(self) -> None:
        await self.browser.close()
        await self.playwright.stop()
        self.firestore.close()  # type: ignore[no-untyped-call]
        await self.google.close()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = WorkerSettings()  # type: ignore[call-arg]
    firestore = AsyncClient(project=settings.project_id, database=settings.firestore_database)
    catalog = FirestoreCatalog(firestore)
    session = await catalog.get(
        FirestorePaths.browser(settings.organisation_id, settings.session_id), BrowserSession
    )
    if session.status not in {BrowserStatus.READY, BrowserStatus.RUNNING}:
        raise RuntimeError("browser session is not ready for its worker")
    google = GoogleRestClient()
    capability = await SecretManagerConnector(google).access(settings.capability_key_version)
    try:
        signer = CapabilitySigner(capability.bytes())
    finally:
        capability.clear()
    engine = await async_playwright().start()
    browser = await engine.chromium.launch(
        headless=True,
        args=["--disable-dev-shm-usage", "--disable-extensions", "--no-first-run"],
    )
    context = await browser.new_context(
        viewport={"width": 1440, "height": 900},
        accept_downloads=False,
        service_workers="block",
    )
    page = await context.new_page()
    driver = BrowserDriver(page, session.policy)
    await driver.enforce_egress()
    sessions = BrowserService(FirestoreBrowserRepository(firestore), _now)
    capture = SecureCapture(page, driver, SecretManagerConnector(google), _now)
    version = await catalog.get(
        FirestorePaths.playbook_version(
            session.organisation_id, session.playbook_id, session.playbook_version
        ),
        PlaybookVersion,
    )
    masked_selectors = tuple(
        step.secure_field.selector
        for step in version.definition.steps
        if step.secure_field is not None
    )
    replay = ReplayRecorder(
        driver,
        sessions,
        GcsEvidenceSink(google, firestore, settings.evidence_bucket, settings.region),
        _now,
        new_id,
    )
    worker = ComputerUseWorker(
        ComputerUseClient(google, settings.project_id, settings.model),
        driver,
        sessions,
        capture,
        new_id,
    )
    app.state.worker = worker
    app.state.sessions = sessions
    app.state.runtime = WorkerRuntime(
        firestore,
        google,
        engine,
        browser,
        session,
        signer,
        driver,
        sessions,
        capture,
        replay,
        masked_selectors,
    )
    yield
    await app.state.runtime.close()


app = FastAPI(title="FireKey Browser Worker", docs_url=None, lifespan=lifespan)
Capability = Annotated[str, Header(alias="X-FireKey-Capability", min_length=32)]


@app.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/steps/propose", response_model=ProposeResponse)
async def propose(
    body: ProposeRequest,
    capability: Capability,
    request: Request,
) -> ProposeResponse:
    runtime: WorkerRuntime = request.app.state.runtime
    session, _ = await _authorise(
        runtime,
        capability,
        "browser.operate",
        {"objective": body.objective, "step": body.step.model_dump(mode="json")},
    )
    worker: ComputerUseWorker = request.app.state.worker
    proposed = await worker.propose(session, body.step, body.objective)
    if proposed is None:
        return ProposeResponse(done=True)
    runtime.pending[proposed.action.id] = (proposed, body.step)
    return ProposeResponse(
        done=False,
        action=proposed.action,
        requires_confirmation=proposed.requires_confirmation,
        safety_explanation=proposed.model.safety_explanation,
    )


@app.post("/v1/steps/execute", response_model=ExecuteResponse)
async def execute(
    body: ExecuteRequest,
    capability: Capability,
    request: Request,
) -> ExecuteResponse:
    runtime: WorkerRuntime = request.app.state.runtime
    session, _ = await _authorise(
        runtime,
        capability,
        "browser.execute",
        {"action_id": body.action_id, "confirmed": body.confirmed},
    )
    pending = runtime.pending.pop(body.action_id, None)
    if pending is None:
        raise ResourceConflictError("browser proposal is missing or was already consumed")
    proposal, step = pending
    worker: ComputerUseWorker = request.app.state.worker
    if step.secure_field is not None:
        changed, capture = await worker.execute_protected_capture(
            session, proposal, step, body.confirmed
        )
    else:
        changed = await worker.execute(session, proposal, body.confirmed)
        capture = None
    runtime.session = changed
    if not changed.recording_paused:
        await runtime.replay.record(
            changed,
            proposal.action.kind.value,
            runtime.masked_selectors,
            ("computer-use",),
        )
    return ExecuteResponse(session=changed, capture=capture)


@app.websocket("/v1/live")
async def live_browser(websocket: WebSocket) -> None:
    runtime: WorkerRuntime = websocket.app.state.runtime
    capability = websocket.headers.get("x-firekey-capability")
    if capability is None:
        await websocket.close(code=4401, reason="browser capability is required")
        return
    try:
        mode = await _authorise_live(runtime, capability)
    except CapabilityError:
        await websocket.close(code=4403, reason="browser capability is invalid")
        return
    await websocket.accept()
    while True:
        message = await websocket.receive_json()
        if not isinstance(message, dict):
            await websocket.close(code=4400, reason="invalid browser message")
            return
        kind = message.get("type")
        session = await FirestoreCatalog(runtime.firestore).get(
            FirestorePaths.browser(runtime.session.organisation_id, runtime.session.id),
            BrowserSession,
        )
        if kind == "frame":
            if session.model_paused or session.recording_paused:
                await websocket.send_json({"type": "paused", "status": session.status.value})
            else:
                await websocket.send_bytes(
                    await runtime.driver.screenshot(session, runtime.masked_selectors)
                )
        elif kind == "action" and mode == "takeover":
            action = BrowserAction.model_validate(message.get("action"))
            authorised = await runtime.sessions.authorize_action(
                session.organisation_id, session.id, session.revision, action
            )
            try:
                await runtime.driver.execute(action)
            except Exception as error:
                await runtime.sessions.finish_action(
                    session.organisation_id,
                    session.id,
                    action.id,
                    False,
                    f"{type(error).__name__}: {error}"[:1024],
                )
                await websocket.send_json({"type": "action", "succeeded": False})
            else:
                await runtime.sessions.finish_action(
                    session.organisation_id, session.id, action.id, True
                )
                runtime.session = authorised
                if not authorised.recording_paused:
                    await runtime.replay.record(
                        authorised,
                        action.kind.value,
                        runtime.masked_selectors,
                        ("human-takeover",),
                    )
                await websocket.send_json({"type": "action", "succeeded": True})
        elif kind == "secure-key" and mode == "takeover":
            public_key = runtime.private_key.public_key().public_bytes(
                serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )
            await websocket.send_json(
                {
                    "type": "secure-key",
                    "algorithm": "RSA-OAEP-256",
                    "public_key": public_key.decode(),
                }
            )
        elif kind == "secure-input" and mode == "takeover":
            changed, result = await _secure_input(runtime, session, message)
            runtime.session = changed
            await websocket.send_json(
                {
                    "type": "secure-input",
                    "succeeded": result is not None,
                    "secret_reference": result.secret_reference if result else None,
                }
            )
        else:
            await websocket.close(code=4403, reason="browser message is not authorised")
            return


async def _authorise(
    runtime: WorkerRuntime,
    capability: str,
    tool: str,
    payload: dict[str, Any],
) -> tuple[BrowserSession, RotationRun]:
    catalog = FirestoreCatalog(runtime.firestore)
    session = await catalog.get(
        FirestorePaths.browser(runtime.session.organisation_id, runtime.session.id),
        BrowserSession,
    )
    run = await catalog.get(
        FirestorePaths.run(session.organisation_id, session.run_id), RotationRun
    )
    claims = runtime.signer.verify(capability, _now())
    expected = (
        session.organisation_id,
        session.run_id,
        tool,
        session.id,
        run.stage,
        session.fencing_token,
        request_digest(tool, payload),
    )
    actual = (
        claims.organisation_id,
        claims.run_id,
        claims.tool,
        claims.connection_id,
        claims.stage,
        claims.fencing_token,
        claims.request_digest,
    )
    if actual != expected:
        raise CapabilityError("worker capability does not bind the exact browser operation")
    return session, run


def _now() -> datetime:
    return datetime.now(UTC)


async def _authorise_live(runtime: WorkerRuntime, capability: str) -> str:
    catalog = FirestoreCatalog(runtime.firestore)
    session = await catalog.get(
        FirestorePaths.browser(runtime.session.organisation_id, runtime.session.id),
        BrowserSession,
    )
    run = await catalog.get(
        FirestorePaths.run(session.organisation_id, session.run_id), RotationRun
    )
    claims = runtime.signer.verify(capability, _now())
    if claims.tool not in {"browser.view", "browser.takeover"}:
        raise CapabilityError("browser live capability has an invalid mode")
    if (
        claims.organisation_id != session.organisation_id
        or claims.run_id != run.id
        or claims.connection_id != session.id
        or claims.stage is not run.stage
        or claims.fencing_token != session.fencing_token
    ):
        raise CapabilityError("browser live capability has stale bindings")
    return claims.tool.removeprefix("browser.")


async def _secure_input(
    runtime: WorkerRuntime,
    session: BrowserSession,
    message: dict[str, Any],
) -> tuple[BrowserSession, SecureCaptureResult | None]:
    field_name = message.get("field_name")
    encoded = message.get("ciphertext")
    if not isinstance(field_name, str) or not isinstance(encoded, str):
        raise ResourceConflictError("secure input envelope is invalid")
    version = await FirestoreCatalog(runtime.firestore).get(
        FirestorePaths.playbook_version(
            session.organisation_id, session.playbook_id, session.playbook_version
        ),
        PlaybookVersion,
    )
    steps = tuple(
        step
        for step in version.definition.steps
        if step.secure_field is not None and step.secure_field.name == field_name
    )
    if len(steps) != 1 or steps[0].checkpoint is None or steps[0].secure_field is None:
        raise ResourceConflictError("secure input field is not uniquely declared by the playbook")
    try:
        ciphertext = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as error:
        raise ResourceConflictError("secure input ciphertext is invalid") from error
    if len(ciphertext) != 384:
        raise ResourceConflictError("secure input ciphertext has an invalid length")
    plaintext = bytearray(
        runtime.private_key.decrypt(
            ciphertext,
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None,
            ),
        )
    )
    armed = await runtime.sessions.arm_human_capture(
        session.organisation_id, session.id, session.revision
    )
    try:
        result = await runtime.capture.transfer_supplied(
            new_id("capture"),
            session.organisation_id,
            session.id,
            steps[0].secure_field,
            steps[0].checkpoint,
            plaintext,
        )
        completed = await runtime.sessions.complete_capture(result, armed.revision)
        return completed, result
    except Exception:
        frozen = await runtime.sessions.freeze(armed.organisation_id, armed.id, armed.revision)
        return frozen, None
    finally:
        for index in range(len(plaintext)):
            plaintext[index] = 0
