import asyncio

from contracts import RunEvent
from google.cloud.pubsub_v1 import PublisherClient, types  # type: ignore[import-untyped]


class PubSubEventTransport:
    def __init__(
        self,
        project_id: str,
        topic_id: str,
        region: str,
        timeout_seconds: float = 20,
    ) -> None:
        options = types.PublisherOptions(enable_message_ordering=True)
        client_options = {"api_endpoint": f"{region}-pubsub.googleapis.com:443"}
        self._client = PublisherClient(
            publisher_options=options,
            client_options=client_options,
        )
        self._topic = self._client.topic_path(project_id, topic_id)
        self._timeout_seconds = timeout_seconds

    async def publish(self, event: RunEvent) -> str:
        future = self._client.publish(
            self._topic,
            data=event.model_dump_json(exclude_none=True).encode(),
            ordering_key=event.run_id,
            event_id=event.id,
            event_kind=event.kind,
            organisation_id=event.organisation_id,
            run_id=event.run_id,
            revision=str(event.revision),
        )
        message_id = await asyncio.to_thread(future.result, timeout=self._timeout_seconds)
        return str(message_id)

    def close(self) -> None:
        self._client.stop()
