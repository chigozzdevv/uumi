from datetime import UTC, datetime, timedelta

import pytest
from contracts import (
    ConsumerBinding,
    CredentialGeneration,
    DryRun,
    DryRunStatus,
    ExecutionMethod,
    GenerationState,
    ManagedCredential,
    Playbook,
    PlaybookAssignment,
    PlaybookDraft,
    PlaybookState,
    PlaybookStep,
    PlaybookVersion,
    RecoveryAction,
    RecoveryBranch,
    Stage,
)
from core.audit import GENESIS, event_hash
from core.errors import PlaybookError, ResourceConflictError
from core.inventory import InventoryService
from core.playbook import PlaybookService

NOW = datetime(2026, 8, 13, 12, tzinfo=UTC)


class Playbooks:
    def __init__(self) -> None:
        self.version: PlaybookVersion | None = None
        self.dryrun: DryRun | None = None

    async def add_version(
        self,
        playbook_id: str,
        version_id: str,
        organisation_id: str,
        definition: PlaybookDraft,
        definition_digest: str,
        actor_id: str,
        created_at: datetime,
        source_ids: tuple[str, ...],
    ) -> tuple[Playbook, PlaybookVersion]:
        root = Playbook(
            id=playbook_id,
            organisation_id=organisation_id,
            name=definition.name,
            provider=definition.provider,
            latest_version=1,
            created_at=created_at,
            updated_at=created_at,
        )
        self.version = PlaybookVersion(
            id=version_id,
            organisation_id=organisation_id,
            playbook_id=playbook_id,
            number=1,
            definition=definition,
            digest=definition_digest,
            state=PlaybookState.TEST,
            source_ids=source_ids,
            created_by=actor_id,
            created_at=created_at,
        )
        return root, self.version

    async def get_version(
        self, organisation_id: str, playbook_id: str, version_id: str
    ) -> PlaybookVersion:
        assert self.version is not None
        return self.version

    async def get_dryrun(
        self, organisation_id: str, playbook_id: str, dryrun_id: str
    ) -> DryRun | None:
        return self.dryrun

    async def validate_dryrun(
        self,
        organisation_id: str,
        playbook_id: str,
        version_id: str,
        environment_id: str,
        credential_id: str,
    ) -> None:
        return None

    async def activate(
        self,
        organisation_id: str,
        playbook_id: str,
        version_id: str,
        dryrun_id: str,
        actor_id: str,
        activated_at: datetime,
    ) -> PlaybookVersion:
        assert self.version is not None
        assert self.dryrun is not None and self.dryrun.status is DryRunStatus.PASSED
        self.version = self.version.model_copy(
            update={
                "state": PlaybookState.ACTIVE,
                "approved_by": actor_id,
                "approved_at": activated_at,
            }
        )
        return self.version

    async def assign(self, assignment: PlaybookAssignment) -> PlaybookAssignment:
        return assignment

    async def list_playbooks(self, organisation_id: str, limit: int) -> tuple[Playbook, ...]:
        return ()


class Inventory:
    async def add_connection(self, value: object) -> object:
        return value

    async def add_application(self, value: object) -> object:
        return value

    async def add_environment(self, value: object) -> object:
        return value

    async def add_service(self, value: object) -> object:
        return value

    async def get_application(self, organisation_id: str, resource_id: str) -> object:
        raise NotImplementedError

    async def get_environment(self, organisation_id: str, resource_id: str) -> object:
        raise NotImplementedError

    async def get_connection(self, organisation_id: str, resource_id: str) -> object:
        raise NotImplementedError

    async def import_credential(
        self,
        credential: ManagedCredential,
        generation: CredentialGeneration,
        bindings: tuple[ConsumerBinding, ...],
    ) -> ManagedCredential:
        return credential

    async def credentials(self, organisation_id: str) -> tuple[ManagedCredential, ...]:
        return ()

    async def services(self, organisation_id: str) -> tuple[object, ...]:
        return ()

    async def bindings(self, organisation_id: str) -> tuple[ConsumerBinding, ...]:
        return ()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_playbook_requires_dryrun_before_activation() -> None:
    repository = Playbooks()
    service = PlaybookService(repository, clock=lambda: NOW)
    _, version = await service.create_version(
        "org_one", "playbook_one", "version_one", _draft(), "author_one"
    )

    with pytest.raises(PlaybookError, match="activation"):
        await service.activate("org_one", "playbook_one", version.id, "dryrun_one", "admin_one")

    dryrun = DryRun(
        id="dryrun_one",
        organisation_id="org_one",
        playbook_id="playbook_one",
        version_id=version.id,
        run_id="run_one",
        status=DryRunStatus.PASSED,
        environment_id="test_one",
        credential_id="credential_one",
        requested_by="author_one",
        checks=frozenset({"created", "stored", "deployed", "verified", "cleaned"}),
        evidence_ids=("evidence_one",),
        started_at=NOW,
        completed_at=NOW + timedelta(minutes=1),
    )
    repository.dryrun = dryrun
    repository.version = version.model_copy(
        update={"dry_run_id": dryrun.id, "state": PlaybookState.APPROVAL}
    )
    active = await service.activate("org_one", "playbook_one", version.id, dryrun.id, "admin_one")

    assert active.state is PlaybookState.ACTIVE
    assert active.approved_by == "admin_one"


@pytest.mark.anyio
async def test_inventory_rejects_missing_consumer_binding() -> None:
    service = InventoryService(Inventory())  # type: ignore[arg-type]
    credential = ManagedCredential(
        id="credential_one",
        organisation_id="org_one",
        connection_id="connection_one",
        provider="provider",
        kind="api-key",
        display_name="Production key",
        consumer_ids=("service_one",),
        active_generation_id="generation_one",
        policy_version="policy_one",
        playbook_version="version_one",
        created_at=NOW,
        updated_at=NOW,
    )
    generation = CredentialGeneration(
        id="generation_one",
        organisation_id="org_one",
        credential_id=credential.id,
        state=GenerationState.ACTIVE,
        attempt_id="attempt_one",
        created_at=NOW,
    )

    with pytest.raises(ResourceConflictError, match="must match"):
        await service.import_credential(credential, generation, ())


def test_audit_hash_binds_sequence_and_previous_event() -> None:
    first = event_hash(
        "org_one",
        0,
        "credential.imported",
        "user_one",
        "credentials/credential_one",
        None,
        {"provider": "vendor"},
        (),
        GENESIS,
        NOW,
        "us-east1",
    )
    second = event_hash(
        "org_one",
        1,
        "credential.updated",
        "user_one",
        "credentials/credential_one",
        None,
        {"provider": "vendor"},
        (),
        first,
        NOW,
        "us-east1",
    )

    assert first != second


def _draft() -> PlaybookDraft:
    stages = (
        Stage.CREATE,
        Stage.STORE,
        Stage.DEPLOY,
        Stage.VERIFY,
        Stage.ROLLOUT,
        Stage.OBSERVE,
        Stage.REVOKE,
    )
    steps = tuple(
        PlaybookStep(
            id=f"step_{stage.value}",
            stage=stage,
            tool=f"test.{stage.value}",
            operation=stage.value,
            objective=f"Execute the {stage.value} lifecycle stage",
            protected=stage is Stage.REVOKE,
            evidence_checks=frozenset({f"{stage.value}-passed"}),
        )
        for stage in stages
    )
    return PlaybookDraft(
        name="Provider rotation",
        provider="provider",
        execution=ExecutionMethod.API,
        allowed_tools=frozenset(step.tool for step in steps),
        required_connections=("provider_one", "secret_one", "runtime_one"),
        steps=steps,
        recovery={
            stage.value: RecoveryBranch(
                mode="rollforward" if stage is Stage.REVOKE else "rollback",
                actions=(
                    RecoveryAction(
                        tool="test.recover",
                        operation="recover",
                        parameters={"connection_id": "runtime_one"},
                    ),
                ),
                preserves_old_generation=stage is not Stage.REVOKE,
            )
            for stage in stages
        },
    )
