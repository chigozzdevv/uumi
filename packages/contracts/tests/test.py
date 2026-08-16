from datetime import UTC, datetime, timedelta

import pytest
from contracts import (
    Approval,
    ApprovalDecision,
    BrowserActionKind,
    BrowserPolicy,
    BrowserSession,
    BrowserStatus,
    DryRun,
    DryRunStatus,
    EventKind,
    ExecutionMethod,
    MutationMode,
    MutationSemantics,
    OutboxEvent,
    PageCheckpoint,
    PlaybookDraft,
    PlaybookStep,
    PolicyDefinition,
    PolicyState,
    PolicyVersion,
    RecoveryMode,
    RotationPlan,
    RotationStrategy,
    RunEvent,
    RunStatus,
    Selector,
    SelectorKind,
    Stage,
)
from policy import REQUIRED_CHECKS, digest
from pydantic import ValidationError

NOW = datetime.now(UTC)
DIGEST = "a" * 64


def test_native_mutation_requires_provider_token() -> None:
    with pytest.raises(ValidationError, match="provider token"):
        MutationSemantics(mode=MutationMode.NATIVE)


def test_compensatable_mutation_requires_cleanup() -> None:
    with pytest.raises(ValidationError, match="compensation action"):
        MutationSemantics(mode=MutationMode.COMPENSATABLE)


def test_rollout_must_be_ordered_and_complete() -> None:
    with pytest.raises(ValidationError, match="unique and increasing"):
        RotationPlan(
            id="plan_one",
            organisation_id="org_one",
            run_id="run_one",
            credential_id="cred_one",
            policy_version="policy_one",
            playbook_version="playbook_one",
            strategy=RotationStrategy.PARALLEL,
            target_scopes=frozenset({"mail.send"}),
            consumer_ids=("service_one",),
            rollout=(25, 5, 100),
            observation_seconds=60,
            recovery_ids={Stage.DEPLOY: "recovery_one"},
        )


def test_approval_consumption_requires_approved_decision() -> None:
    with pytest.raises(ValidationError, match="only an approved action"):
        Approval(
            id="approval_one",
            organisation_id="org_one",
            run_id="run_one",
            action_id="action_one",
            action_digest=DIGEST,
            plan_hash=DIGEST,
            evidence_hash=DIGEST,
            generation_id="generation_one",
            requested_by="service_one",
            capability_hash=DIGEST,
            decision=ApprovalDecision.REJECTED,
            approver_id="user_one",
            expires_at=NOW + timedelta(minutes=10),
            created_at=NOW,
            decided_at=NOW,
            consumed_at=NOW,
        )


def test_contracts_are_immutable() -> None:
    mutation = MutationSemantics(
        mode=MutationMode.COMPENSATABLE,
        compensation="revoke-attributable-orphan",
    )

    with pytest.raises(ValidationError, match="frozen"):
        setattr(mutation, "compensation", "retry")  # noqa: B010


def test_outbox_lease_must_be_complete() -> None:
    event = RunEvent(
        id="event_one",
        organisation_id="org_one",
        run_id="run_one",
        credential_id="cred_one",
        kind=EventKind.RUN_CREATED,
        revision=0,
        stage=Stage.TRIGGER,
        status=RunStatus.PENDING,
        actor_id="service_one",
        occurred_at=NOW,
    )

    with pytest.raises(ValidationError, match="owner and expiry"):
        OutboxEvent(
            event=event,
            available_at=NOW,
            lease_owner="publisher_one",
        )


def test_outbox_publication_requires_provider_receipt() -> None:
    event = RunEvent(
        id="event_one",
        organisation_id="org_one",
        run_id="run_one",
        credential_id="cred_one",
        kind=EventKind.RUN_CREATED,
        revision=0,
        stage=Stage.TRIGGER,
        status=RunStatus.PENDING,
        actor_id="service_one",
        occurred_at=NOW,
    )

    with pytest.raises(ValidationError, match="message ID"):
        OutboxEvent(event=event, available_at=NOW, published_at=NOW)


def test_computer_playbook_requires_a_login_url_pattern() -> None:
    from contracts import SecureField

    with pytest.raises(ValidationError, match="login URL"):
        PlaybookDraft(
            name="Vendor rotation",
            provider="vendor",
            execution=ExecutionMethod.COMPUTER,
            allowed_domains=("vendor.example.com",),
            allowed_tools=frozenset({"browser.secure-capture"}),
            required_connections=("connection_one",),
            steps=(
                PlaybookStep(
                    id="step_one",
                    stage=Stage.CREATE,
                    tool="browser.secure-capture",
                    operation="capture",
                    objective="Capture the created key",
                    selectors=(Selector(kind=SelectorKind.TEST_ID, value="new-api-key"),),
                    checkpoint=PageCheckpoint(url_pattern="https://vendor.example.com/keys"),
                    secure_field=SecureField(
                        name="api_key",
                        selector=Selector(kind=SelectorKind.TEST_ID, value="new-api-key"),
                        sink_connection_id="sink_one",
                        secret_resource="projects/project-one/secrets/key",
                        provider_id_selector=Selector(
                            kind=SelectorKind.TEST_ID, value="new-key-id"
                        ),
                    ),
                    evidence_checks=frozenset({"captured"}),
                ),
            ),
        )


def test_computer_playbook_requires_secure_capture() -> None:
    with pytest.raises(ValidationError, match="secure capture"):
        PlaybookDraft(
            name="Vendor rotation",
            provider="vendor",
            execution=ExecutionMethod.COMPUTER,
            allowed_domains=("vendor.example.com",),
            allowed_tools=frozenset({"browser.click"}),
            required_connections=("connection_one",),
            steps=(
                PlaybookStep(
                    id="step_one",
                    stage=Stage.CREATE,
                    tool="browser.click",
                    operation="click",
                    objective="Click the Create control",
                    selectors=(Selector(kind=SelectorKind.ROLE, value="button", name="Create"),),
                    checkpoint=PageCheckpoint(url_pattern="https://vendor.example.com/keys"),
                    evidence_checks=frozenset({"page-confirmed"}),
                ),
            ),
        )


def test_secure_capture_session_requires_both_barriers() -> None:
    with pytest.raises(ValidationError, match="barriers"):
        BrowserSession(
            id="session_one",
            organisation_id="org_one",
            run_id="run_one",
            playbook_id="playbook_one",
            playbook_version="playbook_one",
            provider_connection_id="provider_one",
            status=BrowserStatus.CAPTURING,
            policy=BrowserPolicy(
                allowed_domains=("vendor.example.com",),
                allowed_actions=frozenset({BrowserActionKind.CLICK}),
            ),
            fencing_token=1,
            model_paused=False,
            recording_paused=True,
            created_at=NOW,
            updated_at=NOW,
            expires_at=NOW + timedelta(minutes=30),
        )


def test_terminal_dry_run_requires_completion() -> None:
    with pytest.raises(ValidationError, match="completion time"):
        DryRun(
            id="dryrun_one",
            organisation_id="org_one",
            playbook_id="playbook_one",
            version_id="version_one",
            run_id="run_one",
            status=DryRunStatus.FAILED,
            environment_id="test_one",
            credential_id="credential_one",
            requested_by="actor_one",
            failure="checkpoint changed",
            started_at=NOW,
        )


def test_active_policy_requires_approval_and_all_stage_checks() -> None:
    definition = PolicyDefinition(
        required_checks=REQUIRED_CHECKS,
        allowed_tools=frozenset({"provider.create", "verification.run"}),
        allowed_recovery_modes=frozenset({RecoveryMode.ROLLBACK}),
        maximum_observation_seconds=1800,
    )

    with pytest.raises(ValidationError, match="require approval"):
        PolicyVersion(
            id="policy_version_one",
            organisation_id="org_one",
            policy_id="policy_one",
            number=1,
            definition=definition,
            digest=digest(definition),
            state=PolicyState.ACTIVE,
            created_by="admin_one",
            created_at=NOW,
        )

    with pytest.raises(ValidationError, match="at least 12 items"):
        PolicyDefinition(
            required_checks={Stage.TRIGGER: REQUIRED_CHECKS[Stage.TRIGGER]},
            allowed_tools=frozenset({"provider.create", "verification.run"}),
            allowed_recovery_modes=frozenset({RecoveryMode.ROLLBACK}),
            maximum_observation_seconds=1800,
        )
