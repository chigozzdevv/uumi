import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from connectors.base.errors import ConnectorError
from connectors.google import GoogleRestClient


@dataclass(frozen=True, slots=True)
class BrowserVm:
    instance: str
    internal_address: str


class BrowserVmManager:
    def __init__(
        self,
        client: GoogleRestClient,
        project_id: str,
        zone: str,
        instance_template: str,
        capability_public_key: str,
        evidence_bucket: str,
        region: str,
        worker_image: str,
        model_armor_template: str,
        model_armor_response_template: str | None = None,
    ) -> None:
        self._client = client
        self._project = project_id
        self._zone = zone
        self._template = instance_template
        self._capability_public = capability_public_key
        self._evidence = evidence_bucket
        self._region = region
        self._image = worker_image
        self._model_armor_template = model_armor_template
        self._model_armor_response_template = model_armor_response_template or model_armor_template

    async def create(
        self,
        organisation_id: str,
        session_id: str,
        expires_at: datetime,
        setup_token_hash: str | None = None,
        allowed_domains: tuple[str, ...] = (),
        storage_domains: tuple[str, ...] = (),
        secret_container: str | None = None,
    ) -> BrowserVm:
        name = _name(session_id)
        base = (
            f"https://compute.googleapis.com/compute/v1/projects/{self._project}/zones/{self._zone}"
        )
        metadata = [
            {"key": "uumi-organisation", "value": organisation_id},
            {"key": "uumi-session", "value": session_id},
            {"key": "uumi-expires", "value": expires_at.isoformat()},
            {"key": "uumi-project", "value": self._project},
            {
                "key": "uumi-capability-public",
                "value": self._capability_public,
            },
            {"key": "uumi-evidence", "value": self._evidence},
            {"key": "uumi-region", "value": self._region},
            {"key": "uumi-worker-image", "value": self._image},
            {
                "key": "uumi-model-armor-template",
                "value": self._model_armor_template,
            },
            {
                "key": "uumi-model-armor-response-template",
                "value": self._model_armor_response_template,
            },
        ]
        if setup_token_hash is not None:
            if not re.fullmatch(r"[a-f0-9]{64}", setup_token_hash):
                raise ConnectorError("invalid-setup-token", "setup token hash is invalid")
            if secret_container is None:
                raise ConnectorError("invalid-setup-secret", "setup secret container is required")
            metadata.extend(
                [
                    {"key": "uumi-setup", "value": "true"},
                    {"key": "uumi-setup-token-hash", "value": setup_token_hash},
                    {"key": "uumi-setup-domains", "value": ",".join(allowed_domains)},
                    {
                        "key": "uumi-setup-storage-domains",
                        "value": ",".join(storage_domains),
                    },
                    {"key": "uumi-setup-secret", "value": secret_container},
                ]
            )
        metadata = await self._merge_template_metadata(metadata)
        operation = await self._client.request(
            "POST",
            f"{base}/instances",
            params={"sourceInstanceTemplate": self._template},
            json={
                "name": name,
                "labels": {
                    "uumi-browser": "true",
                    "uumi-session": _label(session_id),
                },
                "metadata": {"items": metadata},
            },
        )
        await self._client.wait_operation(_operation(operation), base_url=f"{base}/operations")
        instance = await self._client.request("GET", f"{base}/instances/{name}")
        self._validate(instance)
        address = _address(instance)
        return BrowserVm(
            instance=f"projects/{self._project}/zones/{self._zone}/instances/{name}",
            internal_address=address,
        )

    async def _merge_template_metadata(
        self, overrides: list[dict[str, str]]
    ) -> list[dict[str, str]]:
        template_url = self._template
        if not template_url.startswith("https://"):
            template_url = f"https://compute.googleapis.com/compute/v1/{template_url}"
        template = await self._client.request("GET", template_url)
        properties = template.get("properties")
        raw_metadata = properties.get("metadata") if isinstance(properties, dict) else None
        raw_items = raw_metadata.get("items") if isinstance(raw_metadata, dict) else None
        if not isinstance(raw_items, list):
            raise ConnectorError("browser-template", "browser instance template has no metadata")
        merged: dict[str, str] = {}
        for item in raw_items:
            if not isinstance(item, dict):
                raise ConnectorError(
                    "browser-template", "browser instance template metadata is invalid"
                )
            key = item.get("key")
            value = item.get("value")
            if not isinstance(key, str) or not isinstance(value, str):
                raise ConnectorError(
                    "browser-template", "browser instance template metadata is invalid"
                )
            merged[key] = value
        if not merged.get("startup-script"):
            raise ConnectorError(
                "browser-template", "browser instance template has no startup script"
            )
        for item in overrides:
            merged[item["key"]] = item["value"]
        return [{"key": key, "value": value} for key, value in merged.items()]

    async def delete(self, instance: str) -> None:
        expected = f"projects/{self._project}/zones/{self._zone}/instances/"
        if not instance.startswith(expected):
            raise ConnectorError("browser-instance-scope", "browser VM is outside its zone")
        name = instance.removeprefix(expected)
        base = (
            f"https://compute.googleapis.com/compute/v1/projects/{self._project}/zones/{self._zone}"
        )
        try:
            operation = await self._client.request(
                "DELETE", f"{base}/instances/{name}", expected=frozenset({200, 204})
            )
        except ConnectorError as error:
            if error.code == "google-api-404":
                return
            raise
        if operation:
            await self._client.wait_operation(_operation(operation), base_url=f"{base}/operations")

    async def exists(self, instance: str) -> bool:
        expected = f"projects/{self._project}/zones/{self._zone}/instances/"
        if not instance.startswith(expected):
            raise ConnectorError("browser-instance-scope", "browser VM is outside its zone")
        name = instance.removeprefix(expected)
        base = (
            f"https://compute.googleapis.com/compute/v1/projects/{self._project}/zones/{self._zone}"
        )
        try:
            await self._client.request("GET", f"{base}/instances/{name}")
        except ConnectorError as error:
            if error.code == "google-api-404":
                return False
            raise
        return True

    def _validate(self, instance: dict[str, Any]) -> None:
        interfaces = instance.get("networkInterfaces")
        if not isinstance(interfaces, list) or len(interfaces) != 1:
            raise ConnectorError("browser-network", "browser VM must have one interface")
        interface = interfaces[0]
        if not isinstance(interface, dict) or interface.get("accessConfigs"):
            raise ConnectorError("browser-public-ip", "browser VM must not have a public IP")
        disks = instance.get("disks")
        if not isinstance(disks, list) or not disks:
            raise ConnectorError("browser-disk", "browser VM has no boot disk")
        if any(not isinstance(disk, dict) or disk.get("autoDelete") is not True for disk in disks):
            raise ConnectorError("browser-disk", "browser VM disks must auto-delete")
        shielded = instance.get("shieldedInstanceConfig")
        if not isinstance(shielded, dict) or shielded.get("enableSecureBoot") is not True:
            raise ConnectorError("browser-shielding", "browser VM must enable Secure Boot")


def _operation(value: dict[str, Any]) -> str:
    name = value.get("name")
    if not isinstance(name, str):
        raise ConnectorError("browser-operation", "Compute Engine returned no operation")
    return name


def _address(instance: dict[str, Any]) -> str:
    interfaces = instance.get("networkInterfaces")
    if not isinstance(interfaces, list) or not interfaces or not isinstance(interfaces[0], dict):
        raise ConnectorError("browser-address", "browser VM has no network interface")
    address = interfaces[0].get("networkIP")
    if not isinstance(address, str):
        raise ConnectorError("browser-address", "browser VM has no internal address")
    return address


def _name(session_id: str) -> str:
    clean = re.sub(r"[^a-z0-9-]", "-", session_id.lower()).strip("-")
    if not clean:
        raise ValueError("browser session ID cannot form a VM name")
    return f"fk-{clean}"[:63].rstrip("-")


def _label(value: str) -> str:
    clean = re.sub(r"[^a-z0-9_-]", "-", value.lower())
    return clean[:63]
