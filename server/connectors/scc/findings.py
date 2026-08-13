from datetime import datetime
from typing import Any

from contracts import Confidence, IngestionEvent, Severity, SourceResource
from core.ids import new_id


class SecurityCommandCenterFinding:
    def normalise(
        self,
        organisation_id: str,
        message_id: str,
        payload: dict[str, Any],
        received_at: datetime,
    ) -> IngestionEvent:
        finding = payload.get("finding")
        resource = payload.get("resource")
        if not isinstance(finding, dict) or not isinstance(resource, dict):
            raise ValueError("SCC notification is missing finding or resource")
        name = finding.get("name")
        category = finding.get("category")
        severity = finding.get("severity")
        state = finding.get("state")
        if not isinstance(name, str) or not isinstance(category, str):
            raise ValueError("SCC finding metadata is incomplete")
        if not isinstance(severity, str) or not isinstance(state, str):
            raise ValueError("SCC finding metadata is incomplete")
        resource_name = resource.get("name")
        project = resource.get("projectDisplayName") or resource.get("project")
        service = resource.get("service")
        return IngestionEvent(
            id=new_id("ingestion"),
            organisation_id=organisation_id,
            source="security-command-center",
            source_event_id=message_id,
            kind="credential-exposure-detected",
            observed_at=_datetime(finding.get("eventTime"), received_at),
            severity=_severity(severity),
            confidence=Confidence.HIGH if state == "ACTIVE" else Confidence.MEDIUM,
            resource=SourceResource(
                project=project if isinstance(project, str) else None,
                service=service if isinstance(service, str) else None,
                provider="google-cloud",
                provider_id=resource_name if isinstance(resource_name, str) else None,
            ),
            source_reference=name,
            received_at=received_at,
        )


def _severity(value: str) -> Severity:
    return {
        "CRITICAL": Severity.CRITICAL,
        "HIGH": Severity.HIGH,
        "MEDIUM": Severity.MEDIUM,
        "LOW": Severity.LOW,
    }.get(value.upper(), Severity.MEDIUM)


def _datetime(value: Any, fallback: datetime) -> datetime:
    if not isinstance(value, str):
        return fallback
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return fallback
