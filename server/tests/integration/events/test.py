import json
import os
import secrets
from datetime import UTC, datetime, timedelta

import pytest
from contracts import CreateRunCommand, StartRunCommand, Trigger
from core.events import EventPublisher
from core.events.pubsub import PubSubEventTransport
from core.storage import FirestoreOutboxRepository, FirestoreRunRepository
from core.storage.paths import FirestorePaths
from core.workflow import RunWorkflow
from google.cloud.firestore_v1 import AsyncClient
from google.cloud.pubsub_v1 import (  # type: ignore[import-untyped]
    PublisherClient,
    SubscriberClient,
    types,
)
from testkit import make_control_version

if "FIRESTORE_EMULATOR_HOST" not in os.environ or "PUBSUB_EMULATOR_HOST" not in os.environ:
    pytest.skip("Firestore and Pub/Sub emulators are not running", allow_module_level=True)

pytestmark = pytest.mark.integration
NOW = datetime(2026, 8, 13, 12, tzinfo=UTC)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_outbox_delivers_ordered_events_once() -> None:
    suffix = secrets.token_hex(6)
    project_id = "firekey-test"
    topic_id = f"events-{suffix}"
    subscription_id = f"events-{suffix}"
    options = types.PublisherOptions(enable_message_ordering=True)
    topic_client = PublisherClient(publisher_options=options)
    subscriber = SubscriberClient()
    topic = topic_client.topic_path(project_id, topic_id)
    subscription = subscriber.subscription_path(project_id, subscription_id)
    topic_client.create_topic(request={"name": topic})
    subscriber.create_subscription(
        request={
            "name": subscription,
            "topic": topic,
            "enable_message_ordering": True,
        }
    )

    organisation_id = f"org_{suffix}"
    run_id = f"run_{suffix}"
    firestore = AsyncClient(project=project_id)
    credential_id = f"cred_{suffix}"
    controls = make_control_version(organisation_id, credential_id=credential_id, now=NOW)
    await firestore.document(
        FirestorePaths.control_version(organisation_id, credential_id, controls.id)
    ).set(controls.model_dump(mode="json"))
    times = iter((NOW, NOW + timedelta(seconds=1)))
    workflow = RunWorkflow(
        FirestoreRunRepository(firestore),
        clock=lambda: next(times),
        id_factory=lambda prefix: run_id,
    )
    create = CreateRunCommand(
        id=f"cmd_create_{suffix}",
        organisation_id=organisation_id,
        credential_id=credential_id,
        control_version=controls.id,
        trigger=Trigger(
            source="schedule",
            event_id=f"event-{suffix}",
            actor_id="service_one",
            reason="routine rotation",
            urgency="routine",
            received_at=NOW,
        ),
    )
    transport = PubSubEventTransport(project_id, topic_id, "us-east1")

    try:
        created = await workflow.create(create)
        await workflow.start(
            StartRunCommand(
                id=f"cmd_start_{suffix}",
                organisation_id=organisation_id,
                run_id=run_id,
                actor_id="service_one",
                expected_revision=created.run.revision,
                owner_id="worker_one",
                expires_at=NOW + timedelta(minutes=5),
            )
        )
        publisher = EventPublisher(
            FirestoreOutboxRepository(firestore),
            transport,
            owner_id="publisher_one",
            clock=lambda: NOW + timedelta(minutes=1),
            batch_size=10,
        )

        delivered = await publisher.drain()
        repeated = await publisher.drain()
        response = subscriber.pull(
            request={"subscription": subscription, "max_messages": 10},
            timeout=10,
        )
        messages = [json.loads(item.message.data) for item in response.received_messages]

        assert delivered.published == 2
        assert repeated.claimed == 0
        assert [message["revision"] for message in messages] == [0, 1]
        assert len({message["id"] for message in messages}) == 2

        cursor = await firestore.document(FirestorePaths.delivery(organisation_id, run_id)).get()
        cursor_data = cursor.to_dict()
        assert cursor_data is not None
        assert cursor_data["published_revision"] == 1
    finally:
        transport.close()
        subscriber.close()
        topic_client.stop()
        firestore.close()  # type: ignore[no-untyped-call]
