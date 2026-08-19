import copy
from typing import Any

from connectors.base import ConnectorContext, ConnectorResponse
from connectors.base.errors import ConnectorError
from connectors.google import GoogleRestClient


class CloudRunConnector:
    tools = frozenset(
        {
            "runtime.inspectSecretBindings",
            "runtime.deployCandidate",
            "runtime.shiftTraffic",
            "runtime.rollback",
        }
    )

    def __init__(self, client: GoogleRestClient) -> None:
        self._client = client

    async def inspect(self, service_name: str) -> dict[str, Any]:
        service = await self._client.request(
            "GET", f"https://run.googleapis.com/v2/{_service_name(service_name)}"
        )
        return _inspect(service)

    async def execute(
        self,
        tool: str,
        payload: dict[str, Any],
        context: ConnectorContext,
    ) -> ConnectorResponse:
        del context
        service_name = _service(payload)
        service = await self._client.request("GET", f"https://run.googleapis.com/v2/{service_name}")
        if tool == "runtime.inspectSecretBindings":
            return ConnectorResponse(result=_inspect(service))
        if tool == "runtime.deployCandidate":
            return await self._deploy(service_name, service, payload)
        if tool == "runtime.shiftTraffic":
            return await self._traffic(service_name, service, payload)
        if tool == "runtime.rollback":
            rollback = _string(payload, "rollback_revision")
            return await self._patch_traffic(service_name, service, ((rollback, 100, None),))
        raise ConnectorError("unsupported-tool", f"Cloud Run does not support {tool}")

    async def _deploy(
        self,
        service_name: str,
        service: dict[str, Any],
        payload: dict[str, Any],
    ) -> ConnectorResponse:
        previous = service.get("latestReadyRevision")
        if not isinstance(previous, str):
            raise ConnectorError("runtime-not-ready", "Cloud Run service has no ready revision")
        await self._pin_current(service_name, service, previous)
        current = await self._client.request("GET", f"https://run.googleapis.com/v2/{service_name}")
        template = copy.deepcopy(current.get("template"))
        if not isinstance(template, dict):
            raise ConnectorError("runtime-invalid", "Cloud Run service has no revision template")
        containers = template.get("containers")
        if (
            not isinstance(containers, list)
            or not containers
            or not all(isinstance(c, dict) for c in containers)
        ):
            raise ConnectorError(
                "runtime-unsupported", "Cloud Run template must contain valid containers"
            )
        container_name = payload.get("container_name")
        if container_name is not None and not isinstance(container_name, str):
            raise ConnectorError("invalid-parameter", "container_name must be a string")
        if container_name:
            matching = [c for c in containers if c.get("name") == container_name]
            if not matching:
                raise ConnectorError(
                    "container-not-found",
                    f"container {container_name} not found in Cloud Run template",
                )
            container = matching[0]
        else:
            container = containers[0]
        assert isinstance(container, dict)
        secret_env = _string(payload, "secret_env")
        secret_name = _string(payload, "secret_name")
        secret_version = _string(payload, "secret_version")
        generation_id = _string(payload, "generation_id")
        env = container.setdefault("env", [])
        if not isinstance(env, list):
            raise ConnectorError(
                "runtime-invalid", "Cloud Run environment configuration is invalid"
            )
        _set_env(
            env,
            secret_env,
            {"secretKeyRef": {"secret": secret_name, "version": secret_version}},
        )
        _set_env(env, "FIREKEY_GENERATION_ID", generation_id)
        labels = template.setdefault("labels", {})
        if not isinstance(labels, dict):
            raise ConnectorError("runtime-invalid", "Cloud Run revision labels are invalid")
        labels["firekey-generation"] = generation_id
        body = {
            "name": service_name,
            "template": template,
            "traffic": current.get("traffic", []),
            "etag": current.get("etag"),
        }
        operation = await self._client.request(
            "PATCH",
            f"https://run.googleapis.com/v2/{service_name}",
            params={"updateMask": "template"},
            json=body,
        )
        result = await self._client.wait_operation(_operation(operation))
        candidate = result.get("latestReadyRevision")
        if not isinstance(candidate, str) or candidate == previous:
            refreshed = await self._client.request(
                "GET", f"https://run.googleapis.com/v2/{service_name}"
            )
            candidate = refreshed.get("latestReadyRevision")
        if not isinstance(candidate, str) or candidate == previous:
            raise ConnectorError("candidate-not-ready", "Cloud Run created no candidate revision")
        tagged = await self._client.request("GET", f"https://run.googleapis.com/v2/{service_name}")
        await self._patch_traffic(
            service_name,
            tagged,
            ((previous, 100, None), (candidate, 0, _string(payload, "tag"))),
        )
        return ConnectorResponse(
            result={
                "service": service_name,
                "candidate_revision": candidate,
                "rollback_revision": previous,
                "generation_id": generation_id,
            }
        )

    async def _traffic(
        self,
        service_name: str,
        service: dict[str, Any],
        payload: dict[str, Any],
    ) -> ConnectorResponse:
        candidate = _string(payload, "candidate_revision")
        rollback = _string(payload, "rollback_revision")
        percent = payload.get("percent")
        if not isinstance(percent, int) or percent < 0 or percent > 100:
            raise ConnectorError("invalid-traffic", "traffic percent must be between 0 and 100")
        return await self._patch_traffic(
            service_name,
            service,
            ((rollback, 100 - percent, None), (candidate, percent, None)),
        )

    async def _pin_current(
        self,
        service_name: str,
        service: dict[str, Any],
        revision: str,
    ) -> None:
        traffic = service.get("traffic")
        if isinstance(traffic, list) and len(traffic) == 1:
            target = traffic[0]
            if (
                isinstance(target, dict)
                and target.get("revision") == revision
                and target.get("percent") == 100
                and not target.get("type")
            ):
                return
        await self._patch_traffic(service_name, service, ((revision, 100, None),))

    async def _patch_traffic(
        self,
        service_name: str,
        service: dict[str, Any],
        targets: tuple[tuple[str, int, str | None], ...],
    ) -> ConnectorResponse:
        traffic = [
            {
                "revision": revision,
                "percent": percent,
                **({"tag": tag} if tag else {}),
            }
            for revision, percent, tag in targets
        ]
        operation = await self._client.request(
            "PATCH",
            f"https://run.googleapis.com/v2/{service_name}",
            params={"updateMask": "traffic"},
            json={"name": service_name, "traffic": traffic, "etag": service.get("etag")},
        )
        result = await self._client.wait_operation(_operation(operation))
        return ConnectorResponse(
            result={"service": service_name, "traffic": traffic, **_ready(result)}
        )


def _inspect(service: dict[str, Any]) -> dict[str, Any]:
    template = service.get("template")
    containers = template.get("containers", []) if isinstance(template, dict) else []
    bindings: list[dict[str, Any]] = []
    generation_id = None
    for container in containers if isinstance(containers, list) else []:
        if not isinstance(container, dict):
            continue
        for item in container.get("env", []):
            if not isinstance(item, dict):
                continue
            if "valueSource" in item:
                bindings.append({"name": item.get("name"), "valueSource": item["valueSource"]})
            if item.get("name") == "FIREKEY_GENERATION_ID" and isinstance(item.get("value"), str):
                generation_id = item["value"]
    return {
        "name": service.get("name"),
        "latest_ready_revision": service.get("latestReadyRevision"),
        "latest_created_revision": service.get("latestCreatedRevision"),
        "reconciling": service.get("reconciling", False),
        "traffic": service.get("trafficStatuses", []),
        "secret_bindings": bindings,
        "generation_id": generation_id,
    }


def _set_env(env: list[Any], name: str, value: str | dict[str, Any]) -> None:
    env[:] = [item for item in env if not isinstance(item, dict) or item.get("name") != name]
    if isinstance(value, str):
        env.append({"name": name, "value": value})
    else:
        env.append({"name": name, "valueSource": value})


def _ready(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "latest_ready_revision": result.get("latestReadyRevision"),
        "reconciling": result.get("reconciling", False),
    }


def _operation(value: dict[str, Any]) -> str:
    name = value.get("name")
    if not isinstance(name, str):
        raise ConnectorError("operation-missing", "Cloud Run returned no operation name")
    return name


def _service(payload: dict[str, Any]) -> str:
    value = _string(payload, "service")
    return _service_name(value)


def _service_name(value: str) -> str:
    if not value.startswith("projects/") or "/locations/" not in value or "/services/" not in value:
        raise ConnectorError("invalid-service", "a full Cloud Run service resource is required")
    return value


def _string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ConnectorError("invalid-parameter", f"{key} is required")
    return value
