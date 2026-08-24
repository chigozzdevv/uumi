from collections.abc import Mapping
from typing import Any

import httpx
import pytest
from core.auth import AuthenticatedIdentity
from fastapi import FastAPI
from fastapi.testclient import TestClient
from web.app import create_app
from web.config import WebSettings


class Verifier:
    async def verify(self, token: str) -> AuthenticatedIdentity:
        assert token == "firebase-token"
        return AuthenticatedIdentity(
            subject="user_1",
            issuer="https://securetoken.google.com/useuumi",
            email="user@example.com",
            email_verified=True,
            display_name="User",
            connected_via="Google",
        )


class Client:
    def __init__(self) -> None:
        self.requests: list[Mapping[str, Any]] = []

    async def request(
        self,
        method: str,
        url: str,
        *,
        content: bytes,
        headers: dict[str, str],
    ) -> httpx.Response:
        self.requests.append({"method": method, "url": url, "content": content, "headers": headers})
        return httpx.Response(
            200,
            json={"ok": True},
            headers={"content-type": "application/json", "x-private": "hidden"},
        )

    async def aclose(self) -> None:
        return None


async def service_token(audience: str) -> str:
    assert audience == "https://uumi-api.example.run.app"
    return "cloud-run-token"


@pytest.fixture
def upstream() -> Client:
    return Client()


@pytest.fixture
def app(upstream: Client) -> FastAPI:
    return create_app(
        WebSettings(
            project_id="useuumi",
            api_url="https://uumi-api.example.run.app",
        ),
        Verifier(),
        service_token,
        upstream,
    )


def test_rejects_missing_identity_token(app: FastAPI) -> None:
    with TestClient(app) as client:
        response = client.get("/v1/session")
    assert response.status_code == 401
    assert response.json()["detail"] == "bearer identity token is required"


def test_forwards_firebase_and_cloud_run_tokens(app: FastAPI, upstream: Client) -> None:
    with TestClient(app) as client:
        response = client.post(
            "/v1/organisations?source=auth",
            headers={
                "Authorization": "Bearer firebase-token",
                "Idempotency-Key": "request-123",
                "X-Secret": "do-not-forward",
            },
            json={"name": "Acme"},
        )
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    forwarded = upstream.requests[0]
    assert forwarded["url"] == "https://uumi-api.example.run.app/v1/organisations?source=auth"
    assert forwarded["headers"] == {
        "Authorization": "Bearer firebase-token",
        "X-Serverless-Authorization": "Bearer cloud-run-token",
        "accept": "*/*",
        "content-type": "application/json",
        "idempotency-key": "request-123",
    }


def test_strips_private_upstream_headers(app: FastAPI) -> None:
    with TestClient(app) as client:
        response = client.get(
            "/v1/session",
            headers={"Authorization": "Bearer firebase-token"},
        )
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-type"].startswith("application/json")
    assert "x-private" not in response.headers
