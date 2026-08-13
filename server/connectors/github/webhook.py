import hashlib
import hmac
import json
from datetime import datetime
from typing import Any

from contracts import Confidence, IngestionEvent, Severity, SourceResource
from core.ids import new_id


class GitHubWebhook:
    def verify(self, body: bytes, signature: str, secret: bytes) -> None:
        if not signature.startswith("sha256="):
            raise ValueError("GitHub signature must use sha256")
        expected = "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError("GitHub webhook signature is invalid")

    def normalise(
        self,
        organisation_id: str,
        event_type: str,
        body: bytes,
        received_at: datetime,
    ) -> IngestionEvent:
        if event_type != "secret_scanning_alert":
            raise ValueError("GitHub webhook is not a secret scanning alert")
        payload = json.loads(body)
        if not isinstance(payload, dict):
            raise ValueError("GitHub webhook payload must be an object")
        action = payload.get("action")
        alert = payload.get("alert")
        repository = payload.get("repository")
        if action not in {"created", "reopened", "resolved"}:
            raise ValueError("GitHub secret scanning action is unsupported")
        if not isinstance(alert, dict) or not isinstance(repository, dict):
            raise ValueError("GitHub webhook is missing alert or repository metadata")
        repository_name = repository.get("full_name")
        alert_url = alert.get("html_url")
        secret_type = alert.get("secret_type_display_name") or alert.get("secret_type")
        alert_number = alert.get("number")
        if (
            not isinstance(repository_name, str)
            or not isinstance(alert_url, str)
            or not isinstance(alert_number, int)
        ):
            raise ValueError("GitHub webhook metadata is incomplete")
        provider = _provider(secret_type)
        observed = _datetime(alert.get("created_at"), received_at)
        return IngestionEvent(
            id=new_id("ingestion"),
            organisation_id=organisation_id,
            source="github-secret-scanning",
            source_event_id=f"{repository_name}#{alert_number}",
            kind="credential-exposure-detected" if action != "resolved" else "exposure-resolved",
            observed_at=observed,
            severity=Severity.CRITICAL if action != "resolved" else Severity.MEDIUM,
            confidence=Confidence.HIGH,
            resource=SourceResource(repository=repository_name, provider=provider),
            source_reference=alert_url,
            received_at=received_at,
        )


def _provider(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    lowered = value.lower()
    providers = {
        "sendgrid": "sendgrid",
        "github": "github",
        "stripe": "stripe",
        "google": "google-cloud",
        "cloudflare": "cloudflare",
    }
    return next((provider for name, provider in providers.items() if name in lowered), None)


def _datetime(value: Any, fallback: datetime) -> datetime:
    if not isinstance(value, str):
        return fallback
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return fallback
