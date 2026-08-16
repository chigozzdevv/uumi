from datetime import UTC, datetime

from contracts import (
    HttpAuth,
    HttpAuthScheme,
    HttpOperation,
    HttpProviderApi,
    PolicyDefinition,
    PolicyState,
    PolicyVersion,
    RecoveryMode,
    RotationRun,
    Stage,
    StageProof,
    Trigger,
)
from policy import REQUIRED_CHECKS, GatePolicy, digest


def make_http_provider_api(
    base_url: str = "https://api.sendgrid.com/v3",
    scheme: HttpAuthScheme = HttpAuthScheme.BEARER,
) -> HttpProviderApi:
    return HttpProviderApi(
        base_url=base_url,
        auth=HttpAuth(scheme=scheme),
        list_credentials=HttpOperation(
            method="GET",
            path="/api_keys",
            success_statuses=(200,),
            query={"limit": "500"},
            list_items="result",
            provider_id_field="api_key_id",
            name_field="name",
        ),
        create_credential=HttpOperation(
            method="POST",
            path="/api_keys",
            success_statuses=(201,),
            body={"name": "${name}", "scopes": "${scopes}"},
            provider_id_field="api_key_id",
            secret_field="api_key",
            name_field="name",
        ),
        revoke_credential=HttpOperation(
            method="DELETE",
            path="/api_keys/{provider_id}",
            success_statuses=(204,),
        ),
    )


def make_policy_version(
    organisation_id: str = "org_one",
    version_id: str = "policy_one",
    now: datetime | None = None,
) -> PolicyVersion:
    current = now or datetime.now(UTC)
    definition = PolicyDefinition(
        required_checks=REQUIRED_CHECKS,
        allowed_tools=frozenset({"provider.create", "verification.run"}),
        allowed_recovery_modes=frozenset({RecoveryMode.ROLLBACK}),
        maximum_observation_seconds=1800,
    )
    return PolicyVersion(
        id=version_id,
        organisation_id=organisation_id,
        policy_id="policy_default",
        number=1,
        definition=definition,
        digest=digest(definition),
        state=PolicyState.ACTIVE,
        created_by="admin_one",
        created_at=current,
        approved_by="approver_one",
        approved_at=current,
    )


def make_run(now: datetime | None = None) -> RotationRun:
    current = now or datetime.now(UTC)
    return RotationRun(
        id="run_one",
        organisation_id="org_one",
        credential_id="cred_one",
        trigger=Trigger(
            source="schedule",
            event_id="event-one",
            actor_id="service_one",
            reason="routine rotation",
            urgency="routine",
            received_at=current,
        ),
        policy_version="policy_one",
        created_at=current,
        updated_at=current,
    )


def make_proof(stage: Stage, now: datetime | None = None) -> StageProof:
    current = now or datetime.now(UTC)
    return StageProof(
        run_id="run_one",
        organisation_id="org_one",
        stage=stage,
        checks=GatePolicy().checks(stage),
        evidence_ids=(f"evidence_{stage.value}",),
        actor_id="service_one",
        recorded_at=current,
    )
