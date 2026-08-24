import hashlib
from typing import Protocol
from urllib.parse import urlparse

import httpx
from contracts import (
    Notification,
    NotificationEndpoint,
    NotificationProvider,
    Severity,
)

from connectors.base import SecretValue
from connectors.base.errors import ConnectorError


class SecretAccessor(Protocol):
    async def access(self, version: str) -> SecretValue: ...


class NotificationConnector:
    def __init__(
        self,
        secrets: SecretAccessor,
        app_url: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        parsed = urlparse(app_url)
        if parsed.scheme != "https" or not parsed.netloc or parsed.path not in {"", "/"}:
            raise ValueError("notification application URL must be an HTTPS origin")
        self._secrets = secrets
        self._app_url = app_url.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=30)

    async def send(
        self,
        notification: Notification,
        endpoint: NotificationEndpoint,
        delivery_id: str,
    ) -> str:
        if endpoint.organisation_id != notification.organisation_id:
            raise ConnectorError(
                "notification-tenant-mismatch",
                "notification endpoint crosses an organisation boundary",
            )
        if notification.kind not in endpoint.event_kinds:
            raise ConnectorError(
                "notification-kind-rejected",
                "notification endpoint does not accept this event kind",
            )
        with await self._secrets.access(endpoint.auth_reference) as secret:
            if endpoint.provider is NotificationProvider.RESEND:
                return await self._resend(notification, endpoint, delivery_id, secret.bytes())
            if endpoint.provider is NotificationProvider.SLACK:
                return await self._slack(notification, delivery_id, secret.bytes())
            if endpoint.provider is NotificationProvider.PAGERDUTY:
                return await self._pagerduty(notification, delivery_id, secret.bytes())
        raise ConnectorError("notification-provider", "notification provider is unsupported")

    async def close(self) -> None:
        await self._client.aclose()

    async def _resend(
        self,
        notification: Notification,
        endpoint: NotificationEndpoint,
        delivery_id: str,
        secret: bytes,
    ) -> str:
        token = _utf8(secret, "Resend API key")
        assert endpoint.sender is not None
        response = await self._request(
            "POST",
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {token}",
                "Idempotency-Key": delivery_id,
            },
            json={
                "from": endpoint.sender,
                "to": list(endpoint.recipients),
                "subject": notification.title,
                "text": self._text(notification),
                "tags": [{"name": "uumi_delivery_id", "value": delivery_id}],
            },
            expected=frozenset({200}),
        )
        value = response.json()
        email_id = value.get("id") if isinstance(value, dict) else None
        return (
            email_id if isinstance(email_id, str) and email_id else _receipt("resend", delivery_id)
        )

    async def _slack(
        self,
        notification: Notification,
        delivery_id: str,
        secret: bytes,
    ) -> str:
        webhook = _utf8(secret, "Slack webhook")
        parsed = urlparse(webhook)
        if parsed.scheme != "https" or parsed.hostname != "hooks.slack.com":
            raise ConnectorError("invalid-slack-webhook", "Slack webhook must use hooks.slack.com")
        await self._request(
            "POST",
            webhook,
            json={"text": self._text(notification)},
            expected=frozenset({200}),
        )
        return _receipt("slack", delivery_id)

    async def _pagerduty(
        self,
        notification: Notification,
        delivery_id: str,
        secret: bytes,
    ) -> str:
        routing_key = _utf8(secret, "PagerDuty routing key")
        response = await self._request(
            "POST",
            "https://events.pagerduty.com/v2/enqueue",
            json={
                "routing_key": routing_key,
                "event_action": "trigger",
                "dedup_key": delivery_id,
                "payload": {
                    "summary": notification.title,
                    "severity": _pagerduty_severity(notification.severity),
                    "source": "uumi",
                    "custom_details": {
                        "message": notification.body,
                        "resource_id": notification.resource_id,
                        "link": self._link(notification),
                    },
                },
                "links": [{"href": self._link(notification), "text": "Open Uumi"}],
            },
            expected=frozenset({202}),
        )
        value = response.json()
        dedup_key = value.get("dedup_key") if isinstance(value, dict) else None
        return dedup_key if isinstance(dedup_key, str) and dedup_key else delivery_id

    async def _request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json: object,
        expected: frozenset[int],
    ) -> httpx.Response:
        try:
            response = await self._client.request(method, url, headers=headers, json=json)
        except (httpx.TimeoutException, httpx.NetworkError) as error:
            raise ConnectorError(
                "notification-network", "notification provider was unavailable", retryable=True
            ) from error
        if response.status_code not in expected:
            retryable = response.status_code in {408, 409, 429, 500, 502, 503, 504}
            raise ConnectorError(
                f"notification-http-{response.status_code}",
                f"notification provider returned HTTP {response.status_code}",
                retryable=retryable,
            )
        return response

    def _text(self, notification: Notification) -> str:
        return (
            f"{notification.title}\n\n{notification.body}\n\n"
            f"Resource: {notification.resource_id}\nOpen Uumi: {self._link(notification)}"
        )

    def _link(self, notification: Notification) -> str:
        return f"{self._app_url}{notification.link_path}"


def _utf8(value: bytes, label: str) -> str:
    try:
        decoded = value.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ConnectorError("invalid-notification-auth", f"{label} is not UTF-8") from error
    if not decoded or "\n" in decoded or "\r" in decoded:
        raise ConnectorError("invalid-notification-auth", f"{label} is invalid")
    return decoded


def _receipt(provider: str, delivery_id: str) -> str:
    value = hashlib.sha256(f"{provider}\0{delivery_id}".encode()).hexdigest()
    return f"accepted-{value[:40]}"


def _pagerduty_severity(severity: Severity) -> str:
    return {
        Severity.CRITICAL: "critical",
        Severity.HIGH: "error",
        Severity.MEDIUM: "warning",
        Severity.LOW: "info",
    }[severity]
