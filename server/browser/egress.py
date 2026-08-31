import asyncio
import ipaddress
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol

from connectors.base.errors import ConnectorError
from connectors.google import GoogleRestClient
from contracts import Contract
from core.ids import new_id
from core.storage.codec import encode
from core.storage.paths import FirestorePaths
from google.cloud.firestore_v1 import AsyncClient
from google.cloud.firestore_v1.async_transaction import AsyncTransaction, async_transactional
from pydantic import AwareDatetime, Field, model_validator

_PROVISIONING_TIMEOUT = timedelta(minutes=15)
_WAIT_SECONDS = 900
_GOOGLE_DOMAINS = ("googleapis.com", "gstatic.com", "gcr.io", "pkg.dev")


class BrowserEgressStatus(StrEnum):
    ABSENT = "absent"
    PROVISIONING = "provisioning"
    READY = "ready"
    DELETING = "deleting"
    FAILED = "failed"


class BrowserEgressLease(Contract):
    id: str = Field(min_length=1, max_length=512)
    expires_at: AwareDatetime


class BrowserEgressState(Contract):
    id: str = Field(min_length=1, max_length=64)
    status: BrowserEgressStatus
    leases: tuple[BrowserEgressLease, ...] = ()
    operation_token: str | None = Field(default=None, max_length=128)
    gateway_address: str | None = Field(default=None, max_length=64)
    error: str | None = Field(default=None, max_length=1024)
    updated_at: AwareDatetime
    revision: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_state(self) -> "BrowserEgressState":
        if (
            self.status
            in {
                BrowserEgressStatus.PROVISIONING,
                BrowserEgressStatus.DELETING,
            }
            and self.operation_token is None
        ):
            raise ValueError("an in-progress browser egress transition requires an operation token")
        if self.status is BrowserEgressStatus.READY and self.gateway_address is None:
            raise ValueError("ready browser egress requires a gateway address")
        return self


class BrowserEgressStore(Protocol):
    async def get(self) -> BrowserEgressState | None: ...

    async def update(
        self,
        change: Callable[[BrowserEgressState | None], BrowserEgressState],
    ) -> BrowserEgressState: ...


class FirestoreBrowserEgressStore:
    def __init__(self, client: AsyncClient, region: str) -> None:
        self._client = client
        self._path = FirestorePaths.browser_egress(region)

    async def get(self) -> BrowserEgressState | None:
        snapshot = await self._client.document(self._path).get()
        data = snapshot.to_dict() if snapshot.exists else None
        return BrowserEgressState.model_validate(data) if data is not None else None

    async def update(
        self,
        change: Callable[[BrowserEgressState | None], BrowserEgressState],
    ) -> BrowserEgressState:
        reference = self._client.document(self._path)

        @async_transactional
        async def apply(transaction: AsyncTransaction) -> BrowserEgressState:
            snapshot = await reference.get(transaction=transaction)
            data = snapshot.to_dict() if snapshot.exists else None
            current = BrowserEgressState.model_validate(data) if data is not None else None
            changed = change(current)
            expected = 1 if current is None else current.revision + 1
            if changed.revision != expected:
                raise RuntimeError("browser egress update did not advance revision once")
            transaction.set(reference, encode(changed))
            return changed

        return await apply(self._client.transaction(max_attempts=5))


class BrowserEgressManager:
    def __init__(
        self,
        store: BrowserEgressStore,
        client: GoogleRestClient,
        project_id: str,
        region: str,
        network: str,
        subnetwork: str,
        worker_service_account: str,
        approved_domains: tuple[str, ...],
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if not approved_domains:
            raise ValueError("browser egress requires at least one approved provider domain")
        self._store = store
        self._client = client
        self._project = project_id
        self._region = region
        self._network = network
        self._subnetwork = subnetwork
        self._worker_service_account = worker_service_account
        self._approved_domains = tuple(dict.fromkeys((*approved_domains, *_GOOGLE_DOMAINS)))
        self._clock = clock

    async def acquire(
        self,
        lease_id: str,
        expires_at: datetime,
        required_domains: tuple[str, ...],
    ) -> None:
        self._validate_domains(required_domains)
        deadline = self._clock() + timedelta(seconds=_WAIT_SECONDS)
        token = new_id("egressop")
        state = await self._store.update(
            lambda current: self._request_acquire(current, lease_id, expires_at, token)
        )
        while self._clock() < deadline:
            if state.status is BrowserEgressStatus.READY:
                return
            if state.status is BrowserEgressStatus.PROVISIONING and (
                state.operation_token == token
            ):
                try:
                    await self._provision(token)
                except ConnectorError as error:
                    if not error.retryable:
                        raise
            await asyncio.sleep(2)
            current = await self._store.get()
            if current is None:
                token = new_id("egressop")
                state = await self._store.update(self._acquire_change(lease_id, expires_at, token))
                continue
            stale = current.updated_at + _PROVISIONING_TIMEOUT <= self._clock()
            if current.status in {BrowserEgressStatus.ABSENT, BrowserEgressStatus.FAILED} or (
                current.status is BrowserEgressStatus.PROVISIONING and stale
            ):
                token = new_id("egressop")
                state = await self._store.update(self._acquire_change(lease_id, expires_at, token))
                continue
            state = current
        raise ConnectorError(
            "browser-egress-timeout",
            "browser egress did not become ready",
            retryable=True,
        )

    async def release(self, lease_id: str) -> None:
        token = new_id("egressop")
        state = await self._store.update(
            lambda current: self._request_release(current, lease_id, token)
        )
        if state.status is BrowserEgressStatus.DELETING and state.operation_token == token:
            await self._delete(token)

    async def reconcile(self) -> None:
        token = new_id("egressop")
        state = await self._store.update(lambda current: self._request_reconcile(current, token))
        if state.operation_token != token:
            return
        if state.status is BrowserEgressStatus.PROVISIONING:
            await self._provision(token)
        elif state.status is BrowserEgressStatus.DELETING:
            await self._delete(token)

    async def _provision(self, token: str) -> None:
        try:
            address = await self._ensure_resources()
        except Exception as error:
            await self._store.update(self._failure_change(token, error))
            raise
        state = await self._store.update(
            lambda current: self._finish_provision(current, token, address)
        )
        if state.status is BrowserEgressStatus.DELETING and state.operation_token == token:
            await self._delete(token)

    async def _delete(self, token: str) -> None:
        try:
            await self._delete_resources()
        except Exception as error:
            await self._store.update(self._failure_change(token, error))
            raise
        await self._store.update(lambda current: self._finish_delete(current, token))

    def _request_acquire(
        self,
        current: BrowserEgressState | None,
        lease_id: str,
        expires_at: datetime,
        token: str,
    ) -> BrowserEgressState:
        now = self._clock()
        state = self._current(current, now)
        leases = {
            lease.id: lease
            for lease in state.leases
            if lease.expires_at > now and lease.id != lease_id
        }
        leases[lease_id] = BrowserEgressLease(id=lease_id, expires_at=expires_at)
        status = state.status
        operation_token = state.operation_token
        gateway_address = state.gateway_address
        stale = state.updated_at + _PROVISIONING_TIMEOUT <= now
        if status in {BrowserEgressStatus.ABSENT, BrowserEgressStatus.FAILED} or (
            status is BrowserEgressStatus.PROVISIONING and stale
        ):
            status = BrowserEgressStatus.PROVISIONING
            operation_token = token
            gateway_address = None
        return state.model_copy(
            update={
                "status": status,
                "leases": tuple(sorted(leases.values(), key=lambda item: item.id)),
                "operation_token": operation_token,
                "gateway_address": gateway_address,
                "error": None,
                "updated_at": now,
                "revision": state.revision + 1,
            }
        )

    def _acquire_change(
        self,
        lease_id: str,
        expires_at: datetime,
        token: str,
    ) -> Callable[[BrowserEgressState | None], BrowserEgressState]:
        def change(current: BrowserEgressState | None) -> BrowserEgressState:
            return self._request_acquire(current, lease_id, expires_at, token)

        return change

    def _request_release(
        self,
        current: BrowserEgressState | None,
        lease_id: str,
        token: str,
    ) -> BrowserEgressState:
        now = self._clock()
        state = self._current(current, now)
        leases = tuple(
            lease for lease in state.leases if lease.id != lease_id and lease.expires_at > now
        )
        status = state.status
        operation_token = state.operation_token
        if not leases and status in {
            BrowserEgressStatus.READY,
            BrowserEgressStatus.FAILED,
        }:
            status = BrowserEgressStatus.DELETING
            operation_token = token
        return state.model_copy(
            update={
                "status": status,
                "leases": leases,
                "operation_token": operation_token,
                "updated_at": now,
                "revision": state.revision + 1,
            }
        )

    def _request_reconcile(
        self,
        current: BrowserEgressState | None,
        token: str,
    ) -> BrowserEgressState:
        now = self._clock()
        state = self._current(current, now)
        leases = tuple(lease for lease in state.leases if lease.expires_at > now)
        status = state.status
        operation_token = state.operation_token
        gateway_address = state.gateway_address
        stale = state.updated_at + _PROVISIONING_TIMEOUT <= now
        if leases and (
            status in {BrowserEgressStatus.ABSENT, BrowserEgressStatus.FAILED}
            or (status is BrowserEgressStatus.PROVISIONING and stale)
        ):
            status = BrowserEgressStatus.PROVISIONING
            operation_token = token
            gateway_address = None
        elif not leases and (
            status in {BrowserEgressStatus.READY, BrowserEgressStatus.FAILED}
            or (status is BrowserEgressStatus.PROVISIONING and stale)
        ):
            status = BrowserEgressStatus.DELETING
            operation_token = token
        return state.model_copy(
            update={
                "status": status,
                "leases": leases,
                "operation_token": operation_token,
                "gateway_address": gateway_address,
                "updated_at": now,
                "revision": state.revision + 1,
            }
        )

    def _finish_provision(
        self,
        current: BrowserEgressState | None,
        token: str,
        address: str,
    ) -> BrowserEgressState:
        now = self._clock()
        state = self._current(current, now)
        if state.operation_token != token or state.status is not BrowserEgressStatus.PROVISIONING:
            return state.model_copy(update={"revision": state.revision + 1})
        leases = tuple(lease for lease in state.leases if lease.expires_at > now)
        return state.model_copy(
            update={
                "status": (BrowserEgressStatus.READY if leases else BrowserEgressStatus.DELETING),
                "leases": leases,
                "gateway_address": address,
                "error": None,
                "updated_at": now,
                "revision": state.revision + 1,
            }
        )

    def _finish_delete(
        self,
        current: BrowserEgressState | None,
        token: str,
    ) -> BrowserEgressState:
        now = self._clock()
        state = self._current(current, now)
        if state.operation_token != token or state.status is not BrowserEgressStatus.DELETING:
            return state.model_copy(update={"revision": state.revision + 1})
        leases = tuple(lease for lease in state.leases if lease.expires_at > now)
        return state.model_copy(
            update={
                "status": BrowserEgressStatus.ABSENT,
                "leases": leases,
                "operation_token": None,
                "gateway_address": None,
                "error": None,
                "updated_at": now,
                "revision": state.revision + 1,
            }
        )

    def _failed(
        self,
        current: BrowserEgressState | None,
        token: str,
        error: Exception,
    ) -> BrowserEgressState:
        now = self._clock()
        state = self._current(current, now)
        if state.operation_token != token:
            return state.model_copy(update={"revision": state.revision + 1})
        return state.model_copy(
            update={
                "status": BrowserEgressStatus.FAILED,
                "operation_token": None,
                "error": f"{type(error).__name__}: {error}"[:1024],
                "updated_at": now,
                "revision": state.revision + 1,
            }
        )

    def _failure_change(
        self,
        token: str,
        error: Exception,
    ) -> Callable[[BrowserEgressState | None], BrowserEgressState]:
        def change(current: BrowserEgressState | None) -> BrowserEgressState:
            return self._failed(current, token, error)

        return change

    def _current(
        self,
        current: BrowserEgressState | None,
        now: datetime,
    ) -> BrowserEgressState:
        return current or BrowserEgressState(
            id=self._region,
            status=BrowserEgressStatus.ABSENT,
            updated_at=now,
        )

    def _validate_domains(self, required: tuple[str, ...]) -> None:
        for requested in required:
            domain = requested.removeprefix("*.")
            if not any(
                domain == approved or domain.endswith(f".{approved}")
                for approved in self._approved_domains
            ):
                raise ConnectorError(
                    "browser-egress-domain",
                    f"browser domain {requested} is outside the approved egress boundary",
                )

    async def _ensure_resources(self) -> str:
        security_base = (
            f"https://networksecurity.googleapis.com/v1/projects/{self._project}"
            f"/locations/{self._region}"
        )
        policy_name = (
            f"projects/{self._project}/locations/{self._region}"
            "/gatewaySecurityPolicies/uumi-browser-egress"
        )
        policy_url = f"https://networksecurity.googleapis.com/v1/{policy_name}"
        await self._ensure(
            policy_url,
            f"{security_base}/gatewaySecurityPolicies",
            "gatewaySecurityPolicyId",
            "uumi-browser-egress",
            {"description": "On-demand default-deny browser egress policy."},
            "https://networksecurity.googleapis.com/v1",
        )
        matcher = " || ".join(
            clause
            for domain in self._approved_domains
            for clause in (f"host() == '{domain}'", f"host().endsWith('.{domain}')")
        )
        rule_name = f"{policy_name}/rules/allow-approved-domains"
        await self._ensure(
            f"https://networksecurity.googleapis.com/v1/{rule_name}",
            f"{policy_url}/rules",
            "gatewaySecurityPolicyRuleId",
            "allow-approved-domains",
            {
                "enabled": True,
                "priority": 100,
                "sessionMatcher": (
                    f"source.matchServiceAccount('{self._worker_service_account}') && ({matcher})"
                ),
                "basicProfile": "ALLOW",
            },
            "https://networksecurity.googleapis.com/v1",
        )
        gateway_name = (
            f"projects/{self._project}/locations/{self._region}/gateways/uumi-browser-egress"
        )
        gateway_url = f"https://networkservices.googleapis.com/v1/{gateway_name}"
        gateway = await self._ensure(
            gateway_url,
            (
                f"https://networkservices.googleapis.com/v1/projects/{self._project}"
                f"/locations/{self._region}/gateways"
            ),
            "gatewayId",
            "uumi-browser-egress",
            {
                "description": "On-demand next-hop Secure Web Proxy for browser workers.",
                "type": "SECURE_WEB_GATEWAY",
                "ports": [443],
                "scope": "uumi-browser",
                "gatewaySecurityPolicy": policy_name,
                "network": self._network,
                "subnetwork": self._subnetwork,
                "routingMode": "NEXT_HOP_ROUTING_MODE",
            },
            "https://networkservices.googleapis.com/v1",
            attempts=900,
        )
        address = _gateway_address(gateway)
        route_url = (
            f"https://compute.googleapis.com/compute/v1/projects/{self._project}"
            "/global/routes/uumi-browser-proxy"
        )
        try:
            await self._client.request("GET", route_url)
        except ConnectorError as error:
            if error.code != "google-api-404":
                raise
            operation = await self._client.request(
                "POST",
                f"https://compute.googleapis.com/compute/v1/projects/{self._project}/global/routes",
                json={
                    "name": "uumi-browser-proxy",
                    "description": "Routes active browser workers through Secure Web Proxy.",
                    "network": self._network,
                    "destRange": "0.0.0.0/0",
                    "priority": 100,
                    "tags": ["uumi-browser"],
                    "nextHopIlb": address,
                },
            )
            await self._client.wait_operation(
                _operation(operation),
                attempts=300,
                base_url=(
                    f"https://compute.googleapis.com/compute/v1/projects/{self._project}"
                    "/global/operations"
                ),
            )
        return address

    async def _ensure(
        self,
        resource_url: str,
        collection_url: str,
        id_parameter: str,
        resource_id: str,
        body: dict[str, Any],
        operations_url: str,
        attempts: int = 300,
    ) -> dict[str, Any]:
        try:
            return await self._client.request("GET", resource_url)
        except ConnectorError as error:
            if error.code != "google-api-404":
                raise
        try:
            operation = await self._client.request(
                "POST",
                collection_url,
                params={id_parameter: resource_id},
                json=body,
            )
        except ConnectorError as error:
            if error.code != "google-api-409":
                raise
            return await self._client.request("GET", resource_url)
        await self._client.wait_operation(
            _operation(operation), attempts=attempts, base_url=operations_url
        )
        return await self._client.request("GET", resource_url)

    async def _delete_resources(self) -> None:
        await self._delete_resource(
            (
                f"https://compute.googleapis.com/compute/v1/projects/{self._project}"
                "/global/routes/uumi-browser-proxy"
            ),
            (
                f"https://compute.googleapis.com/compute/v1/projects/{self._project}"
                "/global/operations"
            ),
            300,
        )
        await self._delete_resource(
            (
                f"https://networkservices.googleapis.com/v1/projects/{self._project}"
                f"/locations/{self._region}/gateways/uumi-browser-egress"
            ),
            "https://networkservices.googleapis.com/v1",
            900,
        )
        policy = (
            f"projects/{self._project}/locations/{self._region}"
            "/gatewaySecurityPolicies/uumi-browser-egress"
        )
        await self._delete_resource(
            f"https://networksecurity.googleapis.com/v1/{policy}/rules/allow-approved-domains",
            "https://networksecurity.googleapis.com/v1",
            300,
        )
        await self._delete_resource(
            f"https://networksecurity.googleapis.com/v1/{policy}",
            "https://networksecurity.googleapis.com/v1",
            300,
        )

    async def _delete_resource(self, url: str, operations_url: str, attempts: int) -> None:
        try:
            operation = await self._client.request("DELETE", url, expected=frozenset({200, 204}))
        except ConnectorError as error:
            if error.code == "google-api-404":
                return
            raise
        if operation:
            await self._client.wait_operation(
                _operation(operation), attempts=attempts, base_url=operations_url
            )


def _operation(value: dict[str, Any]) -> str:
    name = value.get("name")
    if not isinstance(name, str):
        raise ConnectorError("browser-egress-operation", "Google returned no operation name")
    return name


def _gateway_address(value: dict[str, Any]) -> str:
    addresses = value.get("addresses")
    if not isinstance(addresses, list) or len(addresses) != 1:
        raise ConnectorError("browser-egress-address", "Secure Web Proxy returned no address")
    address = addresses[0]
    if not isinstance(address, str):
        raise ConnectorError("browser-egress-address", "Secure Web Proxy address is invalid")
    try:
        return str(ipaddress.ip_address(address))
    except ValueError as error:
        raise ConnectorError(
            "browser-egress-address", "Secure Web Proxy address is invalid"
        ) from error
