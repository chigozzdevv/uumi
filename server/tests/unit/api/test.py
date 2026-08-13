from datetime import UTC, datetime, timedelta

import httpx
import pytest
from api.app import create_app
from api.deps import ApiServices
from core.auth import (
    AccessControl,
    AuthenticatedIdentity,
    PrincipalGrant,
    Role,
)
from core.errors import AuthenticationError
from core.workflow import RunWorkflow
from fastapi import FastAPI
from testkit import MemoryRunRepository

NOW = datetime(2026, 8, 13, 12, tzinfo=UTC)
IDENTITY = AuthenticatedIdentity(
    subject="107777777777777777777",
    issuer="https://accounts.google.com",
    email="workflow@example.iam.gserviceaccount.com",
)


class TokenVerifier:
    async def verify(self, token: str) -> AuthenticatedIdentity:
        if token != "valid-token":
            raise AuthenticationError("identity token is invalid")
        return IDENTITY


class AccessRepository:
    def __init__(self, role: Role) -> None:
        self._role = role

    async def get(
        self,
        organisation_id: str,
        identity: AuthenticatedIdentity,
    ) -> PrincipalGrant | None:
        if organisation_id != "org_one" or identity != IDENTITY:
            return None
        return PrincipalGrant(subject=identity.subject, roles=frozenset({self._role}))


def app(role: Role = Role.OPERATOR) -> FastAPI:
    repository = MemoryRunRepository()
    workflow = RunWorkflow(
        repository,
        clock=lambda: NOW,
        id_factory=lambda prefix: f"{prefix}_one",
    )
    services = ApiServices(
        workflow=workflow,
        access=AccessControl(AccessRepository(role)),
        tokens=TokenVerifier(),
    )
    return create_app(services)


def headers(key: str = "request-one") -> dict[str, str]:
    return {
        "Authorization": "Bearer valid-token",
        "Idempotency-Key": key,
    }


def create_body() -> dict[str, str]:
    return {
        "credential_id": "cred_one",
        "policy_version": "policy_one",
        "source": "schedule",
        "event_id": "event-one",
        "reason": "routine rotation",
        "urgency": "routine",
        "received_at": NOW.isoformat(),
    }


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_run_routes_require_identity() -> None:
    transport = httpx.ASGITransport(app=app(), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/organisations/org_one/runs",
            headers={"Idempotency-Key": "request-one"},
            json=create_body(),
        )

    assert response.status_code == 401
    assert response.json()["code"] == "unauthenticated"


@pytest.mark.anyio
async def test_viewer_cannot_create_run() -> None:
    transport = httpx.ASGITransport(
        app=app(Role.VIEWER),
        raise_app_exceptions=False,
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/organisations/org_one/runs",
            headers=headers(),
            json=create_body(),
        )

    assert response.status_code == 403
    assert response.json()["code"] == "forbidden"


@pytest.mark.anyio
async def test_create_and_start_are_authenticated_and_idempotent() -> None:
    transport = httpx.ASGITransport(app=app(), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/v1/organisations/org_one/runs",
            headers=headers(),
            json=create_body(),
        )
        run = created.json()["run"]
        started = await client.post(
            f"/v1/organisations/org_one/runs/{run['id']}/start",
            headers=headers("request-start"),
            json={
                "expected_revision": run["revision"],
                "expires_at": (NOW + timedelta(hours=1)).isoformat(),
            },
        )
        duplicate = await client.post(
            f"/v1/organisations/org_one/runs/{run['id']}/start",
            headers=headers("request-start"),
            json={
                "expected_revision": run["revision"],
                "expires_at": (NOW + timedelta(hours=1)).isoformat(),
            },
        )

    assert created.status_code == 201
    assert run["trigger"]["actor_id"] == IDENTITY.actor_id
    assert started.status_code == 200
    assert started.json()["applied"] is True
    assert started.json()["run"]["lease"]["owner_id"] == IDENTITY.actor_id
    assert duplicate.status_code == 200
    assert duplicate.json()["applied"] is False
