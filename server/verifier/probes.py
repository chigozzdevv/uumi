import json
from collections.abc import Callable
from datetime import datetime
from typing import Any

import httpx
from broker.evidence import GcsEvidenceSink
from broker.service import ConnectorRegistry
from connectors import ConnectorContext
from connectors.base.errors import ConnectorError
from connectors.cloudrun import CloudRunConnector
from connectors.google import GoogleRestClient
from connectors.secrets import SecretManagerConnector
from contracts import (
    Connection,
    ProbeDefinition,
    ProbeKind,
    ProbeResult,
    VerificationStatus,
)
from core.errors import ResourceConflictError


class ProbeExecutor:
    def __init__(
        self,
        evidence: GcsEvidenceSink,
        google: GoogleRestClient,
        connectors: ConnectorRegistry,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._evidence = evidence
        self._google = google
        self._connectors = connectors
        self._http = http or httpx.AsyncClient(follow_redirects=False)

    async def execute(
        self,
        definition: ProbeDefinition,
        connection: Connection,
        context: ConnectorContext,
        clock: Callable[[], datetime],
    ) -> ProbeResult:
        started_at = clock()
        try:
            if definition.kind in {ProbeKind.HTTP, ProbeKind.EMAIL}:
                observations, checks, raw = await self._http_probe(definition)
            elif definition.kind is ProbeKind.SECRET:
                observations, checks, raw = await self._secret_probe(definition)
            elif definition.kind in {ProbeKind.RUNTIME, ProbeKind.GENERATION}:
                observations, checks, raw = await self._runtime_probe(
                    definition, connection, context
                )
            elif definition.kind is ProbeKind.TELEMETRY:
                observations, checks, raw = await self._telemetry_probe(definition)
            elif definition.kind is ProbeKind.PROVIDER:
                observations, checks, raw = await self._provider_probe(
                    definition, connection, context
                )
            else:
                raise ResourceConflictError(f"unsupported probe kind {definition.kind.value}")
            evidence = await self._evidence.store(
                definition.organisation_id,
                context.run.id,
                f"verification-{definition.kind.value}",
                raw,
                "application/json",
                clock(),
            )
            return ProbeResult(
                probe_id=definition.id,
                status=VerificationStatus.PASSED,
                observed_status=_integer(observations.get("http_status")),
                generation_id=_generation(observations.get("generation_id")),
                checks=frozenset(checks),
                evidence_ids=(evidence.id,),
                observations=observations,
                started_at=started_at,
                completed_at=clock(),
            )
        except Exception as error:
            failure = json.dumps(
                {"error": type(error).__name__, "message": str(error)},
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
            evidence = await self._evidence.store(
                definition.organisation_id,
                context.run.id,
                f"verification-{definition.kind.value}-failure",
                failure,
                "application/json",
                clock(),
            )
            return ProbeResult(
                probe_id=definition.id,
                status=VerificationStatus.FAILED,
                checks=frozenset({"probe-executed"}),
                evidence_ids=(evidence.id,),
                error=_error(error),
                started_at=started_at,
                completed_at=clock(),
            )

    async def _http_probe(
        self, definition: ProbeDefinition
    ) -> tuple[dict[str, str | int | float | bool | None], set[str], bytes]:
        response = await self._http.request(
            definition.method,
            definition.target,
            headers=definition.headers,
            timeout=definition.timeout_seconds,
        )
        if response.status_code not in definition.expected_status:
            raise ConnectorError(
                "verification-http-status",
                f"expected {definition.expected_status}, received {response.status_code}",
            )
        checks = {"http-status-matched"}
        body: Any = None
        if definition.required_fields or definition.expected_generation_id:
            try:
                body = response.json()
            except ValueError:
                body = None
        if definition.required_fields:
            if not isinstance(body, dict):
                raise ConnectorError("verification-http-body", "response body is not a JSON object")
            for path, expected in definition.required_fields.items():
                if _field(body, path) != expected:
                    raise ConnectorError(
                        "verification-field-mismatch", f"response field {path} changed"
                    )
            checks.add("response-fields-matched")
        observed_generation = response.headers.get("x-firekey-generation-id")
        if observed_generation is None and isinstance(body, dict):
            value = body.get("generation_id")
            observed_generation = value if isinstance(value, str) else None
        if definition.expected_generation_id is not None:
            if observed_generation != definition.expected_generation_id:
                raise ConnectorError(
                    "verification-generation-mismatch",
                    "response did not identify the expected credential generation",
                )
            checks.add("generation-identified")
        safe_headers = {
            key.lower(): value
            for key, value in response.headers.items()
            if key.lower()
            in {"content-type", "date", "server", "x-firekey-generation-id", "x-request-id"}
        }
        record = {
            "status": response.status_code,
            "headers": safe_headers,
            "body_digest": __import__("hashlib").sha256(response.content).hexdigest(),
        }
        observations: dict[str, str | int | float | bool | None] = {
            "http_status": response.status_code,
            "generation_id": observed_generation,
        }
        return observations, checks, _json(record)

    async def _secret_probe(
        self, definition: ProbeDefinition
    ) -> tuple[dict[str, str | int | float | bool | None], set[str], bytes]:
        connector = SecretManagerConnector(self._google)
        version = definition.target
        if not version.startswith("projects/") or "/versions/" not in version:
            raise ConnectorError(
                "verification-secret-target", "secret probe requires a full version resource"
            )
        metadata = await self._google.request(
            "GET", f"https://secretmanager.googleapis.com/v1/{version}"
        )
        expected = "DISABLED" if definition.negative else "ENABLED"
        if metadata.get("state") != expected:
            raise ConnectorError(
                "verification-secret-state", f"secret version is not {expected.lower()}"
            )
        checks = {"secret-version-exists", f"secret-version-{expected.lower()}"}
        if not definition.negative:
            with await connector.access(version) as value:
                if not value.bytes():
                    raise ConnectorError(
                        "verification-secret-empty", "secret version contains no bytes"
                    )
                checks.add("secret-payload-accessible")
        observations: dict[str, str | int | float | bool | None] = {
            "secret_state": expected,
            "secret_version": version,
        }
        return observations, checks, _json(observations)

    async def _runtime_probe(
        self,
        definition: ProbeDefinition,
        connection: Connection,
        context: ConnectorContext,
    ) -> tuple[dict[str, str | int | float | bool | None], set[str], bytes]:
        connector = CloudRunConnector(self._google)
        response = await connector.execute(
            "runtime.inspectSecretBindings", {"service": definition.target}, context
        )
        result = response.result
        if result.get("reconciling") is True:
            raise ConnectorError("verification-runtime-reconciling", "runtime is reconciling")
        expected = definition.expected_generation_id
        if expected is not None:
            revision = result.get("latest_ready_revision")
            if not isinstance(revision, str):
                raise ConnectorError(
                    "verification-runtime-revision", "runtime has no ready revision"
                )
            revision_data = await self._google.request(
                "GET", f"https://run.googleapis.com/v2/{revision}"
            )
            labels = revision_data.get("labels")
            if not isinstance(labels, dict) or labels.get("firekey-generation") != expected:
                raise ConnectorError(
                    "verification-generation-label",
                    "ready revision does not carry the expected generation label",
                )
        observations: dict[str, str | int | float | bool | None] = {
            "runtime_ready": True,
            "generation_id": expected,
            "latest_ready_revision": _text(result.get("latest_ready_revision")),
        }
        return observations, {"runtime-ready", "runtime-binding-inspected"}, _json(result)

    async def _telemetry_probe(
        self, definition: ProbeDefinition
    ) -> tuple[dict[str, str | int | float | bool | None], set[str], bytes]:
        project = _project(definition.target)
        response = await self._google.request(
            "POST",
            "https://logging.googleapis.com/v2/entries:list",
            json={
                "resourceNames": [f"projects/{project}"],
                "filter": definition.headers.get("x-firekey-log-filter", ""),
                "orderBy": "timestamp desc",
                "pageSize": 100,
            },
        )
        entries = response.get("entries", [])
        if not isinstance(entries, list):
            raise ConnectorError(
                "verification-telemetry-response", "Logging returned invalid entries"
            )
        expected_count = definition.required_fields.get("minimum_count", 1)
        if not isinstance(expected_count, int) or len(entries) < expected_count:
            raise ConnectorError("verification-telemetry-count", "insufficient matching telemetry")
        record = {
            "entry_count": len(entries),
            "insert_ids": [entry.get("insertId") for entry in entries if isinstance(entry, dict)],
        }
        observations: dict[str, str | int | float | bool | None] = {"telemetry_count": len(entries)}
        return observations, {"telemetry-query-executed", "telemetry-threshold-met"}, _json(record)

    async def _provider_probe(
        self,
        definition: ProbeDefinition,
        connection: Connection,
        context: ConnectorContext,
    ) -> tuple[dict[str, str | int | float | bool | None], set[str], bytes]:
        response = await self._connectors.resolve(
            connection, "provider.getCredentialStatus"
        ).execute(
            "provider.getCredentialStatus",
            {"provider_id": definition.target},
            context,
        )
        exists = response.result.get("exists")
        expected = not definition.negative
        if exists is not expected:
            state = "exist" if expected else "be revoked"
            raise ConnectorError(
                "verification-provider-state", f"provider credential should {state}"
            )
        observations: dict[str, str | int | float | bool | None] = {
            "provider_credential_exists": expected,
            "provider_id": definition.target,
        }
        check = "provider-credential-exists" if expected else "provider-credential-revoked"
        return observations, {check}, _json(response.result)


def _field(value: dict[str, Any], path: str) -> Any:
    current: Any = value
    for segment in path.split("."):
        if not isinstance(current, dict) or segment not in current:
            return None
        current = current[segment]
    return current


def _json(value: Any) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode()


def _project(value: str) -> str:
    segments = value.split("/")
    if len(segments) < 2 or segments[0] != "projects" or not segments[1]:
        raise ConnectorError("verification-project", "target must start with projects/{project}")
    return segments[1]


def _error(error: Exception) -> str:
    value = f"{type(error).__name__}: {error}".replace("\n", " ")
    return value[:1024]


def _integer(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _generation(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    return value


def _text(value: object) -> str | None:
    return value if isinstance(value, str) else None
