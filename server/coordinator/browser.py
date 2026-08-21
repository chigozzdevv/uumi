import asyncio
import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from broker.capability import CapabilityClaims, CapabilitySigner, request_digest
from browser.compute import BrowserVmManager
from browser.secret import SecretAccessInstaller
from browser.service import BrowserService
from contracts import (
    Approval,
    BrowserActionKind,
    BrowserPolicy,
    BrowserSession,
    BrowserStatus,
    Connection,
    ConnectionAuthorization,
    ConnectionInterface,
    ConnectionRole,
    ConnectionStatus,
    ManagedCredential,
    PlaybookStep,
    PlaybookVersion,
    ProtectedAction,
    RotationRun,
)
from core.errors import ResourceNotFoundError
from core.ids import new_id
from core.storage.catalog import FirestoreCatalog
from core.storage.paths import FirestorePaths
from policy import digest


class BrowserPauseError(RuntimeError):
    def __init__(self, reason: str, output: dict[str, Any]) -> None:
        super().__init__(reason)
        self.output = output


class BrowserStepExecutor:
    def __init__(
        self,
        catalog: FirestoreCatalog,
        sessions: BrowserService,
        vms: BrowserVmManager,
        signer: CapabilitySigner,
        http: httpx.AsyncClient | None = None,
        secret_access: SecretAccessInstaller | None = None,
    ) -> None:
        self._catalog = catalog
        self._sessions = sessions
        self._vms = vms
        self._signer = signer
        self._http = http or httpx.AsyncClient(timeout=60)
        self._secret_access = secret_access

    async def execute(
        self,
        run: RotationRun,
        connection: Connection,
        version: PlaybookVersion,
        credential: ManagedCredential,
        protected_tools: frozenset[str],
        step: PlaybookStep,
        approval: Approval | None = None,
    ) -> dict[str, Any]:
        session = await self._session(run, connection, version, credential, protected_tools)
        if step.operation == "navigate":
            payload = {"step": step.model_dump(mode="json")}
            result = await self._post(
                run,
                session,
                "browser.navigate",
                "/v1/steps/navigate",
                payload,
                approval,
            )
            navigated = BrowserSession.model_validate(result.get("session"))
            return {"session_id": navigated.id, "step_id": step.id, "done": True}
        objective = (
            f"Execute only this approved browser objective: {step.objective}. "
            f"Operation: {step.operation}. Approved parameters: {_safe_parameters(step.parameters)}"
        )
        for _ in range(session.policy.max_steps):
            propose_payload = {"step": step.model_dump(mode="json"), "objective": objective}
            proposal = await self._post(
                run,
                session,
                "browser.operate",
                "/v1/steps/propose",
                propose_payload,
            )
            if proposal.get("done") is True:
                outputs = proposal.get("outputs", {})
                if not isinstance(outputs, dict) or any(
                    not isinstance(key, str) or not isinstance(value, str)
                    for key, value in outputs.items()
                ):
                    raise RuntimeError("browser worker returned invalid declared outputs")
                return {
                    "session_id": session.id,
                    "step_id": step.id,
                    "done": True,
                    **outputs,
                }
            action = proposal.get("action")
            if not isinstance(action, dict) or not isinstance(action.get("id"), str):
                raise RuntimeError("browser worker returned no deterministic action")
            if proposal.get("requires_confirmation") is True and approval is None:
                await self._sessions.freeze(session.organisation_id, session.id, session.revision)
                raise BrowserPauseError(
                    str(proposal.get("safety_explanation") or "browser action needs takeover"),
                    {
                        "session_id": session.id,
                        "action_id": action["id"],
                        "step_id": step.id,
                        "takeover_required": True,
                    },
                )
            execute_payload = {"action_id": action["id"], "confirmed": approval is not None}
            if step.secure_field is not None:
                if self._secret_access is None:
                    raise RuntimeError("browser secure capture authorization is unavailable")
                await self._secret_access.install(run, session)
            result = await self._post(
                run,
                session,
                "browser.execute",
                "/v1/steps/execute",
                execute_payload,
                approval,
            )
            session = BrowserSession.model_validate(result.get("session"))
            capture = result.get("capture")
            if capture is not None:
                captured = dict(capture) if isinstance(capture, dict) else {"capture": capture}
                return {
                    "session_id": session.id,
                    "step_id": step.id,
                    **captured,
                }
            if session.status is BrowserStatus.PAUSED:
                raise BrowserPauseError(
                    str(result.get("paused_reason") or "browser execution paused"),
                    {
                        "session_id": session.id,
                        "step_id": step.id,
                        "takeover_required": True,
                        "secure_field": (
                            step.secure_field.name if step.secure_field is not None else None
                        ),
                    },
                )
        raise RuntimeError("browser step exceeded the playbook action budget")

    async def terminate(self, run: RotationRun) -> None:
        session_id = _session_id(run.id)
        try:
            session = await self._catalog.get(
                FirestorePaths.browser(run.organisation_id, session_id), BrowserSession
            )
        except ResourceNotFoundError:
            return
        if session.status is not BrowserStatus.TERMINATED:
            session = await self._sessions.terminate(
                session.organisation_id, session.id, session.revision
            )
        if session.worker_instance:
            await self._vms.delete(session.worker_instance)

    async def _session(
        self,
        run: RotationRun,
        connection: Connection,
        version: PlaybookVersion,
        credential: ManagedCredential,
        protected_tools: frozenset[str],
    ) -> BrowserSession:
        session_id = _session_id(run.id)
        try:
            session = await self._catalog.get(
                FirestorePaths.browser(run.organisation_id, session_id), BrowserSession
            )
        except ResourceNotFoundError:
            session = None
        if (
            session is not None
            and session.status is not BrowserStatus.TERMINATED
            and session.policy.protected_tools != protected_tools
        ):
            raise RuntimeError("browser session restrictions differ from the pinned controls")
        if session is None or session.status is BrowserStatus.TERMINATED:
            now = datetime.now(UTC)
            if (
                connection.interface is not ConnectionInterface.BROWSER
                or ConnectionRole.PROVIDER not in connection.roles
                or connection.authorization is not ConnectionAuthorization.BROWSER_SESSION
                or connection.playbook_id != version.playbook_id
                or connection.playbook_version_id != version.id
            ):
                raise RuntimeError("browser run connection and published playbook differ")
            if (
                connection.status is not ConnectionStatus.READY
                or connection.authorization_reference is None
                or (
                    connection.authorization_expires_at is not None
                    and connection.authorization_expires_at <= now
                )
            ):
                raise BrowserPauseError(
                    "connection still needs login",
                    {
                        "authentication_required": True,
                        "session_id": session_id,
                        "connection_id": connection.id,
                    },
                )
            policy = BrowserPolicy(
                allowed_domains=version.definition.allowed_domains,
                allowed_actions=frozenset(BrowserActionKind),
                protected_tools=protected_tools,
                login_url_pattern=version.definition.login_url_pattern,
            )
            if session is None:
                session = await self._sessions.create(
                    BrowserSession(
                        id=session_id,
                        organisation_id=run.organisation_id,
                        run_id=run.id,
                        playbook_id=version.playbook_id,
                        playbook_version=version.id,
                        provider_connection_id=connection.id,
                        secret_store_connection_id=credential.secret_store_connection_id,
                        secret_resource=credential.secret_reference,
                        status=BrowserStatus.PROVISIONING,
                        policy=policy,
                        fencing_token=run.fencing_token,
                        created_at=now,
                        expires_at=now + timedelta(hours=2),
                        updated_at=now,
                    )
                )
            else:
                session = await self._sessions.reprovision(
                    session.organisation_id,
                    session.id,
                    session.revision,
                    connection.id,
                    version.playbook_id,
                    version.id,
                    credential.secret_store_connection_id,
                    credential.secret_reference,
                    policy,
                    run.fencing_token,
                    now + timedelta(hours=2),
                )
            vm = await self._vms.create(run.organisation_id, session.id, session.expires_at)
            session = await self._sessions.attach(
                session.organisation_id,
                session.id,
                session.revision,
                vm.instance,
                vm.internal_address,
            )
            await self._wait_ready(vm.internal_address)
        if session.fencing_token != run.fencing_token:
            session = await self._sessions.rebind_fence(
                session.organisation_id,
                session.id,
                session.revision,
                run.fencing_token,
            )
        if session.status in {BrowserStatus.READY, BrowserStatus.PAUSED}:
            session = await self._sessions.start(
                session.organisation_id, session.id, session.revision
            )
        return session

    async def _post(
        self,
        run: RotationRun,
        session: BrowserSession,
        tool: str,
        path: str,
        payload: dict[str, Any],
        approval: Approval | None = None,
    ) -> dict[str, Any]:
        action_digest = request_digest(tool, payload)
        if approval is not None:
            action = await self._catalog.get(
                FirestorePaths.action(run.organisation_id, approval.action_id),
                ProtectedAction,
            )
            action_digest = digest(action)
        capability = self._signer.mint(
            CapabilityClaims(
                organisation_id=run.organisation_id,
                run_id=run.id,
                agent_id="coordinator_one",
                tool=tool,
                connection_id=session.id,
                stage=run.stage,
                fencing_token=run.fencing_token,
                request_digest=request_digest(tool, payload),
                action_digest=action_digest,
                expires_at=int((datetime.now(UTC) + timedelta(minutes=2)).timestamp()),
                nonce=new_id("browsercap"),
                approval_id=approval.id if approval is not None else None,
            )
        )
        response = await self._http.post(
            f"http://{session.internal_address}:8080{path}",
            headers={"X-FireKey-Capability": capability},
            json=payload,
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            if error.response.status_code == 409 and _code(error.response) == (
                "authentication-required"
            ):
                raise BrowserPauseError(
                    "provider session requires reauthentication",
                    {
                        "authentication_required": True,
                        "session_id": session.id,
                        "connection_id": session.provider_connection_id,
                    },
                ) from error
            raise
        value = response.json()
        if not isinstance(value, dict):
            raise RuntimeError("browser worker returned a non-object response")
        return value

    async def _wait_ready(self, address: str) -> None:
        for _ in range(60):
            try:
                response = await self._http.get(f"http://{address}:8080/health/live")
                if response.status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(2)
        raise RuntimeError("browser VM worker did not become ready")


def _session_id(run_id: str) -> str:
    return f"browser_{run_id.removeprefix('run_')}"[:128]


def _code(response: httpx.Response) -> str | None:
    try:
        body = response.json()
    except ValueError:
        return None
    code = body.get("code") if isinstance(body, dict) else None
    return code if isinstance(code, str) else None


def _safe_parameters(value: Mapping[str, object]) -> str:
    safe = {
        key: item
        for key, item in value.items()
        if key.lower() not in {"secret", "password", "token", "value"}
    }
    return json.dumps(safe, separators=(",", ":"), sort_keys=True)
