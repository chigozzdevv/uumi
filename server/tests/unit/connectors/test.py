import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from connectors.base import ConnectorContext
from connectors.base.errors import AmbiguousMutationError, ConnectorError
from connectors.github import GitHubWebhook
from connectors.google import GoogleRestClient
from connectors.http import HttpProviderConnector
from connectors.scc import SecurityCommandCenterFinding
from connectors.secrets import SecretManagerConnector
from contracts import (
    Connection,
    ConnectionAuthorization,
    ConnectionInterface,
    ConnectionRole,
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
        platform="example-provider",
        display_name="Example provider",
        roles=frozenset({ConnectionRole.PROVIDER}),
        interface=ConnectionInterface.API,
        authorization=ConnectionAuthorization.API_KEY,
        authorization_reference="projects/project-one/secrets/admin/versions/1",
        capabilities=HttpProviderConnector.tools,
        allowed_resources=("provider.example",),
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


def _runtime_context() -> ConnectorContext:
    context = _context()
    return ConnectorContext(
        request_id=context.request_id,
        agent_id=context.agent_id,
        connection=context.connection.model_copy(
            update={
                "platform": "cloud-run",
                "roles": frozenset({ConnectionRole.RUNTIME}),
                "authorization": ConnectionAuthorization.WORKLOAD_IDENTITY,
                "authorization_reference": (
                    "workload-identity://runtime@project-one.iam.gserviceaccount.com"
                ),
                "http": None,
            }
        ),
        run=context.run,
        now=context.now,
        idempotency_key=context.idempotency_key,
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
async def test_secret_manager_proves_consumer_access_without_returning_secret() -> None:
    expires_at = datetime.now(UTC).replace(microsecond=0) + timedelta(minutes=10)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(":generateAccessToken"):
            assert request.url.path.endswith(
                "/serviceAccounts/consumer@project-one.iam.gserviceaccount.com:generateAccessToken"
            )
            return httpx.Response(
                200,
                json={
                    "accessToken": "consumer-access-token",
                    "expireTime": expires_at.isoformat().replace("+00:00", "Z"),
                },
            )
        assert request.url.path.endswith("/versions/7:access")
        assert request.headers["Authorization"] == "Bearer consumer-access-token"
        return httpx.Response(
            200,
            json={"payload": {"data": base64.b64encode(b"credential-value").decode()}},
        )

    client = _google(handler)
    connection = _runtime_context().connection.model_copy(
        update={
            "platform": "google-secret-manager",
            "roles": frozenset({ConnectionRole.SECRET_STORE}),
            "authorization_reference": (
                "workload-identity://secret-writer@project-one.iam.gserviceaccount.com"
            ),
            "capabilities": SecretManagerConnector.tools,
        }
    )
    context = _context()
    context = ConnectorContext(
        request_id=context.request_id,
        agent_id=context.agent_id,
        connection=connection,
        run=context.run,
        now=context.now,
        idempotency_key=context.idempotency_key,
    )

    result = await SecretManagerConnector(client).execute(
        "secretStore.testConsumerAccess",
        {
            "version": "projects/project-one/secrets/key/versions/7",
            "consumer_identity": "consumer@project-one.iam.gserviceaccount.com",
        },
        context,
    )

    assert result.result["accessible"] is True
    assert "credential-value" not in repr(result)
    assert "consumer-access-token" not in repr(result)
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
        {"name": "uumi-run-one", "scopes": ["mail.send"]},
        _context(),
    )

    assert calls == 2
    assert response.result == {
        "provider_id": "provider-key-one",
        "name": "uumi-run-one",
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
async def test_provider_stale_reconcile_cleans_provider_orphan() -> None:
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
    assert disabled == []
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
            update={"platform": "internal-vendor", "http": api}
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
async def test_http_metadata_projection_drops_undeclared_provider_fields() -> None:
    def google_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"payload": {"data": base64.b64encode(b"admin-key").decode()}},
        )

    def provider_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "result": [
                    {
                        "api_key_id": "provider-key-one",
                        "name": "mailer",
                        "scopes": ["mail.send"],
                        "apiKey": "must-not-cross-boundary",
                        "owner_email": "operator@example.com",
                    }
                ]
            },
        )

    google = _google(google_handler)
    connector = HttpProviderConnector(
        SecretManagerConnector(google),
        httpx.AsyncClient(transport=httpx.MockTransport(provider_handler)),
    )

    listed = await connector.execute("provider.listCredentialMetadata", {}, _context())
    status = await connector.execute(
        "provider.getCredentialStatus", {"provider_id": "provider-key-one"}, _context()
    )

    expected = {
        "provider_id": "provider-key-one",
        "name": "mailer",
        "scopes": ["mail.send"],
    }
    assert listed.result == {"credentials": [expected]}
    assert status.result == {"exists": True, "credential": expected}
    assert "must-not-cross-boundary" not in repr((listed, status))
    assert "operator@example.com" not in repr((listed, status))
    await google.close()


@pytest.mark.anyio
async def test_http_provider_proves_the_secret_credential_identity() -> None:
    def google_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/versions/1:access")
        return httpx.Response(
            200,
            json={"payload": {"data": base64.b64encode(b"workload-secret").decode()}},
        )

    def provider_handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer workload-secret"
        return httpx.Response(200, json={"id": "provider-key-one"})

    google = _google(google_handler)
    secret_connection = _runtime_context().connection.model_copy(
        update={
            "id": "secret_one",
            "platform": "google-secret-manager",
            "roles": frozenset({ConnectionRole.SECRET_STORE}),
            "authorization_reference": (
                "workload-identity://secret-reader@project-one.iam.gserviceaccount.com"
            ),
            "capabilities": SecretManagerConnector.tools,
        }
    )
    google._connection_credentials["secret-reader@project-one.iam.gserviceaccount.com"] = (
        Credentials(token="token")  # type: ignore[no-untyped-call]
    )
    connector = HttpProviderConnector(
        SecretManagerConnector(google),
        httpx.AsyncClient(transport=httpx.MockTransport(provider_handler)),
    )

    identity = await connector.credential_identity(
        _context().connection,
        secret_connection,
        "projects/project-one/secrets/workload/versions/1",
    )

    assert identity == "provider-key-one"
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

    with pytest.raises(ValidationError, match="unsupported fields"):
        HttpOperation(
            method="GET",
            path="/keys",
            success_statuses=(200,),
            metadata_fields={"api_key": "apiKey"},
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
    google._connection_credentials["runtime@project-one.iam.gserviceaccount.com"] = Credentials(
        token="token"
    )  # type: ignore[no-untyped-call]
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
        _runtime_context(),
    )

    assert response.result["candidate_revision"] == "rev-2"
    assert response.result["rollback_revision"] == "rev-1"
    assert response.result["generation_id"] == "gen_2"
    await google.close()


@pytest.mark.anyio
async def test_cloudrun_lists_services_from_connection_region_without_secret_material() -> None:
    from connectors.cloudrun import CloudRunConnector

    requests_log: list[httpx.Request] = []

    def run_handler(request: httpx.Request) -> httpx.Response:
        requests_log.append(request)
        page_token = request.url.params.get("pageToken")
        if page_token == "next":
            return httpx.Response(
                200,
                json={
                    "services": [
                        {
                            "name": "projects/project-one/locations/us-east1/services/worker-two",
                            "uri": "https://worker-two.example.run.app",
                            "template": {
                                "serviceAccount": "worker-two@example.iam.gserviceaccount.com"
                            },
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "services": [
                    {
                        "name": "projects/project-one/locations/us-east1/services/worker-one",
                        "uri": "https://worker-one.example.run.app",
                        "labels": {"environment": "production"},
                        "template": {
                            "serviceAccount": "worker-one@example.iam.gserviceaccount.com",
                            "containers": [
                                {
                                    "name": "worker",
                                    "env": [
                                        {
                                            "name": "PROVIDER_KEY",
                                            "valueSource": {
                                                "secretKeyRef": {
                                                    "secret": "provider-key",
                                                    "version": "7",
                                                }
                                            },
                                        }
                                    ],
                                }
                            ],
                        },
                    }
                ],
                "nextPageToken": "next",
            },
        )

    google = _google(run_handler)
    google._connection_credentials["runtime@project-one.iam.gserviceaccount.com"] = Credentials(
        token="token"
    )  # type: ignore[no-untyped-call]
    connector = CloudRunConnector(google)
    connection = _runtime_context().connection.model_copy(
        update={"allowed_resources": ("projects/project-one",), "region": "us-east1"}
    )

    resources = await connector.resources_for(connection)

    assert [resource["display_name"] for resource in resources] == ["worker-one", "worker-two"]
    assert resources[0]["identity"] == "worker-one@example.iam.gserviceaccount.com"
    assert resources[0]["endpoint"] == "https://worker-one.example.run.app"
    assert resources[0]["region"] == "us-east1"
    assert resources[0]["environment_name"] == "production"
    assert resources[0]["production"] is True
    assert resources[0]["secret_bindings"] == (
        {
            "name": "PROVIDER_KEY",
            "secret": "provider-key",
            "version": "7",
            "container": "worker",
        },
    )
    assert "value" not in str(resources[0]["secret_bindings"])
    assert all(
        request.url.path == "/v2/projects/project-one/locations/us-east1/services"
        for request in requests_log
    )
    await google.close()


@pytest.mark.anyio
async def test_google_customer_calls_reject_process_identity_fallback() -> None:
    google = _google(lambda request: httpx.Response(200, json={}))

    with pytest.raises(ConnectorError, match="workload-identity"):
        await google.request(
            "GET",
            "https://run.googleapis.com/v2/projects/p/locations/r/services/s",
            connection=_context().connection,
        )

    await google.close()


@pytest.mark.anyio
async def test_google_mints_short_lived_connection_token() -> None:
    expires_at = datetime.now(UTC).replace(microsecond=0) + timedelta(minutes=10)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith(
            "/serviceAccounts/runtime@project-one.iam.gserviceaccount.com:generateAccessToken"
        )
        body = json.loads(request.content)
        assert body == {
            "scope": ["https://www.googleapis.com/auth/cloud-platform"],
            "lifetime": "600s",
        }
        return httpx.Response(
            200,
            json={
                "accessToken": "short-lived-customer-token",
                "expireTime": expires_at.isoformat().replace("+00:00", "Z"),
            },
        )

    google = _google(handler)
    token, returned_expiry = await google.mint_access_token_for(_runtime_context().connection)

    assert token.bytes() == b"short-lived-customer-token"
    assert returned_expiry == expires_at
    assert "short-lived-customer-token" not in repr(token)
    token.clear()
    await google.close()


@pytest.mark.anyio
async def test_google_stream_parses_server_sent_json_events() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["alt"] == "sse"
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            text=(
                'data: {"candidates":[{"content":{"parts":[{"text":"first"}]}}]}\n\n'
                'data: {"candidates":[{"content":{"parts":[{"text":"second"}]}}]}\n\n'
                "data: [DONE]\n\n"
            ),
        )

    google = _google(handler)
    events = [
        event
        async for event in google.stream(
            "POST", "https://aiplatform.googleapis.com/model:stream", params={"alt": "sse"}
        )
    ]

    assert [event["candidates"][0]["content"]["parts"][0]["text"] for event in events] == [
        "first",
        "second",
    ]
    await google.close()


def _google(handler: Any) -> GoogleRestClient:
    return GoogleRestClient(
        credentials=Credentials(token="token"),  # type: ignore[no-untyped-call]
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
