import os
import secrets
from datetime import UTC, datetime, timedelta

import pytest
from contracts import CreateRunCommand, MemberRole, RunStatus, StartRunCommand, Trigger
from core.account import AccountService, FirestoreAccountRepository
from core.auth import AuthenticatedIdentity
from core.errors import IdempotencyConflictError
from core.storage import FirestoreRunRepository
from core.storage.paths import FirestorePaths
from core.workflow import RunWorkflow
from google.cloud.firestore_v1 import AsyncClient
from testkit import make_control_version

if "FIRESTORE_EMULATOR_HOST" not in os.environ:
    pytest.skip("Firestore emulator is not running", allow_module_level=True)

pytestmark = pytest.mark.integration
NOW = datetime(2026, 8, 13, 12, tzinfo=UTC)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_transactions_persist_and_deduplicate_run_commands() -> None:
    suffix = secrets.token_hex(6)
    organisation_id = f"org_{suffix}"
    credential_id = f"cred_{suffix}"
    run_id = f"run_{suffix}"
    create_id = f"cmd_create_{suffix}"
    start_id = f"cmd_start_{suffix}"
    client = AsyncClient(project="uumi-test")
    controls = make_control_version(organisation_id, credential_id=credential_id, now=NOW)
    await client.document(
        FirestorePaths.control_version(organisation_id, credential_id, controls.id)
    ).set(controls.model_dump(mode="json"))
    repository = FirestoreRunRepository(client)
    workflow = RunWorkflow(
        repository,
        clock=lambda: NOW,
        id_factory=lambda prefix: run_id,
    )
    create = CreateRunCommand(
        id=create_id,
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

    try:
        created = await workflow.create(create)
        duplicate = await workflow.create(create.model_copy(update={"id": f"cmd_retry_{suffix}"}))
        start = StartRunCommand(
            id=start_id,
            organisation_id=organisation_id,
            run_id=created.run.id,
            actor_id="service_one",
            expected_revision=created.run.revision,
            owner_id="worker_one",
            expires_at=NOW + timedelta(minutes=5),
        )
        started = await workflow.start(start)
        repeated = await workflow.start(start)

        assert created.applied is True
        assert duplicate.applied is False
        assert duplicate.run.id == created.run.id
        assert started.applied is True
        assert repeated.applied is False
        assert repeated.run.revision == 1

        changed = start.model_copy(update={"owner_id": "worker_two"})
        with pytest.raises(IdempotencyConflictError, match="another mutation"):
            await workflow.start(changed)

        stored = await workflow.get(organisation_id, run_id)
        step = await client.document(FirestorePaths.step(organisation_id, run_id, start_id)).get()
        event = await client.document(FirestorePaths.outbox(organisation_id, start_id)).get()

        assert stored.revision == 1
        assert step.exists
        assert event.exists
        event_data = event.to_dict()
        assert event_data is not None
        assert event_data["event"]["revision"] == 1

        listed = await workflow.list_runs(organisation_id)
        running = await repository.count_runs(organisation_id, frozenset({RunStatus.RUNNING}))
        pending = await repository.count_runs(organisation_id, frozenset({RunStatus.PENDING}))
        assert [stored.id for stored in listed] == [run_id]
        assert running == 1
        assert pending == 0
    finally:
        client.close()  # type: ignore[no-untyped-call]


@pytest.mark.anyio
async def test_invited_identity_discovers_and_joins_its_organisation() -> None:
    suffix = secrets.token_hex(6)
    client = AsyncClient(project="uumi-test")
    repository = FirestoreAccountRepository(client, lambda: NOW)
    accounts = AccountService(repository, lambda: NOW)
    owner = AuthenticatedIdentity(
        subject=f"owner-{suffix}",
        issuer="https://securetoken.google.com/uumi-test",
        email=f"owner-{suffix}@uumi.test",
        email_verified=True,
        display_name="Owner",
        connected_via="Google",
    )
    invited = AuthenticatedIdentity(
        subject=f"invited-{suffix}",
        issuer=owner.issuer,
        email=f"invited-{suffix}@uumi.test",
        email_verified=True,
        display_name="Invited Member",
        connected_via="Email",
    )

    try:
        created = await accounts.create_organisation(owner, f"Organisation {suffix}")
        await accounts.invite(
            created.organisation.id,
            owner,
            invited.email or "",
            MemberRole.OPERATOR,
        )

        session = await accounts.session(invited)

        assert len(session.organisations) == 1
        assert session.organisations[0].organisation.id == created.organisation.id
        assert session.organisations[0].role is MemberRole.OPERATOR
    finally:
        client.close()  # type: ignore[no-untyped-call]
