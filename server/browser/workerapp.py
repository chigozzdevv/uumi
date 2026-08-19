import asyncio
import base64
import binascii
import hashlib
import hmac
import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from typing import Annotated, Any, cast

from broker import CapabilityClaims, CapabilityVerifier
from broker.capability import request_digest
from broker.evidence import GcsEvidenceSink
from capture import SecureCapture
from connectors.base import SecretValue
from connectors.google import GoogleRestClient
from connectors.secrets import SecretManagerConnector
from contracts import (
    Approval,
    ApprovalDecision,
    BrowserAction,
    BrowserActionKind,
    BrowserPolicy,
    BrowserSession,
    BrowserStatus,
    Connection,
    Contract,
    PlaybookStep,
    PlaybookVersion,
    ProtectedAction,
    RotationRun,
    SecureCaptureResult,
    Selector,
)
from core.errors import CapabilityError, ResourceConflictError, ResourceNotFoundError
from core.ids import new_id
from core.storage import FirestoreCatalog
from core.storage.paths import FirestorePaths
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastapi import FastAPI, Header, Request, WebSocket
from fastapi.responses import JSONResponse
from google.cloud.firestore_v1 import AsyncClient
from playwright.async_api import Browser, BrowserContext, Playwright, async_playwright
from policy import digest
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from telemetry import instrument

from browser.auth import BrowserAuthBroker, filter_storage_state, validate_storage_state
from browser.driver import AuthenticationRequiredError, BrowserDriver
from browser.model import ComputerProposal, ComputerUseClient
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
    capability_public_key: str = Field(min_length=40, max_length=64)
    evidence_bucket: str = Field(min_length=3)
    region: str = Field(min_length=3, max_length=32)
    model: str = "gemini-3.7-flash"
    setup: bool = False
    setup_token_hash: str = ""
    setup_domains: str = ""
    setup_storage_domains: str = ""
    setup_secret: str = ""


class SetupRuntime:
    # Setup VMs run no model and record no replay; the human drives a fresh
    # profile and only the exported session leaves the machine.
    def __init__(
        self,
        context: BrowserContext,
        driver: BrowserDriver,
        token_hash: str,
        storage_domains: tuple[str, ...],
        secret: str,
        google: GoogleRestClient,
    ) -> None:
        self.context = context
        self.driver = driver
        self.token_hash = token_hash
        self.storage_domains = storage_domains
        self.secret = secret
        self.secrets = SecretManagerConnector(google)
        self.google = google


class ProposeRequest(Contract):
    step: PlaybookStep
    objective: str = Field(min_length=1, max_length=2048)


class NavigateRequest(Contract):
    step: PlaybookStep


class ProposeResponse(Contract):
    done: bool
    outputs: dict[str, str] = Field(default_factory=dict)
    action: BrowserAction | None = None
    requires_confirmation: bool = False
    safety_explanation: str | None = Field(default=None, max_length=1024)


class ExecuteRequest(Contract):
    action_id: str = Field(min_length=3, max_length=96)
    confirmed: bool


class ExecuteResponse(Contract):
    session: BrowserSession
    capture: SecureCaptureResult | None = None
    paused_reason: str | None = Field(default=None, max_length=256)


class WorkerRuntime:
    def __init__(
        self,
        firestore: AsyncClient,
        google: GoogleRestClient,
        playwright: Playwright,
        browser: Browser,
        session: BrowserSession,
        signer: CapabilityVerifier,
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
        self.continuations: dict[str, tuple[ComputerProposal, dict[str, str | int | bool]]] = {}

    async def close(self) -> None:
        await self.browser.close()
        await self.playwright.stop()
        self.firestore.close()  # type: ignore[no-untyped-call]
        await self.google.close()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings = WorkerSettings()  # type: ignore[call-arg]
    if settings.setup:
        async with _setup_lifespan(app, settings):
            yield
        return
    firestore = AsyncClient(project=settings.project_id, database=settings.firestore_database)
    catalog = FirestoreCatalog(firestore)
    session = await _wait_session(catalog, settings.organisation_id, settings.session_id)
    google = GoogleRestClient()
    signer = CapabilityVerifier.decode(settings.capability_public_key)
    connection = await catalog.get(
        FirestorePaths.connection(session.organisation_id, session.provider_connection_id),
        Connection,
    )
    storage_state = await BrowserAuthBroker(SecretManagerConnector(google)).storage_state(
        connection,
        connection.allowed_resources,
    )
    engine = await async_playwright().start()
    browser = await engine.chromium.launch(
        headless=True,
        args=["--disable-dev-shm-usage", "--disable-extensions", "--no-first-run"],
    )
    context = await browser.new_context(
        viewport={"width": 1440, "height": 900},
        accept_downloads=False,
        service_workers="block",
        storage_state=cast(Any, storage_state),
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
        masked_selectors,
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
instrument(app, "firekey-browser")
Capability = Annotated[str, Header(alias="X-FireKey-Capability", min_length=32)]
SetupToken = Annotated[str, Header(alias="X-FireKey-Setup", min_length=32)]


@app.exception_handler(AuthenticationRequiredError)
async def _authentication_required(
    request: Request, error: AuthenticationRequiredError
) -> JSONResponse:
    del request
    return JSONResponse(
        status_code=409,
        content={"code": "authentication-required", "message": str(error)},
    )


@app.exception_handler(CapabilityError)
async def _capability_error(request: Request, error: CapabilityError) -> JSONResponse:
    del request
    return JSONResponse(status_code=403, content={"code": "forbidden", "message": str(error)})


@app.exception_handler(ResourceConflictError)
async def _resource_conflict(request: Request, error: ResourceConflictError) -> JSONResponse:
    del request
    return JSONResponse(
        status_code=409,
        content={"code": "conflict", "message": str(error)},
    )


@asynccontextmanager
async def _setup_lifespan(app: FastAPI, settings: WorkerSettings) -> AsyncGenerator[None, None]:
    if (
        not settings.setup_token_hash
        or not settings.setup_domains
        or not settings.setup_storage_domains
        or not settings.setup_secret
    ):
        raise RuntimeError("setup mode requires token, domain, and secret metadata")
    if len(settings.setup_token_hash) != 64 or any(
        character not in "0123456789abcdef" for character in settings.setup_token_hash
    ):
        raise RuntimeError("setup mode requires a valid token hash")
    domains = tuple(value.strip() for value in settings.setup_domains.split(",") if value.strip())
    storage_domains = tuple(
        value.strip() for value in settings.setup_storage_domains.split(",") if value.strip()
    )
    if not domains:
        raise RuntimeError("setup mode requires at least one allowed domain")
    if not storage_domains or not set(storage_domains).issubset(domains):
        raise RuntimeError("setup storage domains must be included in allowed domains")
    google = GoogleRestClient()
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
    driver = BrowserDriver(
        page,
        BrowserPolicy(
            allowed_domains=domains,
            allowed_actions=frozenset(BrowserActionKind),
        ),
    )
    await driver.enforce_egress()
    app.state.setup = SetupRuntime(
        context,
        driver,
        settings.setup_token_hash,
        storage_domains,
        settings.setup_secret,
        google,
    )
    try:
        yield
    finally:
        await browser.close()
        await engine.stop()
        await google.close()


def _runtime(request: Request) -> WorkerRuntime:
    runtime = getattr(request.app.state, "runtime", None)
    if runtime is None:
        raise ResourceNotFoundError("browser worker is in connection setup mode")
    return cast(WorkerRuntime, runtime)


def _setup(request: Request) -> SetupRuntime:
    setup = getattr(request.app.state, "setup", None)
    if setup is None:
        raise ResourceNotFoundError("browser worker is not in setup mode")
    return cast(SetupRuntime, setup)


def _authorise_setup(setup: SetupRuntime, token: str | None) -> None:
    presented = hashlib.sha256((token or "").encode()).hexdigest()
    if not token or not hmac.compare_digest(presented, setup.token_hash):
        raise CapabilityError("setup token is invalid")


@app.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/steps/propose", response_model=ProposeResponse)
async def propose(
    body: ProposeRequest,
    capability: Capability,
    request: Request,
) -> ProposeResponse:
    runtime = _runtime(request)
    session, _, _ = await _authorise(
        runtime,
        capability,
        "browser.operate",
        {"objective": body.objective, "step": body.step.model_dump(mode="json")},
    )
    await _validate_step(runtime, session, body.step)
    worker: ComputerUseWorker = request.app.state.worker
    continuation = runtime.continuations.get(body.step.id)
    proposed = await worker.propose(
        session,
        body.step,
        body.objective,
        continuation[0] if continuation is not None else None,
        continuation[1] if continuation is not None else None,
    )
    if proposed is None:
        runtime.continuations.pop(body.step.id, None)
        return ProposeResponse(done=True, outputs=await runtime.driver.extract(body.step.outputs))
    runtime.pending.clear()
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
    runtime = _runtime(request)
    session, _, claims = await _authorise(
        runtime,
        capability,
        "browser.execute",
        {"action_id": body.action_id, "confirmed": body.confirmed},
    )
    pending = runtime.pending.pop(body.action_id, None)
    if pending is None:
        raise ResourceConflictError("browser proposal is missing or was already consumed")
    proposal, step = pending
    await _validate_approval(runtime, claims, step)
    worker: ComputerUseWorker = request.app.state.worker
    if step.secure_field is not None:
        changed, capture = await worker.execute_protected_capture(
            session, proposal, step, body.confirmed
        )
    else:
        changed = await worker.execute(session, proposal, body.confirmed)
        capture = None
    runtime.session = changed
    outcome: dict[str, str | int | bool] = {
        "status": "succeeded" if changed.status is BrowserStatus.RUNNING else "paused",
        "url": runtime.driver.metadata_url,
    }
    if proposal.model.requires_confirmation and body.confirmed:
        outcome["safety_acknowledgement"] = "true"
    runtime.continuations[step.id] = (proposal.model, outcome)
    if not changed.recording_paused:
        await runtime.replay.record(
            changed,
            proposal.action.kind.value,
            runtime.masked_selectors,
            ("computer-use",),
        )
    paused_reason = None
    if step.secure_field is not None and capture is None:
        paused_reason = "secure capture requires human-assisted transfer"
    return ExecuteResponse(
        session=changed,
        capture=capture,
        paused_reason=paused_reason,
    )


@app.post("/v1/steps/navigate", response_model=ExecuteResponse)
async def navigate(
    body: NavigateRequest,
    capability: Capability,
    request: Request,
) -> ExecuteResponse:
    runtime = _runtime(request)
    payload = {"step": body.step.model_dump(mode="json")}
    session, _, claims = await _authorise(runtime, capability, "browser.navigate", payload)
    await _validate_step(runtime, session, body.step)
    if (
        body.step.operation != "navigate"
        or body.step.secure_field is not None
        or body.step.selectors
    ):
        raise ResourceConflictError("deterministic navigation requires a selector-free URL step")
    await _validate_approval(runtime, claims, body.step)
    url = body.step.parameters.get("url")
    if not isinstance(url, str):
        raise ResourceConflictError("deterministic navigation step has no URL")
    action = BrowserAction(
        id=new_id("browser-action"),
        session_id=session.id,
        kind=BrowserActionKind.NAVIGATE,
        url=url,
        protected=body.step.protected,
        expected_url=body.step.checkpoint.url_pattern if body.step.checkpoint else None,
        expected_text=body.step.checkpoint.required_text if body.step.checkpoint else (),
        forbidden_text=(body.step.checkpoint.forbidden_text if body.step.checkpoint else ()),
        fencing_token=session.fencing_token,
    )
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
        raise
    await runtime.sessions.finish_action(session.organisation_id, session.id, action.id, True)
    runtime.session = authorised
    await runtime.replay.record(
        authorised,
        action.kind.value,
        runtime.masked_selectors,
        ("deterministic-navigation",),
    )
    return ExecuteResponse(session=authorised)


@app.websocket("/v1/live")
async def live_browser(websocket: WebSocket) -> None:
    runtime = getattr(websocket.app.state, "runtime", None)
    if runtime is None:
        await websocket.close(code=4404, reason="browser worker is in setup mode")
        return
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
            if session.status is BrowserStatus.TAKEOVER:
                await websocket.send_bytes(
                    await runtime.driver.live_screenshot(session, runtime.masked_selectors)
                )
            elif session.recording_paused:
                await websocket.send_json({"type": "paused", "status": session.status.value})
            else:
                await websocket.send_bytes(
                    await runtime.driver.screenshot(session, runtime.masked_selectors)
                )
        elif kind == "action" and mode == "takeover":
            action = BrowserAction.model_validate(message.get("action"))
            await _validate_takeover_action(runtime, session, action)
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
) -> tuple[BrowserSession, RotationRun, CapabilityClaims]:
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
    return session, run, claims


async def _validate_approval(
    runtime: WorkerRuntime,
    claims: CapabilityClaims,
    step: PlaybookStep,
) -> None:
    if step.protected and claims.approval_id is None:
        raise CapabilityError("protected browser action has no consumed approval")
    if claims.approval_id is None:
        return
    catalog = FirestoreCatalog(runtime.firestore)
    approval = await catalog.get(
        FirestorePaths.approval(runtime.session.organisation_id, claims.approval_id), Approval
    )
    action = await catalog.get(
        FirestorePaths.action(runtime.session.organisation_id, approval.action_id), ProtectedAction
    )
    if (
        approval.decision is not ApprovalDecision.APPROVED
        or approval.consumed_at is None
        or approval.expires_at <= _now()
    ):
        raise CapabilityError("protected browser approval is not active")
    if action.run_id != runtime.session.run_id or action.kind != step.tool:
        raise CapabilityError("protected browser approval belongs to another action")
    if claims.action_digest != digest(action) or approval.action_digest != digest(action):
        raise CapabilityError("protected browser action digest changed")


async def _validate_step(
    runtime: WorkerRuntime,
    session: BrowserSession,
    step: PlaybookStep,
) -> None:
    version = await FirestoreCatalog(runtime.firestore).get(
        FirestorePaths.playbook_version(
            session.organisation_id,
            session.playbook_id,
            session.playbook_version,
        ),
        PlaybookVersion,
    )
    matches = tuple(item for item in version.definition.steps if item.id == step.id)
    if len(matches) != 1 or not _resolved_step(matches[0], step):
        raise CapabilityError("browser step differs from the immutable playbook")


def _resolved_step(template: PlaybookStep, resolved: PlaybookStep) -> bool:
    return resolved.model_copy(update={"parameters": template.parameters}) == template


async def _validate_takeover_action(
    runtime: WorkerRuntime,
    session: BrowserSession,
    action: BrowserAction,
) -> None:
    if session.status is not BrowserStatus.TAKEOVER or session.takeover_subject is None:
        raise CapabilityError("human action requires an active takeover")
    if action.protected or action.expected_url or action.expected_text or action.forbidden_text:
        raise CapabilityError("takeover cannot declare its own protected checkpoint")
    if action.kind is BrowserActionKind.KEY and action.value == "Enter":
        raise CapabilityError("takeover cannot submit a protected form with Enter")
    version = await FirestoreCatalog(runtime.firestore).get(
        FirestorePaths.playbook_version(
            session.organisation_id,
            session.playbook_id,
            session.playbook_version,
        ),
        PlaybookVersion,
    )
    protected = {
        selector
        for step in version.definition.steps
        if step.protected
        for selector in (
            *step.selectors,
            *(
                (step.secure_field.selector, step.secure_field.provider_id_selector)
                if step.secure_field is not None
                else ()
            ),
        )
    }
    if action.selector is not None:
        for selector in protected:
            if action.selector == selector or await runtime.driver.same_element(
                action.selector, selector
            ):
                raise CapabilityError("takeover cannot operate a protected playbook control")


async def _wait_session(
    catalog: FirestoreCatalog,
    organisation_id: str,
    session_id: str,
) -> BrowserSession:
    for _ in range(90):
        session = await catalog.get(
            FirestorePaths.browser(organisation_id, session_id), BrowserSession
        )
        if session.status in {BrowserStatus.READY, BrowserStatus.RUNNING}:
            return session
        if session.status is not BrowserStatus.PROVISIONING:
            raise RuntimeError(f"browser worker cannot start from {session.status.value}")
        await asyncio.sleep(2)
    raise RuntimeError("browser session did not become ready before worker startup timeout")


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


@app.post("/v1/setup/store")
async def setup_store(request: Request, token: SetupToken) -> dict[str, Any]:
    setup = _setup(request)
    _authorise_setup(setup, token)
    state = await setup.context.storage_state()
    filtered = filter_storage_state(state, setup.storage_domains)
    if not filtered["cookies"] and not filtered["origins"]:
        raise ResourceConflictError("no provider session was captured on the connection domains")
    validate_storage_state(filtered, setup.storage_domains)
    encoded = bytearray(json.dumps(filtered, separators=(",", ":")).encode())
    secret = SecretValue(encoded)
    try:
        return await setup.secrets.add_version(setup.secret, secret)
    finally:
        secret.clear()
        for index in range(len(encoded)):
            encoded[index] = 0
        await _clear_setup_state(setup.context)


async def _clear_setup_state(context: BrowserContext) -> None:
    await context.clear_cookies()
    for page in context.pages:
        with suppress(Exception):
            await page.evaluate("localStorage.clear(); sessionStorage.clear()")


@app.websocket("/v1/setup/live")
async def setup_live(websocket: WebSocket) -> None:
    setup = getattr(websocket.app.state, "setup", None)
    if setup is None:
        await websocket.close(code=4404, reason="browser worker is not in setup mode")
        return
    token = websocket.headers.get("x-firekey-setup")
    presented = hashlib.sha256((token or "").encode()).hexdigest()
    if not token or not hmac.compare_digest(presented, setup.token_hash):
        await websocket.close(code=4403, reason="setup token is invalid")
        return
    await websocket.accept()
    while True:
        message = await websocket.receive_json()
        if not isinstance(message, dict):
            await websocket.close(code=4400, reason="invalid setup message")
            return
        kind = message.get("type")
        if kind == "frame":
            await websocket.send_bytes(await setup.driver.setup_screenshot())
        elif kind == "action":
            raw = message.get("action")
            if not isinstance(raw, dict):
                await websocket.close(code=4400, reason="setup action is invalid")
                return
            action = BrowserAction.model_validate({**raw, "fencing_token": 1})
            try:
                await setup.driver.execute(action)
            except Exception:
                await websocket.send_json({"type": "action", "succeeded": False})
            else:
                await websocket.send_json({"type": "action", "succeeded": True})
        else:
            await websocket.close(code=4403, reason="setup message is not authorised")
            return
