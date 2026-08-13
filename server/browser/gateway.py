import asyncio
import ipaddress
import json
from datetime import datetime
from typing import Any, Protocol

from broker.capability import CapabilityVerifier, request_digest
from contracts import BrowserSession, RotationRun
from core.auth import AccessControl, AuthenticatedIdentity, IdentityTokenVerifier, Permission
from core.errors import AuthenticationError, CapabilityError, ResourceConflictError
from fastapi import WebSocket
from websockets.asyncio.client import ClientConnection, connect


class GatewayRepository(Protocol):
    async def browser(self, organisation_id: str, session_id: str) -> BrowserSession: ...

    async def run(self, organisation_id: str, run_id: str) -> RotationRun: ...


class BrowserSessionGateway:
    def __init__(
        self,
        repository: GatewayRepository,
        access: AccessControl,
        identities: IdentityTokenVerifier,
        capabilities: CapabilityVerifier,
    ) -> None:
        self._repository = repository
        self._access = access
        self._identities = identities
        self._capabilities = capabilities

    async def bridge(self, websocket: WebSocket) -> None:
        assertion = websocket.headers.get("x-goog-iap-jwt-assertion")
        if not assertion:
            await websocket.close(code=4401, reason="IAP identity is required")
            return
        try:
            identity = await self._identities.verify(assertion)
        except AuthenticationError:
            await websocket.close(code=4401, reason="IAP identity is invalid")
            return
        await websocket.accept()
        try:
            initial = await websocket.receive_json()
            organisation_id, session, mode, capability = await self._authorise(initial, identity)
            await self._access.require(identity, organisation_id, Permission.RUN_READ)
            address = _private_address(session.internal_address)
            async with connect(
                f"ws://{address}:8080/v1/live",
                additional_headers={"x-firekey-capability": capability},
                open_timeout=10,
                max_size=5 * 1024 * 1024,
            ) as worker:
                await asyncio.gather(
                    self._worker_to_user(worker, websocket),
                    self._user_to_worker(websocket, worker, mode),
                )
        except Exception as error:
            await websocket.close(code=4403, reason=_safe_error(error))

    async def _authorise(
        self, initial: Any, identity: AuthenticatedIdentity
    ) -> tuple[str, BrowserSession, str, str]:
        if not isinstance(initial, dict):
            raise CapabilityError("browser gateway handshake is invalid")
        organisation_id = _string(initial, "organisation_id")
        session_id = _string(initial, "session_id")
        mode = _string(initial, "mode")
        capability = _string(initial, "capability")
        if mode not in {"view", "takeover"}:
            raise CapabilityError("browser gateway mode is invalid")
        session = await self._repository.browser(organisation_id, session_id)
        run = await self._repository.run(organisation_id, session.run_id)
        claims = self._capabilities.verify(capability, datetime.now().astimezone())
        expected_digest = request_digest(
            f"browser.{mode}", {"session_id": session.id, "subject": identity.subject}
        )
        expected = (
            organisation_id,
            run.id,
            identity.actor_id,
            f"browser.{mode}",
            session.id,
            run.stage,
            session.fencing_token,
            expected_digest,
        )
        actual = (
            claims.organisation_id,
            claims.run_id,
            claims.agent_id,
            claims.tool,
            claims.connection_id,
            claims.stage,
            claims.fencing_token,
            claims.request_digest,
        )
        if actual != expected:
            raise CapabilityError("browser capability does not bind this identity and session")
        return organisation_id, session, mode, capability

    async def _worker_to_user(self, worker: ClientConnection, websocket: WebSocket) -> None:
        async for message in worker:
            if isinstance(message, bytes):
                await websocket.send_bytes(message)
            else:
                await websocket.send_text(message)

    async def _user_to_worker(
        self,
        websocket: WebSocket,
        worker: ClientConnection,
        mode: str,
    ) -> None:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                await worker.close()
                return
            text = message.get("text")
            data = message.get("bytes")
            if mode == "view":
                if text != '{"type":"frame"}':
                    raise CapabilityError("view-only session cannot send browser input")
                await worker.send(text)
            elif isinstance(text, str):
                value = json.loads(text)
                if not isinstance(value, dict) or value.get("type") not in {
                    "frame",
                    "action",
                    "secure-key",
                    "secure-input",
                }:
                    raise CapabilityError("takeover message type is invalid")
                await worker.send(text)
            elif isinstance(data, bytes):
                await worker.send(data)
            else:
                raise CapabilityError("browser gateway received an invalid frame")


def _private_address(value: str | None) -> str:
    if value is None:
        raise ResourceConflictError("browser VM has no internal address")
    address = ipaddress.ip_address(value)
    if not address.is_private:
        raise ResourceConflictError("browser gateway refuses a public worker address")
    return str(address)


def _string(value: dict[str, Any], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result:
        raise CapabilityError(f"browser gateway field {key} is required")
    return result


def _safe_error(error: Exception) -> str:
    if isinstance(error, AuthenticationError | CapabilityError | ResourceConflictError):
        return str(error)[:120]
    return "browser gateway failed"
