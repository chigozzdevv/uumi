from urllib.parse import quote

from contracts import AuditEvent

from connectors.google import GoogleRestClient


class CloudLoggingConnector:
    def __init__(
        self,
        client: GoogleRestClient,
        project_id: str,
        log_id: str = "uumi-audit",
    ) -> None:
        self._client = client
        self._project = project_id
        self._log_name = f"projects/{project_id}/logs/{quote(log_id, safe='')}"

    async def write(self, event: AuditEvent) -> str:
        await self._client.request(
            "POST",
            "https://logging.googleapis.com/v2/entries:write",
            json={
                "logName": self._log_name,
                "resource": {
                    "type": "generic_task",
                    "labels": {
                        "project_id": self._project,
                        "location": event.region,
                        "namespace": event.organisation_id,
                        "job": "uumi-audit",
                        "task_id": event.id,
                    },
                },
                "labels": {
                    "uumi_organisation_id": event.organisation_id,
                    "uumi_kind": event.kind,
                },
                "entries": [
                    {
                        "insertId": event.event_hash,
                        "timestamp": event.occurred_at.isoformat(),
                        "severity": "NOTICE",
                        "jsonPayload": event.model_dump(mode="json", exclude_none=True),
                    }
                ],
                "partialSuccess": False,
            },
        )
        return event.event_hash
