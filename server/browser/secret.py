import asyncio
import base64
import json
import os
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol, TypeVar

import httpx
from broker.capability import CapabilityClaims, CapabilitySigner, request_digest
from connectors.google import GoogleRestClient
from contracts import (
    BrowserSecretAccessEnvelope,
    BrowserSecretAccessReceipt,
    BrowserSecretKey,
    BrowserSecretKeyRequest,
    BrowserSession,
    Connection,
    ConnectionAuthorization,
    ConnectionInterface,
    ConnectionRole,
    ConnectionStatus,
    Contract,
    RotationRun,
)
from core.ids import new_id
from core.storage.paths import FirestorePaths
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

T = TypeVar("T", bound=Contract)
SignerLoader = Callable[[], Awaitable[CapabilitySigner]]


class SecretAccessCatalog(Protocol):
    async def get(self, path: str, model: type[T]) -> T: ...


class SecretAccessInstaller(Protocol):
    async def install(self, run: RotationRun, session: BrowserSession) -> datetime: ...


class BrowserSecretAccessService:
    def __init__(
        self,
        catalog: SecretAccessCatalog,
        google: GoogleRestClient,
        signer_loader: SignerLoader,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._catalog = catalog
        self._google = google
        self._signer_loader = signer_loader
        self._clock = clock
        self._http = http or httpx.AsyncClient(timeout=30)
        self._signer: CapabilitySigner | None = None
        self._lock = asyncio.Lock()

    async def install(self, run: RotationRun, session: BrowserSession) -> datetime:
        if session.internal_address is None:
            raise RuntimeError("browser session has no worker address")
        connection = await self._catalog.get(
            FirestorePaths.connection(run.organisation_id, session.secret_store_connection_id),
            Connection,
        )
        _validate_sink(connection, session)
        key_request = BrowserSecretKeyRequest(
            session_id=session.id,
            secret_store_connection_id=connection.id,
            secret_resource=session.secret_resource,
        )
        key_response = await self._post(
            run,
            session,
            "browser.secret-key",
            "/v1/access/key",
            key_request.model_dump(mode="json"),
        )
        key = BrowserSecretKey.model_validate(key_response)
        token, token_expiry = await self._google.mint_access_token_for(connection)
        expires_at = min(token_expiry, session.expires_at, self._clock() + timedelta(minutes=10))
        try:
            envelope = _encrypt_access(key, key_request, token.bytes(), expires_at)
        finally:
            token.clear()
        receipt = BrowserSecretAccessReceipt.model_validate(
            await self._post(
                run,
                session,
                "browser.secret-access",
                "/v1/access/secret",
                envelope.model_dump(mode="json"),
            )
        )
        if receipt.expires_at != expires_at:
            raise RuntimeError("browser worker changed the ephemeral authorization expiry")
        return expires_at

    async def _post(
        self,
        run: RotationRun,
        session: BrowserSession,
        tool: str,
        path: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        signer = await self._get_signer()
        checksum = request_digest(tool, payload)
        capability = signer.mint(
            CapabilityClaims(
                organisation_id=run.organisation_id,
                run_id=run.id,
                agent_id="secret_access_broker",
                tool=tool,
                connection_id=session.id,
                stage=run.stage,
                fencing_token=run.fencing_token,
                request_digest=checksum,
                action_digest=checksum,
                expires_at=int((self._clock() + timedelta(minutes=2)).timestamp()),
                nonce=new_id("browsercap"),
            )
        )
        response = await self._http.post(
            f"http://{session.internal_address}:8080{path}",
            headers={"X-FireKey-Capability": capability},
            json=payload,
        )
        response.raise_for_status()
        value = response.json()
        if not isinstance(value, dict):
            raise RuntimeError("browser worker returned an invalid secret-access response")
        return value

    async def _get_signer(self) -> CapabilitySigner:
        if self._signer is not None:
            return self._signer
        async with self._lock:
            if self._signer is None:
                self._signer = await self._signer_loader()
            return self._signer


def _validate_sink(connection: Connection, session: BrowserSession) -> None:
    allowed = any(
        session.secret_resource == boundary
        or session.secret_resource.startswith(boundary.rstrip("/") + "/")
        for boundary in connection.allowed_resources
    )
    if (
        connection.id != session.secret_store_connection_id
        or connection.platform not in {"google-secret-manager", "google-cloud"}
        or ConnectionRole.SECRET_STORE not in connection.roles
        or connection.interface is not ConnectionInterface.API
        or connection.authorization is not ConnectionAuthorization.WORKLOAD_IDENTITY
        or connection.status is not ConnectionStatus.READY
        or not allowed
    ):
        raise RuntimeError("browser session secret-store authorization is invalid")


def _encrypt_access(
    key: BrowserSecretKey,
    request: BrowserSecretKeyRequest,
    token: bytes,
    expires_at: datetime,
) -> BrowserSecretAccessEnvelope:
    public = serialization.load_pem_public_key(key.public_key.encode())
    if not isinstance(public, rsa.RSAPublicKey) or public.key_size < 3072:
        raise RuntimeError("browser worker returned an invalid ephemeral encryption key")
    aes_key = bytearray(AESGCM.generate_key(bit_length=256))
    nonce = bytearray(os.urandom(12))
    associated = _associated_data(request, expires_at)
    try:
        ciphertext = AESGCM(bytes(aes_key)).encrypt(bytes(nonce), token, associated)
        encrypted_key = public.encrypt(
            bytes(aes_key),
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None,
            ),
        )
        return BrowserSecretAccessEnvelope(
            **request.model_dump(),
            expires_at=expires_at,
            encrypted_key=base64.b64encode(encrypted_key).decode(),
            nonce=base64.b64encode(nonce).decode(),
            ciphertext=base64.b64encode(ciphertext).decode(),
        )
    finally:
        for index in range(len(aes_key)):
            aes_key[index] = 0
        for index in range(len(nonce)):
            nonce[index] = 0


def associated_data(envelope: BrowserSecretAccessEnvelope) -> bytes:
    return _associated_data(
        BrowserSecretKeyRequest(
            session_id=envelope.session_id,
            secret_store_connection_id=envelope.secret_store_connection_id,
            secret_resource=envelope.secret_resource,
        ),
        envelope.expires_at,
    )


def _associated_data(request: BrowserSecretKeyRequest, expires_at: datetime) -> bytes:
    return json.dumps(
        {
            **request.model_dump(mode="json"),
            "expires_at": expires_at.isoformat(),
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
