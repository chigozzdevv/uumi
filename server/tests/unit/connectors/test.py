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
from connectors.http import HttpProviderConnector
from connectors.scc import SecurityCommandCenterFinding
from connectors.secrets import SecretManagerConnector
from contracts import (
    Connection,
    ConnectionKind,
    ConnectionStatus,
    HttpOperation,
    HttpProviderApi,
    RunStatus,
    Stage,
)
from google.oauth2.credentials import Credentials
from pydantic import ValidationError
from testkit import make_http_provider_api, make_run

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
        capabilities=HttpProviderConnector.tools,
        allowed_resources=("sendgrid",),
        http=make_http_provider_api(),
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
    connector = HttpProviderConnector(
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
    connector = HttpProviderConnector(
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
    connector = HttpProviderConnector(
        SecretManagerConnector(google),
        httpx.AsyncClient(
            base_url="https://api.sendgrid.com/v3",
            transport=httpx.MockTransport(sendgrid_handler),
        ),
    )
    payload: dict[str, Any] = {
        "name": "rotate",
        "scopes": ["mail.send"],
        "secret_resource": "projects/project-one/secrets/key",
    }

    state = await connector.prepare("provider.createCredential", payload, _context())
    await connector.reconcile("provider.createCredential", payload, state, _context())

    assert deleted == ["/v3/api_keys/orphan-one"]
    assert disabled == ["/v1/projects/project-one/secrets/key/versions/2:disable"]
    await google.close()


@pytest.mark.anyio
async def test_declared_header_auth_sends_the_configured_api_key() -> None:
    from contracts import HttpAuth, HttpAuthScheme

    seen: list[str] = []

    def google_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"payload": {"data": base64.b64encode(b"org-admin-token").decode()}},
        )

    def vendor_handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["X-Api-Key"])
        if request.method == "GET":
            return httpx.Response(200, json={"keys": []})
        return httpx.Response(
            201,
            json={"id": "key-one", "token": "workload-secret"},
        )

    api = HttpProviderApi(
        base_url="https://keys.vendor.example/v1",
        auth=HttpAuth(scheme=HttpAuthScheme.HEADER, header="X-Api-Key"),
        list_credentials=HttpOperation(
            method="GET",
            path="/keys",
            success_statuses=(200,),
            list_items="keys",
            provider_id_field="id",
            name_field="name",
        ),
        create_credential=HttpOperation(
            method="POST",
            path="/keys",
            success_statuses=(201,),
            body={"name": "${name}"},
            provider_id_field="id",
            secret_field="token",
            name_field="name",
        ),
        revoke_credential=HttpOperation(
            method="DELETE",
            path="/keys/{provider_id}",
            success_statuses=(204,),
        ),
    )
    context = _context()
    context = ConnectorContext(
        request_id=context.request_id,
        agent_id=context.agent_id,
        connection=context.connection.model_copy(
            update={"provider": "internal-vendor", "http": api}
        ),
        run=context.run,
        now=context.now,
        idempotency_key=context.idempotency_key,
    )
    google = _google(google_handler)
    connector = HttpProviderConnector(
        SecretManagerConnector(google),
        httpx.AsyncClient(transport=httpx.MockTransport(vendor_handler)),
    )
    response = await connector.execute(
        "provider.createCredential",
        {"name": "rotate"},
        context,
    )

    assert seen == ["org-admin-token", "org-admin-token"]
    assert response.result["provider_id"] == "key-one"
    assert response.secret is not None
    assert response.secret.bytes() == b"workload-secret"
    response.secret.clear()
    await google.close()


@pytest.mark.anyio
async def test_http_connector_encodes_provider_ids_before_building_paths() -> None:
    seen: list[bytes] = []

    def google_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"payload": {"data": base64.b64encode(b"admin-key").decode()}},
        )

    def provider_handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.raw_path)
        return httpx.Response(204)

    google = _google(google_handler)
    connector = HttpProviderConnector(
        SecretManagerConnector(google),
        httpx.AsyncClient(
            base_url="https://api.sendgrid.com/v3",
            transport=httpx.MockTransport(provider_handler),
        ),
    )

    response = await connector.execute(
        "provider.revokeCredential",
        {"provider_id": "key?scope=all/#fragment"},
        _context(),
    )

    assert response.result == {"provider_id": "key?scope=all/#fragment", "revoked": True}
    assert seen == [b"/v3/api_keys/key%3Fscope%3Dall%2F%23fragment"]
    await google.close()


def test_http_provider_contract_rejects_non_origin_base_urls_and_unsafe_paths() -> None:
    template = make_http_provider_api()

    with pytest.raises(ValidationError, match="without credentials or query data"):
        HttpProviderApi(
            **{**template.model_dump(), "base_url": "https://api.sendgrid.com/v3?tenant=attacker"},
        )

    with pytest.raises(ValidationError, match="origin-relative paths"):
        HttpOperation(
            method="GET",
            path="//attacker.example/keys",
            success_statuses=(200,),
        )

    with pytest.raises(ValidationError, match="invalid placeholder"):
        HttpOperation(
            method="GET",
            path="/keys/{provider-id}",
            success_statuses=(200,),
        )


def test_github_webhook_rejects_modified_payload() -> None:
    webhook = GitHubWebhook()
    body = b'{"action":"created"}'
    signature = "sha256=" + hmac.new(b"hook-secret", body, hashlib.sha256).hexdigest()

    webhook.verify(body, signature, b"hook-secret")
    with pytest.raises(ValueError, match="invalid"):
        webhook.verify(body + b" ", signature, b"hook-secret")


def test_github_retry_is_stable_but_reopened_occurrence_is_distinct() -> None:
    webhook = GitHubWebhook()
    payload: dict[str, Any] = {
        "action": "created",
        "alert": {
            "number": 7,
            "html_url": "https://github.com/example/mailer/security/secret-scanning/7",
            "secret_type_display_name": "SendGrid API Key",
            "created_at": NOW.isoformat(),
            "updated_at": NOW.isoformat(),
        },
        "repository": {"full_name": "example/mailer"},
    }

    first = webhook.normalise("org_one", "secret_scanning_alert", json.dumps(payload).encode(), NOW)
    retry = webhook.normalise("org_one", "secret_scanning_alert", json.dumps(payload).encode(), NOW)
    payload["action"] = "reopened"
    alert = payload["alert"]
    assert isinstance(alert, dict)
    alert["updated_at"] = NOW.replace(minute=1).isoformat()
    reopened = webhook.normalise(
        "org_one", "secret_scanning_alert", json.dumps(payload).encode(), NOW
    )

    assert first.id == retry.id
    assert first.id != reopened.id


def test_scc_retry_is_stable_but_new_occurrence_is_distinct() -> None:
    payload = {
        "finding": {
            "name": "organizations/123/sources/456/findings/finding-one",
            "category": "LEAKED_CREDENTIAL",
            "severity": "CRITICAL",
            "state": "ACTIVE",
            "eventTime": NOW.isoformat(),
        },
        "resource": {
            "name": "//cloudresourcemanager.googleapis.com/projects/project-one",
            "projectDisplayName": "project-one",
            "service": "iam.googleapis.com",
        },
    }
    connector = SecurityCommandCenterFinding()

    first = connector.normalise("org_one", payload, NOW)
    retry = connector.normalise("org_one", payload, NOW)
    updated = connector.normalise(
        "org_one",
        {
            **payload,
            "finding": {
                **payload["finding"],
                "eventTime": (NOW.replace(minute=1)).isoformat(),
            },
        },
        NOW,
    )

    assert first.id == retry.id
    assert first.id != updated.id


@pytest.mark.anyio
async def test_cloudrun_deploy_supports_multi_container_with_target() -> None:
    from connectors.cloudrun import CloudRunConnector

    requests_log: list[httpx.Request] = []

    def run_handler(request: httpx.Request) -> httpx.Response:
        requests_log.append(request)
        if request.method == "GET":
            if "operations" in str(request.url):
                return httpx.Response(
                    200,
                    json={
                        "name": "operations/op-1",
                        "done": True,
                        "response": {
                            "name": "projects/p/locations/us-central1/services/s",
                            "latestReadyRevision": "rev-2",
                        },
                    },
                )
            return httpx.Response(
                200,
                json={
                    "name": "projects/p/locations/us-central1/services/s",
                    "latestReadyRevision": "rev-1",
                    "etag": "etag-1",
                    "template": {
                        "containers": [
                            {"name": "app", "image": "app:v1", "env": []},
                            {"name": "sidecar", "image": "sidecar:v1", "env": []},
                        ],
                    },
                    "traffic": [{"revision": "rev-1", "percent": 100}],
                },
            )
        if request.method == "PATCH":
            return httpx.Response(
                200,
                json={
                    "name": "operations/op-1",
                    "done": True,
                    "response": {
                        "name": "projects/p/locations/us-central1/services/s",
                        "latestReadyRevision": "rev-2",
                    },
                },
            )
        raise AssertionError(f"unexpected request {request.method} {request.url}")

    google = _google(run_handler)
    connector = CloudRunConnector(google)

    response = await connector.execute(
        "runtime.deployCandidate",
        {
            "service": "projects/p/locations/us-central1/services/s",
            "container_name": "sidecar",
            "secret_env": "API_KEY",
            "secret_name": "projects/p/secrets/k",
            "secret_version": "1",
            "generation_id": "gen_2",
            "tag": "candidate",
        },
        _context(),
    )

    assert response.result["candidate_revision"] == "rev-2"
    assert response.result["rollback_revision"] == "rev-1"
    assert response.result["generation_id"] == "gen_2"
    await google.close()


def _google(handler: Any) -> GoogleRestClient:
    return GoogleRestClient(
        credentials=Credentials(token="token"),  # type: ignore[no-untyped-call]
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
