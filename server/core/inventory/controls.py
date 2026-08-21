from datetime import datetime

from contracts import (
    Connection,
    ConnectionInterface,
    ConnectionRole,
    ConnectionStatus,
    ConsumerBinding,
    ConsumerService,
    ControlDefinition,
    ControlPreferences,
    GenerationBinding,
    ManagedCredential,
    ProbeDefinition,
    ProbeKind,
    ProbeState,
    ProbeVersion,
    RecoveryAction,
    RecoveryBranch,
    RecoveryMode,
    Stage,
    TargetBinding,
    TelemetryThresholds,
)
from policy import REQUIRED_CHECKS, digest

from core.errors import ResourceConflictError
from core.ids import new_id


def compile_controls(
    credential: ManagedCredential,
    bindings: tuple[ConsumerBinding, ...],
    services: tuple[ConsumerService, ...],
    connections: tuple[Connection, ...],
    preferences: ControlPreferences,
    actor_id: str,
    now: datetime,
) -> tuple[ControlDefinition, tuple[ProbeVersion, ...]]:
    by_id = {connection.id: connection for connection in connections}
    management = by_id[credential.connection_id]
    secret_store = by_id[credential.secret_store_connection_id]
    verification = next(
        (
            connection
            for connection in connections
            if connection.platform == credential.provider
            and ConnectionRole.PROVIDER in connection.roles
            and connection.interface is ConnectionInterface.API
            and connection.status is ConnectionStatus.READY
            and connection.http is not None
            and connection.http.test_credential is not None
            and connection.http.credential_auth is not None
        ),
        None,
    )
    if verification is None:
        raise ResourceConflictError(
            "credential automation requires a typed workload authentication test"
        )
    probes: list[ProbeVersion] = []
    assigned: dict[Stage, list[str]] = {
        Stage.VERIFY: [],
        Stage.OBSERVE: [],
        Stage.REVOKE: [],
    }

    def add(stage: Stage, definition: ProbeDefinition) -> None:
        version = ProbeVersion(
            id=definition.id,
            organisation_id=credential.organisation_id,
            probe_id=new_id("probe"),
            number=1,
            definition=definition,
            digest=digest(definition),
            state=ProbeState.ACTIVE,
            created_by=actor_id,
            created_at=now,
            approved_by=actor_id,
            approved_at=now,
        )
        probes.append(version)
        assigned[stage].append(version.id)

    add(
        Stage.VERIFY,
        _probe(
            credential,
            ProbeKind.PROVIDER,
            management.id,
            credential.provider_id or credential.id,
            GenerationBinding.TARGET,
            TargetBinding.PROVIDER_ID,
        ),
    )
    add(
        Stage.VERIFY,
        _probe(
            credential,
            ProbeKind.CREDENTIAL,
            verification.id,
            credential.provider_id or credential.id,
            GenerationBinding.TARGET,
            TargetBinding.PROVIDER_ID,
            secret_reference=credential.secret_reference,
            secret_connection_id=secret_store.id,
        ),
    )
    add(
        Stage.VERIFY,
        _probe(
            credential,
            ProbeKind.SECRET,
            secret_store.id,
            credential.secret_reference,
            GenerationBinding.TARGET,
            TargetBinding.SECRET_REFERENCE,
        ),
    )
    add(
        Stage.REVOKE,
        _probe(
            credential,
            ProbeKind.PROVIDER,
            management.id,
            credential.provider_id or credential.id,
            GenerationBinding.CURRENT,
            TargetBinding.PROVIDER_ID,
            negative=True,
        ),
    )
    add(
        Stage.REVOKE,
        _probe(
            credential,
            ProbeKind.CREDENTIAL,
            verification.id,
            credential.provider_id or credential.id,
            GenerationBinding.CURRENT,
            TargetBinding.PROVIDER_ID,
            negative=True,
            secret_reference=credential.secret_reference,
            secret_connection_id=secret_store.id,
        ),
    )
    add(
        Stage.REVOKE,
        _probe(
            credential,
            ProbeKind.SECRET,
            secret_store.id,
            credential.secret_reference,
            GenerationBinding.CURRENT,
            TargetBinding.SECRET_REFERENCE,
        ),
    )

    service_by_id = {service.id: service for service in services}
    for binding in bindings:
        add(
            Stage.VERIFY,
            _probe(
                credential,
                ProbeKind.RUNTIME,
                binding.runtime_connection_id,
                binding.runtime_resource,
                GenerationBinding.TARGET,
            ),
        )
        add(
            Stage.OBSERVE,
            _probe(
                credential,
                ProbeKind.RUNTIME,
                binding.runtime_connection_id,
                binding.runtime_resource,
                GenerationBinding.TARGET,
            ),
        )
        service = service_by_id[binding.service_id]
        functional = service.verification
        if functional is None:
            raise ResourceConflictError(
                f"consumer service {service.display_name} has no functional verification"
            )
        add(
            Stage.VERIFY,
            ProbeDefinition(
                id=binding.verification_id,
                organisation_id=credential.organisation_id,
                kind=functional.kind,
                connection_id=binding.runtime_connection_id,
                target=functional.target,
                method=functional.method,
                expected_status=functional.expected_status,
                generation_binding=GenerationBinding.TARGET,
                required_fields=functional.required_fields,
                confirmation=functional.confirmation,
                timeout_seconds=functional.timeout_seconds,
            ),
        )
        for connection_id in service.telemetry_connection_ids:
            connection = by_id[connection_id]
            telemetry = _probe(
                credential,
                ProbeKind.TELEMETRY,
                connection.id,
                connection.allowed_resources[0],
                GenerationBinding.TARGET,
                telemetry=TelemetryThresholds(
                    window_seconds=preferences.maximum_observation_seconds
                ),
            )
            add(Stage.VERIFY, telemetry)
            add(Stage.OBSERVE, telemetry.model_copy(update={"id": new_id("probeversion")}))
            add(
                Stage.OBSERVE,
                telemetry.model_copy(
                    update={
                        "id": new_id("probeversion"),
                        "generation_binding": GenerationBinding.CURRENT,
                        "negative": True,
                    }
                ),
            )

    telemetry_enabled = any(
        service_by_id[binding.service_id].telemetry_connection_ids for binding in bindings
    )
    allowed_tools = {
        "secretStore.getVersion",
        "secretStore.testConsumerAccess",
        "secretStore.disableVersion",
        "secretStore.destroyVersion",
        "runtime.inspectSecretBindings",
        "runtime.deployCandidate",
        "runtime.shiftTraffic",
        "runtime.rollback",
        "verification.run",
    }
    protected_tools = {"secretStore.disableVersion", "secretStore.destroyVersion"}
    if management.interface is ConnectionInterface.BROWSER:
        allowed_tools.update({"browser.click", "browser.secure-capture", "provider.testCredential"})
        protected_tools.update({"browser.click", "browser.secure-capture"})
    else:
        allowed_tools.update(
            {
                "provider.listCredentialMetadata",
                "provider.getCredentialStatus",
                "provider.createCredential",
                "provider.revokeCredential",
                "provider.testCredential",
            }
        )
        protected_tools.add("provider.revokeCredential")
    if telemetry_enabled:
        allowed_tools.update({"telemetry.queryHealth", "telemetry.queryCredentialUsage"})

    rollback = RecoveryBranch(
        mode=RecoveryMode.ROLLBACK,
        actions=tuple(
            RecoveryAction(
                tool="runtime.rollback",
                operation="rollback",
                parameters={
                    "connection_id": binding.runtime_connection_id,
                    "service": binding.runtime_resource,
                },
            )
            for binding in bindings
        ),
        preserves_old_generation=True,
    )
    required_checks = dict(REQUIRED_CHECKS)
    if not telemetry_enabled:
        required_checks[Stage.VERIFY] = required_checks[Stage.VERIFY].difference(
            {"telemetry-healthy"}
        )
        required_checks[Stage.OBSERVE] = required_checks[Stage.OBSERVE].difference(
            {"telemetry-healthy", "old-use-clear"}
        )
    definition = ControlDefinition(
        required_checks=required_checks,
        allowed_tools=frozenset(allowed_tools),
        protected_tools=frozenset(protected_tools),
        allowed_recovery_modes=frozenset({RecoveryMode.ROLLBACK}),
        maximum_observation_seconds=preferences.maximum_observation_seconds,
        require_functional_probe=True,
        require_generation_telemetry=telemetry_enabled,
        rotate_before_expiry_seconds=preferences.rotate_before_expiry_seconds,
        automatic_triggers=preferences.automatic_triggers,
        emergency_triggers=frozenset(
            {"verified-exposure"}.intersection(preferences.automatic_triggers)
        ),
        probe_versions={stage: tuple(ids) for stage, ids in assigned.items()},
        recovery={
            Stage.DEPLOY: rollback,
            Stage.VERIFY: rollback,
            Stage.ROLLOUT: rollback,
            Stage.OBSERVE: rollback,
        },
    )
    return definition, tuple(probes)


def update_controls(
    current: ControlDefinition,
    preferences: ControlPreferences,
) -> ControlDefinition:
    return current.model_copy(
        update={
            "automatic_triggers": preferences.automatic_triggers,
            "emergency_triggers": frozenset(
                {"verified-exposure"}.intersection(preferences.automatic_triggers)
            ),
            "rotate_before_expiry_seconds": preferences.rotate_before_expiry_seconds,
            "maximum_observation_seconds": preferences.maximum_observation_seconds,
        }
    )


def _probe(
    credential: ManagedCredential,
    kind: ProbeKind,
    connection_id: str,
    target: str,
    generation_binding: GenerationBinding,
    target_binding: TargetBinding = TargetBinding.STATIC,
    *,
    negative: bool = False,
    telemetry: TelemetryThresholds | None = None,
    secret_reference: str | None = None,
    secret_connection_id: str | None = None,
) -> ProbeDefinition:
    return ProbeDefinition(
        id=new_id("probeversion"),
        organisation_id=credential.organisation_id,
        kind=kind,
        connection_id=connection_id,
        target=target,
        generation_binding=generation_binding,
        target_binding=target_binding,
        telemetry=telemetry,
        secret_reference=secret_reference,
        secret_connection_id=secret_connection_id,
        negative=negative,
    )
