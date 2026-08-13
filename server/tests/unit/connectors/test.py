import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from connectors.base import ConnectorContext
from connectors.base.errors import AmbiguousMutationError
from connectors.github import GitHubWebhook
from connectors.google import GoogleRestClient
from connectors.secrets import SecretManagerConnector
from connectors.sendgrid import SendGridConnector
from contracts import (
    Connection,
    ConnectionKind,
    ConnectionStatus,
    RunStatus,
    Stage,
)
from google.oauth2.credentials import Credentials
from testkit import make_run

NOW = datetime(2026, 8, 13, 12, tzinfo=UTC)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _context() -> ConnectorContext:
    connection = Connection(
        id="connection_one",
        organisation_id="org_one",
        kind=ConnectionKind.PROVIDER,
        provider="sendgrid",
        display_name="SendGrid production",
        auth_reference="projects/project-one/secrets/admin/versions/1",
        capabilities=SendGridConnector.tools,
        allowed_resources=("sendgrid",),
        status=ConnectionStatus.READY,
        region="us-east1",
        created_at=NOW,
        updated_at=NOW,
    )
    run = make_run(NOW).model_copy(
        update={
            "status": RunStatus.RUNNING,
            "stage": Stage.CREATE,
            "fencing_token": 1,
            "lease": {
                "owner_id": "worker_one",
                "fencing_token": 1,
                "expires_at": NOW,
            },
        }
    )
    return ConnectorContext(
        request_id="request_one",
        agent_id="operator_one",
        connection=connection,
        run=run,
        now=NOW,
        idempotency_key="request_one",
    )


@pytest.mark.anyio
async def test_secret_manager_transfers_bytes_without_returning_them() -> None:
    received: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        request.read()
        value = json.loads(request.content)
        assert isinstance(value, dict)
        received.update(value)
        return httpx.Response(
            200,
            json={
                "name": "projects/project-one/secrets/key/versions/7",
                "state": "ENABLED",
            },
        )

    client = _google(handler)
    connector = SecretManagerConnector(client)
    from connectors.base import SecretValue

    secret = SecretValue(b"secret-value")
    result = await connector.add_version("projects/project-one/secrets/key", secret)

    assert base64.b64decode(received["payload"]["data"]) == b"secret-value"
    assert result["secret_reference"].endswith("/versions/7")
    assert "secret-value" not in repr(result)
    secret.clear()
    with pytest.raises(RuntimeError, match="cleared"):
        secret.bytes()
    await client.close()


@pytest.mark.anyio
async def test_sendgrid_creation_returns_one_time_secret_for_direct_transfer() -> None:
    calls = 0

    def google_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith(":access")
        return httpx.Response(
            200,
            json={"payload": {"data": base64.b64encode(b"admin-key").decode()}},
        )

    def sendgrid_handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        assert request.headers["Authorization"] == "Bearer admin-key"
        calls += 1
        if request.method == "GET":
            return httpx.Response(200, json={"result": []})
        return httpx.Response(
            201,
            json={"api_key_id": "provider-key-one", "api_key": "one-time-key"},
        )

    google = _google(google_handler)
    connector = SendGridConnector(
        SecretManagerConnector(google),
        httpx.AsyncClient(
            base_url="https://api.sendgrid.com/v3",
            transport=httpx.MockTransport(sendgrid_handler),
        ),
    )
    response = await connector.execute(
        "provider.createCredential",
        {"name": "firekey-run-one", "scopes": ["mail.send"]},
        _context(),
    )

    assert calls == 2
    assert response.result == {
        "provider_id": "provider-key-one",
        "name": "firekey-run-one",
        "scopes": ["mail.send"],
    }
    assert response.secret is not None
    assert response.secret.bytes() == b"one-time-key"
    response.secret.clear()
    await google.close()


@pytest.mark.anyio
async def test_sendgrid_timeout_deletes_attributable_orphan_and_stops() -> None:
    listed = 0
    deleted: list[str] = []

    def google_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"payload": {"data": base64.b64encode(b"admin-key").decode()}},
        )

    def sendgrid_handler(request: httpx.Request) -> httpx.Response:
        nonlocal listed
        if request.method == "GET":
            listed += 1
            result = [] if listed == 1 else [{"api_key_id": "orphan-one", "name": "rotate"}]
            return httpx.Response(200, json={"result": result})
        if request.method == "DELETE":
            deleted.append(request.url.path)
            return httpx.Response(204)
        raise httpx.ReadTimeout("response lost", request=request)

    google = _google(google_handler)
    connector = SendGridConnector(
        SecretManagerConnector(google),
        httpx.AsyncClient(
            base_url="https://api.sendgrid.com/v3",
            transport=httpx.MockTransport(sendgrid_handler),
        ),
    )

    with pytest.raises(AmbiguousMutationError, match="reconciled"):
        await connector.execute(
            "provider.createCredential",
            {"name": "rotate", "scopes": ["mail.send"]},
            _context(),
        )

    assert deleted == ["/v3/api_keys/orphan-one"]
    await google.close()


@pytest.mark.anyio
async def test_sendgrid_stale_reconcile_cleans_provider_and_secret_orphans() -> None:
    listed_keys = 0
    listed_versions = 0
    deleted: list[str] = []
    disabled: list[str] = []

    def google_handler(request: httpx.Request) -> httpx.Response:
        nonlocal listed_versions
        if request.url.path.endswith(":access"):
            return httpx.Response(
                200,
                json={"payload": {"data": base64.b64encode(b"admin-key").decode()}},
            )
        if request.method == "GET" and request.url.path.endswith("/versions"):
            listed_versions += 1
            versions = [
                {
                    "name": "projects/project-one/secrets/key/versions/1",
                    "state": "ENABLED",
                }
            ]
            if listed_versions > 1:
                versions.append(
                    {
                        "name": "projects/project-one/secrets/key/versions/2",
                        "state": "ENABLED",
                    }
                )
            return httpx.Response(200, json={"versions": versions})
        if request.method == "POST" and request.url.path.endswith(":disable"):
            disabled.append(request.url.path)
            return httpx.Response(
                200,
                json={
                    "name": "projects/project-one/secrets/key/versions/2",
                    "state": "DISABLED",
                },
            )
        raise AssertionError(f"unexpected Google request {request.method} {request.url}")

    def sendgrid_handler(request: httpx.Request) -> httpx.Response:
        nonlocal listed_keys
        if request.method == "GET":
            listed_keys += 1
            keys = []
            if listed_keys > 1:
                keys.append({"api_key_id": "orphan-one", "name": "rotate"})
            return httpx.Response(200, json={"result": keys})
        if request.method == "DELETE":
            deleted.append(request.url.path)
            return httpx.Response(204)
        raise AssertionError(f"unexpected SendGrid request {request.method} {request.url}")

    google = _google(google_handler)
    connector = SendGridConnector(
        SecretManagerConnector(google),
        httpx.AsyncClient(
            base_url="https://api.sendgrid.com/v3",
            transport=httpx.MockTransport(sendgrid_handler),
        ),
    )
    payload = {
        "name": "rotate",
        "scopes": ["mail.send"],
        "secret_resource": "projects/project-one/secrets/key",
    }

    state = await connector.prepare("provider.createCredential", payload, _context())
    await connector.reconcile("provider.createCredential", payload, state, _context())

    assert deleted == ["/v3/api_keys/orphan-one"]
    assert disabled == ["/v1/projects/project-one/secrets/key/versions/2:disable"]
    await google.close()


def test_github_webhook_rejects_modified_payload() -> None:
    webhook = GitHubWebhook()
    body = b'{"action":"created"}'
    signature = "sha256=" + hmac.new(b"hook-secret", body, hashlib.sha256).hexdigest()

    webhook.verify(body, signature, b"hook-secret")
    with pytest.raises(ValueError, match="invalid"):
        webhook.verify(body + b" ", signature, b"hook-secret")


def _google(handler: Any) -> GoogleRestClient:
    return GoogleRestClient(
        credentials=Credentials(token="token"),  # type: ignore[no-untyped-call]
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
