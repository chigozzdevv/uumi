import contextlib
import hashlib
import hmac
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from agents.runtime import AgentRuntimeService
from agents.shared.models import OperatorDecision
from broker.evidence import GcsEvidenceSink
from browser.playbook import browser_step_context, resolve_playbook_step
from connectors import ConnectorContext
from contracts import (
    AgentKind,
    AgentTask,
    Approval,
    ApprovalDecision,
    AuditEvent,
    Connection,
    ConnectionInterface,
    ConnectionRole,
    ConnectionStatus,
    ConnectionWaiter,
    ConsumerBinding,
    ControlVersion,
    CredentialGeneration,
    GenerationState,
    IncidentStatus,
    ManagedCredential,
    NotificationKind,
    OperationStep,
    PlaybookState,
    PlaybookStep,
    PlaybookVersion,
    ProbeDefinition,
    ProbeKind,
    ProbeState,
    ProbeVersion,
    ProtectedAction,
    RecoveryMode,
    RecoveryPlan,
    RecoveryResult,
    RotationPlan,
    RotationRun,
    RotationStrategy,
    RunStatus,
    RuntimeDeployment,
    Severity,
    Stage,
    StageBindings,
    StageExecutionRequest,
    StageExecutionResult,
    StageExecutionStatus,
    StageProof,
    VerificationReport,
    VerificationStatus,
)
from core.audit.chain import GENESIS, event_hash
from core.audit.writer import AuditWriter
from core.errors import ResourceConflictError, ResourceNotFoundError
from core.generation import GenerationService
from core.incident import IncidentService
from core.notification import NotificationService
from core.storage.catalog import FirestoreCatalog
from core.storage.paths import FirestorePaths
from policy import GatePolicy, digest
from pydantic import TypeAdapter
from verifier import VerificationService

from coordinator.broker import McpBrokerClient
from coordinator.browser import (
    BrowserPauseError,
    BrowserStepExecutor,
    BrowserWorkerError,
    is_deterministic_browser_step,
)


class StageExecutionError(ValueError):
    def __init__(self, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


def _operator_objective(step_id: str) -> str:
    return f"Review immutable browser step {step_id} for isolated Computer Use readiness."


def _operator_task_id(run_id: str, step_id: str, fencing_token: int) -> str:
    return _id("task", run_id, step_id, "operator", str(fencing_token))


@dataclass(frozen=True, slots=True)
class RotationContext:
    credential: ManagedCredential
    provider: Connection
    secret_store: Connection
    bindings: tuple[ConsumerBinding, ...]
    runtimes: dict[str, Connection]
    browser_playbook: PlaybookVersion | None

    @property
    def connections(self) -> tuple[Connection, ...]:
        values = [self.provider, self.secret_store, *self.runtimes.values()]
        return tuple({item.id: item for item in values}.values())


class StageCoordinator:
    def __init__(
        self,
        catalog: FirestoreCatalog,
        broker: McpBrokerClient,
        browser: BrowserStepExecutor,
        agents: AgentRuntimeService,
        verifier: VerificationService,
        generations: GenerationService,
        incidents: IncidentService,
        evidence: GcsEvidenceSink,
        audit: AuditWriter,
        clock: Callable[[], datetime],
        notifications: NotificationService | None = None,
    ) -> None:
        self._catalog = catalog
        self._broker = broker
        self._browser = browser
        self._agents = agents
        self._verifier = verifier
        self._generations = generations
        self._incidents = incidents
        self._evidence = evidence
        self._audit = audit
        self._clock = clock
        self._notifications = notifications
        self._controls: dict[tuple[str, str, str], tuple[ControlVersion, GatePolicy]] = {}

    async def execute(self, request: StageExecutionRequest) -> StageExecutionResult:
        started = self._clock()
        execution_id = _execution_id(request)
        path = FirestorePaths.stage(request.organisation_id, execution_id)
        run = await self._catalog.get(
            FirestorePaths.run(request.organisation_id, request.run_id), RotationRun
        )
        self._validate_request(request, run)
        try:
            current = await self._catalog.get(path, StageExecutionResult)
        except Exception as error:
            if not isinstance(error, ResourceNotFoundError):
                raise
        else:
            return current
        try:
            _, gates = await self._control(run)
            recovering = run.status is RunStatus.RECOVERING
            recovery_mode = None
            if recovering:
                checks, evidence_ids, bindings, output, recovery_mode = await self._recover(run)
            else:
                checks, evidence_ids, bindings, output = await self._dispatch(run)
            proof_evidence = await self._stage_evidence(run, checks, output)
            all_evidence = tuple(dict.fromkeys((*evidence_ids, *proof_evidence)))
            result = StageExecutionResult(
                id=execution_id,
                organisation_id=run.organisation_id,
                run_id=run.id,
                stage=run.stage,
                status=(
                    StageExecutionStatus.RECOVERED if recovering else StageExecutionStatus.SUCCEEDED
                ),
                checks=checks,
                evidence_ids=all_evidence,
                bindings=bindings,
                recovery_mode=recovery_mode,
                output=output,
                started_at=started,
                completed_at=self._clock(),
            )
            if not recovering:
                gates.validate(
                    StageProof(
                        run_id=run.id,
                        organisation_id=run.organisation_id,
                        stage=run.stage,
                        checks=result.checks,
                        evidence_ids=result.evidence_ids,
                        actor_id="coordinator_one",
                        recorded_at=result.completed_at,
                    )
                )
        except BrowserPauseError as pause:
            if pause.output.get("authentication_required") is True:
                await _flag_reauthentication(
                    self._catalog,
                    self._notifications,
                    self._clock(),
                    run,
                    pause.output,
                    execution_id,
                )
                with contextlib.suppress(Exception):
                    await self._browser.terminate(run)
            result = StageExecutionResult(
                id=execution_id,
                organisation_id=run.organisation_id,
                run_id=run.id,
                stage=run.stage,
                status=StageExecutionStatus.PAUSED,
                output=pause.output,
                reason=str(pause),
                started_at=started,
                completed_at=self._clock(),
            )
        except Exception as error:
            with contextlib.suppress(Exception):
                await self._browser.terminate(run)
            reason = f"{type(error).__name__}: {error}".replace("\n", " ")[:1024]
            evidence_ids = await self._failure_evidence(run, reason)
            output = error.output if isinstance(error, BrowserWorkerError) else {}
            result = StageExecutionResult(
                id=execution_id,
                organisation_id=run.organisation_id,
                run_id=run.id,
                stage=run.stage,
                status=StageExecutionStatus.FAILED,
                evidence_ids=evidence_ids,
                reason=reason,
                output=output,
                retryable=isinstance(error, StageExecutionError) and error.retryable,
                started_at=started,
                completed_at=self._clock(),
            )
        await self._audit.append(
            event_id=_id("audit", execution_id),
            organisation_id=run.organisation_id,
            kind=f"stage.{result.status.value}",
            actor_id="coordinator_one",
            resource=f"runs/{run.id}/stages/{run.stage.value}",
            run_id=run.id,
            payload={
                "stage": run.stage.value,
                "status": result.status.value,
                "checks": len(result.checks),
                "reason": result.reason,
            },
            evidence_ids=result.evidence_ids,
        )
        await self._catalog.create(path, result)
        return result

    async def _recover(
        self, run: RotationRun
    ) -> tuple[
        frozenset[str],
        tuple[str, ...],
        StageBindings,
        dict[str, Any],
        RecoveryMode,
    ]:
        if run.plan_id is None or run.recovery_stage is not run.stage:
            raise ValueError("recovery is not bound to a planned failed stage")
        plan = await self._catalog.get(
            FirestorePaths.plan(run.organisation_id, run.plan_id), RotationPlan
        )
        if digest(plan) != run.plan_hash:
            raise ValueError("rotation plan digest changed")
        recovery_id = plan.recovery_ids.get(run.stage)
        if recovery_id is None:
            raise ValueError(f"rotation plan has no recovery branch for {run.stage.value}")
        recovery = await self._catalog.get(
            FirestorePaths.recovery(run.organisation_id, recovery_id), RecoveryPlan
        )
        if (
            recovery.run_id != run.id
            or recovery.failed_stage is not run.stage
            or (recovery.preserves_old_generation and run.current_generation_id is None)
        ):
            raise ValueError("recovery plan binding is invalid")
        control_version, _ = await self._control(run)
        if recovery.mode not in control_version.definition.allowed_recovery_modes:
            raise ValueError("recovery mode is forbidden by the bound controls")
        recommendation = await self._agents.execute(
            AgentTask(
                id=_id("task", run.id, run.stage.value, "recovery"),
                organisation_id=run.organisation_id,
                run_id=run.id,
                agent=AgentKind.PLANNER,
                skill="recommend_authorised_recovery",
                objective=(
                    "Evaluate only the pre-authorised recovery branch and confirm whether its "
                    "declared actions remain eligible. Do not propose new tools or mutations."
                ),
                context={
                    "run": run.model_dump(mode="json"),
                    "plan": plan.model_dump(mode="json"),
                    "recovery": recovery.model_dump(mode="json"),
                },
                requested_at=self._clock(),
            )
        )
        if not recommendation.succeeded:
            raise ValueError("recovery agent could not evaluate the authorised branch")
        if (
            recommendation.output.get("decision") != "recovery"
            or recommendation.output.get("recovery_id") != recovery.id
            or recommendation.output.get("recovery_mode") != recovery.mode.value
            or recommendation.output.get("eligible") is not True
        ):
            raise ValueError("recovery agent changed or rejected the authorised branch")
        context = await self._step_context(run)
        outputs: list[dict[str, Any]] = []
        evidence: list[str] = list(recommendation.evidence_ids)
        for index, action in enumerate(recovery.steps):
            if action.tool not in control_version.definition.allowed_tools:
                raise ValueError(f"recovery tool {action.tool} is forbidden by controls")
            if action.tool in control_version.definition.protected_tools and not action.protected:
                raise ValueError(f"controls require protected recovery for {action.tool}")
            payload = _resolve(action.parameters, context)
            if not isinstance(payload, dict):
                raise ValueError("recovery action parameters are invalid")
            if action.tool == "runtime.rollback" and "rollback_revision" not in payload:
                connection = payload.get("connection_id")
                service = payload.get("service")
                matches = tuple(
                    deployment
                    for deployment in run.deployments
                    if deployment.connection_id == connection and deployment.service == service
                )
                if len(matches) != 1:
                    raise ValueError("runtime rollback does not match one pinned deployment")
                payload["rollback_revision"] = matches[0].rollback_revision
            connection_id = payload.pop("connection_id", None)
            if not isinstance(connection_id, str):
                raise ValueError(f"recovery action {action.tool} has no connection_id")
            approval = None
            if action.protected:
                step = PlaybookStep(
                    id=_id("recovery-step", recovery.id, str(index)),
                    stage=run.stage,
                    tool=action.tool,
                    operation=action.operation,
                    objective=f"Execute authorised {recovery.mode.value} recovery",
                    parameters=payload,
                    protected=True,
                    evidence_checks=frozenset({"recovery-authorised"}),
                )
                approval = await self._approval_for_step(run, step, payload, connection_id)
            result = await self._broker.execute(
                run,
                _id("recovery-tool", recovery.id, str(index)),
                connection_id,
                action.tool,
                payload,
                approval.id if approval is not None else None,
            )
            if not result.succeeded:
                raise ValueError(
                    f"recovery tool {action.tool} failed: {result.error_code or 'unknown'}"
                )
            outputs.append(result.result)
            evidence.extend(result.evidence_ids)
        checks = frozenset(
            {
                "recovery-plan-bound",
                "recovery-agent-approved",
                "recovery-actions-completed",
                "recovery-evidence-recorded",
            }
        )
        result_id = _id("recovery-result", run.id, str(run.revision))
        stored = RecoveryResult(
            id=result_id,
            organisation_id=run.organisation_id,
            run_id=run.id,
            recovery_id=recovery.id,
            failed_stage=run.stage,
            mode=recovery.mode,
            checks=checks,
            evidence_ids=tuple(dict.fromkeys(evidence)),
        )
        if not stored.evidence_ids:
            raise ValueError("recovery actions returned no independently stored evidence")
        await self._create_once(
            FirestorePaths.recovery_result(run.organisation_id, recovery.id, stored.id), stored
        )
        return (
            checks,
            stored.evidence_ids,
            StageBindings(),
            {
                "recovery_id": recovery.id,
                "mode": recovery.mode.value,
                "actions": outputs,
                "recommendation": recommendation.output,
            },
            recovery.mode,
        )

    def _validate_request(self, request: StageExecutionRequest, run: RotationRun) -> None:
        if run.revision != request.expected_revision or run.stage is not request.stage:
            raise ValueError("stage execution request is stale")
        if run.status not in {RunStatus.RUNNING, RunStatus.RECOVERING}:
            raise ValueError("stage execution requires an active run")
        if run.lease is None or run.fencing_token != request.fencing_token:
            raise ValueError("stage execution does not hold the active fence")
        if run.lease.expires_at <= self._clock():
            raise ValueError("stage execution lease expired")

    async def _dispatch(
        self, run: RotationRun
    ) -> tuple[frozenset[str], tuple[str, ...], StageBindings, dict[str, Any]]:
        handlers = {
            Stage.TRIGGER: self._trigger,
            Stage.PREFLIGHT: self._preflight,
            Stage.PLAN: self._plan,
            Stage.CREATE: self._create,
            Stage.STORE: self._store,
            Stage.DEPLOY: self._deploy,
            Stage.VERIFY: self._verify,
            Stage.ROLLOUT: self._rollout,
            Stage.OBSERVE: self._observe,
            Stage.APPROVAL: self._approval,
            Stage.REVOKE: self._revoke,
            Stage.COMPLETE: self._complete,
        }
        return await handlers[run.stage](run)

    async def _trigger(
        self, run: RotationRun
    ) -> tuple[frozenset[str], tuple[str, ...], StageBindings, dict[str, Any]]:
        dedupe = await self._catalog.client.document(
            FirestorePaths.dedupe(run.organisation_id, run.trigger.source, run.trigger.event_id)
        ).get()
        if not dedupe.exists or run.lease is None:
            raise ValueError("trigger dedupe or run lease is missing")
        output = {
            "source": run.trigger.source,
            "event_id": run.trigger.event_id,
            "actor_id": run.trigger.actor_id,
        }
        return (
            frozenset({"request-authenticated", "source-deduplicated", "lease-held"}),
            (),
            StageBindings(),
            output,
        )

    async def _preflight(
        self, run: RotationRun
    ) -> tuple[frozenset[str], tuple[str, ...], StageBindings, dict[str, Any]]:
        context = await self._rotation_context(run)
        credential = context.credential
        if (
            credential.active_generation_id is None
            or (context.provider.interface is ConnectionInterface.API and not credential.scopes)
            or not credential.consumer_ids
        ):
            raise ValueError("credential inventory is incomplete")
        now = self._clock()
        if any(
            item.status is not ConnectionStatus.READY
            or (item.authorization_expires_at is not None and item.authorization_expires_at <= now)
            for item in context.connections
        ):
            raise ValueError("one or more credential connections are not ready")
        roles = frozenset(role for item in context.connections for role in item.roles)
        required = required_connection_roles()
        if not required.issubset(roles):
            names = ", ".join(sorted(item.value for item in required))
            raise ValueError(f"{names} connections are required")
        if context.browser_playbook is not None:
            if (
                context.provider.interface is not ConnectionInterface.BROWSER
                or context.browser_playbook.state is not PlaybookState.PUBLISHED
            ):
                raise ValueError("browser execution requires the connection's published playbook")
        elif context.provider.interface is not ConnectionInterface.API:
            raise ValueError("provider execution requires an API or browser connection")
        if {item.service_id for item in context.bindings} != set(credential.consumer_ids):
            raise ValueError("consumer bindings do not cover credential inventory")
        await self._require_probes(run)
        lock = await self._catalog.client.document(
            FirestorePaths.lock(run.organisation_id, credential.id)
        ).get()
        if not lock.exists or (lock.to_dict() or {}).get("run_id") != run.id:
            raise ValueError("credential rotation lock changed")
        control_version, _ = await self._control(run)
        if control_version.definition.require_revoke_approval and not await self._approvers_known(
            run.organisation_id
        ):
            raise ValueError("organisation has no active approver")
        task = AgentTask(
            id=_id("task", run.id, "preflight"),
            organisation_id=run.organisation_id,
            run_id=run.id,
            agent=AgentKind.INVENTORY,
            skill="detect_stale_mapping",
            objective=(
                "Use detect_stale_mapping for "
                f"{credential.id} and confirm every observed credential consumer is represented "
                "in inventory."
            ),
            context={
                "run": run.model_dump(mode="json"),
                "inventory_item": credential.model_dump(mode="json"),
                "bindings": tuple(item.model_dump(mode="json") for item in context.bindings),
                "services": (),
            },
            requested_at=self._clock(),
        )
        agent = await self._agents.execute(task)
        if not agent.succeeded:
            detail = agent.error or "unknown managed-agent failure"
            raise ValueError(f"inventory agent failed: {detail}")
        missing_inventory = agent.output.get("missing_inventory")
        if missing_inventory:
            missing = (
                ", ".join(item for item in missing_inventory if isinstance(item, str))
                if isinstance(missing_inventory, list)
                else "invalid structured result"
            )
            raise ValueError(f"inventory agent found unresolved consumer mappings: {missing}")
        output = {
            "credential_id": credential.id,
            "browser_playbook_version": (
                context.browser_playbook.id if context.browser_playbook is not None else None
            ),
            "connections": [item.id for item in context.connections],
            "bindings": [item.id for item in context.bindings],
            "agent": agent.output,
        }
        checks = {
            "provider-ready",
            "credential-known",
            "scopes-known",
            "playbook-eligible",
            "management-authenticated",
            "store-ready",
            "consumers-known",
            "runtime-ready",
            "verifier-ready",
            "overlap-supported",
            "mutation-declared",
            "no-conflict",
        }
        if control_version.definition.require_revoke_approval:
            checks.add("approvers-known")
        return (
            frozenset(checks),
            agent.evidence_ids,
            StageBindings(
                browser_playbook_version=(
                    context.browser_playbook.id if context.browser_playbook is not None else None
                ),
                current_generation_id=credential.active_generation_id,
            ),
            output,
        )

    async def _plan(
        self, run: RotationRun
    ) -> tuple[frozenset[str], tuple[str, ...], StageBindings, dict[str, Any]]:
        context = await self._rotation_context(run)
        credential = context.credential
        control_version, _ = await self._control(run)
        current_generation = await self._optional_generation(
            run.organisation_id, run.current_generation_id
        )
        task = AgentTask(
            id=_id("task", run.id, "plan"),
            organisation_id=run.organisation_id,
            run_id=run.id,
            agent=AgentKind.PLANNER,
            skill="plan_rotation",
            objective="Select a rotation strategy from controls and confirmed inventory.",
            context={
                "run": run.model_dump(mode="json"),
                "inventory_item": credential.model_dump(mode="json"),
                "bindings": tuple(item.model_dump(mode="json") for item in context.bindings),
                "services": (),
                "provider_connection": context.provider.model_dump(mode="json"),
                "controls": control_version.model_dump(mode="json"),
                "published_playbook": (
                    context.browser_playbook.model_dump(mode="json")
                    if context.browser_playbook is not None
                    else None
                ),
                "current_generation": (
                    current_generation.model_dump(mode="json")
                    if current_generation is not None
                    else None
                ),
            },
            requested_at=self._clock(),
        )
        agent = await self._agents.execute(task)
        if not agent.succeeded:
            raise ValueError("rotation planner agent failed")
        if agent.output.get("decision") != "plan":
            raise ValueError("rotation planner did not return a plan decision")
        strategy_value = agent.output.get("strategy")
        if not isinstance(strategy_value, str):
            raise ValueError("rotation planner returned no valid strategy")
        try:
            strategy = RotationStrategy(strategy_value)
        except (TypeError, ValueError) as error:
            raise ValueError("rotation planner returned no valid strategy") from error
        if strategy is RotationStrategy.IMMEDIATE and len(credential.consumer_ids) > 1:
            raise ValueError("immediate rotation is invalid for multiple consumers")
        recovery_ids: dict[Stage, str] = {}
        recoveries: list[RecoveryPlan] = []
        if not control_version.definition.recovery:
            raise ValueError("rotation controls has no recovery branches")
        plan_key = (
            context.browser_playbook.id
            if context.browser_playbook is not None
            else context.provider.id
        )
        for failed_stage, branch in control_version.definition.recovery.items():
            recovery = RecoveryPlan(
                id=_id("recovery", run.id, plan_key, failed_stage.value),
                organisation_id=run.organisation_id,
                run_id=run.id,
                failed_stage=failed_stage,
                mode=branch.mode,
                steps=branch.actions,
                preserves_old_generation=branch.preserves_old_generation,
                requires_approval=any(item.protected for item in branch.actions),
            )
            recovery_ids[failed_stage] = recovery.id
            recoveries.append(recovery)
        plan = RotationPlan(
            id=_id("plan", run.id, plan_key),
            organisation_id=run.organisation_id,
            run_id=run.id,
            credential_id=credential.id,
            control_version=run.control_version,
            browser_playbook_version=(
                context.browser_playbook.id if context.browser_playbook is not None else None
            ),
            strategy=strategy,
            target_scopes=credential.scopes,
            consumer_ids=credential.consumer_ids,
            observation_seconds=_integer(agent.output.get("observation_seconds"), 300),
            recovery_ids=recovery_ids,
        )
        if plan.observation_seconds > control_version.definition.maximum_observation_seconds:
            raise ValueError("planned observation exceeds the bound controls maximum")
        if control_version.definition.preserve_old_generation and any(
            not item.preserves_old_generation
            for item in recoveries
            if item.failed_stage is not Stage.REVOKE
        ):
            raise ValueError("recovery plan violates old-generation preservation controls")
        for recovery in recoveries:
            await self._create_once(
                FirestorePaths.recovery(run.organisation_id, recovery.id), recovery
            )
        await self._create_once(FirestorePaths.plan(run.organisation_id, plan.id), plan)
        checksum = digest(plan)
        return (
            frozenset({"plan-bound", "controls-pinned", "plan-hashed", "recovery-ready"}),
            agent.evidence_ids,
            StageBindings(plan_id=plan.id, plan_hash=checksum),
            {
                "plan": plan.model_dump(mode="json"),
                "plan_hash": checksum,
                "agent": {
                    "decision": "plan",
                    "strategy": strategy.value,
                    "rationale": agent.output.get("rationale"),
                },
            },
        )

    async def _create(
        self, run: RotationRun
    ) -> tuple[frozenset[str], tuple[str, ...], StageBindings, dict[str, Any]]:
        context = await self._rotation_context(run)
        credential = context.credential
        if context.browser_playbook is not None:
            outputs, evidence = await self._execute_browser_steps(run, context, Stage.CREATE)
        else:
            result, evidence = await self._execute_operation(
                run,
                OperationStep(
                    id="provider_create",
                    stage=Stage.CREATE,
                    tool="provider.createCredential",
                    operation="create",
                    objective="Create the replacement credential through the typed provider API.",
                    parameters={
                        "connection_id": context.provider.id,
                        "name": credential.display_name,
                        "scopes": tuple(sorted(credential.scopes)),
                        "sink_connection_id": context.secret_store.id,
                        "secret_resource": credential.secret_resource,
                    },
                    evidence_checks=frozenset({"provider-created", "secret-transferred"}),
                ),
            )
            outputs = [result]
        flattened = _flatten(outputs)
        provider_id = _find_string(flattened, "provider_id")
        provider_display_name = _find_optional(flattened, "provider_display_name")
        if provider_display_name is not None and not isinstance(provider_display_name, str):
            raise ValueError("stage output has invalid provider_display_name")
        secret_reference = _find_string(flattened, "secret_reference")
        fingerprint = _find_string(flattened, "fingerprint")
        generation_id = _id("generation", run.id, provider_id)
        generation = CredentialGeneration(
            id=generation_id,
            organisation_id=run.organisation_id,
            credential_id=credential.id,
            provider_id=provider_id,
            provider_display_name=provider_display_name,
            fingerprint=fingerprint,
            scopes=credential.scopes,
            state=GenerationState.CREATING,
            attempt_id=_id("attempt", run.id, "create"),
            secret_reference=secret_reference,
            predecessor_id=run.current_generation_id,
            created_at=self._clock(),
        )
        await self._generations.create(generation)
        return (
            frozenset({"replacement-created", "mutation-resolved", "generation-recorded"}),
            evidence,
            StageBindings(target_generation_id=generation.id),
            {
                "generation": generation.model_dump(mode="json"),
                "browser_activity": _safe_browser_activity(outputs),
            },
        )

    async def _store(
        self, run: RotationRun
    ) -> tuple[frozenset[str], tuple[str, ...], StageBindings, dict[str, Any]]:
        context = await self._rotation_context(run)
        credential = context.credential
        target = await self._target(run)
        result, evidence = await self._execute_operation(
            run,
            OperationStep(
                id="secret_store_verify",
                stage=Stage.STORE,
                tool="secretStore.getVersion",
                operation="inspect",
                objective="Verify the replacement secret version metadata.",
                parameters={
                    "connection_id": context.secret_store.id,
                    "version": _required(target.secret_reference, "target secret reference"),
                },
                evidence_checks=frozenset({"secret-version-enabled"}),
            ),
        )
        outputs = [result]
        states = {value.get("state") for value in _flatten(outputs) if isinstance(value, dict)}
        if states and "ENABLED" not in states:
            raise ValueError("stored secret version is not enabled")
        if _contains_secret_value(outputs):
            raise ValueError("secret value escaped into stage output")
        bindings = await self._bindings(run, credential)
        consumer_access: list[dict[str, Any]] = []
        for binding in bindings:
            runtime, runtime_evidence = await self._execute_operation(
                run,
                OperationStep(
                    id=f"store_identity_{binding.id}",
                    stage=Stage.STORE,
                    tool="runtime.inspectSecretBindings",
                    operation="inspect",
                    objective="Resolve the declared consumer workload identity.",
                    parameters={
                        "connection_id": binding.runtime_connection_id,
                        "service": binding.runtime_resource,
                    },
                    evidence_checks=frozenset({"consumer-identity-resolved"}),
                ),
            )
            identity = _find_string([runtime], "service_account")
            access, access_evidence = await self._execute_operation(
                run,
                OperationStep(
                    id=f"store_access_{binding.id}",
                    stage=Stage.STORE,
                    tool="secretStore.testConsumerAccess",
                    operation="test-access",
                    objective="Prove the consumer identity can read the replacement version.",
                    parameters={
                        "connection_id": context.secret_store.id,
                        "version": _required(target.secret_reference, "target secret reference"),
                        "consumer_identity": identity,
                    },
                    evidence_checks=frozenset({"consumer-accessible"}),
                ),
            )
            if access.get("accessible") is not True:
                raise ValueError("consumer identity cannot access the replacement secret")
            consumer_access.append({"binding_id": binding.id, "consumer_identity": identity})
            evidence = tuple(dict.fromkeys((*evidence, *runtime_evidence, *access_evidence)))
        await self._generations.stage_bindings(
            run.organisation_id,
            credential.id,
            target.id,
            _required(target.secret_reference, "target secret reference"),
            tuple(item.id for item in bindings),
        )
        return (
            frozenset({"secret-stored", "consumer-accessible", "plaintext-isolated"}),
            evidence,
            StageBindings(),
            {
                "secret_reference": target.secret_reference,
                "secret_store": context.secret_store.display_name,
                "bindings": [item.id for item in bindings],
                "consumer_access": consumer_access,
            },
        )

    async def _deploy(
        self, run: RotationRun
    ) -> tuple[frozenset[str], tuple[str, ...], StageBindings, dict[str, Any]]:
        context = await self._rotation_context(run)
        target = await self._target(run)
        secret_name, secret_version = _secret_parts(
            _required(target.secret_reference, "target secret reference")
        )
        outputs: list[dict[str, Any]] = []
        evidence: list[str] = []
        deployments: list[RuntimeDeployment] = []
        for binding in context.bindings:
            result, ids = await self._execute_operation(
                run,
                OperationStep(
                    id=f"deploy_{binding.id}",
                    stage=Stage.DEPLOY,
                    tool="runtime.deployCandidate",
                    operation="deploy-candidate",
                    objective="Deploy a zero-traffic consumer candidate using the replacement.",
                    parameters={
                        "connection_id": binding.runtime_connection_id,
                        "service": binding.runtime_resource,
                        "secret_env": binding.runtime_secret_name,
                        "secret_name": secret_name,
                        "secret_version": secret_version,
                        "generation_id": _required(run.target_generation_id, "target generation"),
                        "tag": f"uumi-{run.id[-12:]}",
                        **(
                            {"container_name": binding.runtime_container_name}
                            if binding.runtime_container_name is not None
                            else {}
                        ),
                    },
                    evidence_checks=frozenset({"candidate-deployed", "rollback-pinned"}),
                ),
            )
            candidate = _find_string([result], "candidate_revision")
            rollback = _find_string([result], "rollback_revision")
            if _find_optional([result], "generation_id") != run.target_generation_id:
                raise ValueError("runtime candidate does not carry the target generation")
            deployments.append(
                RuntimeDeployment(
                    binding_id=binding.id,
                    connection_id=binding.runtime_connection_id,
                    service=binding.runtime_resource,
                    candidate_revision=candidate,
                    rollback_revision=rollback,
                )
            )
            outputs.append(result)
            evidence.extend(ids)
        return (
            frozenset(
                {"candidate-deployed", "version-bound", "generation-tagged", "rollback-ready"}
            ),
            tuple(dict.fromkeys(evidence)),
            StageBindings(deployments=tuple(deployments)),
            {"steps": outputs},
        )

    async def _verify(
        self, run: RotationRun
    ) -> tuple[frozenset[str], tuple[str, ...], StageBindings, dict[str, Any]]:
        report = await self._run_verification(run, negative=False)
        if report.status is not VerificationStatus.PASSED:
            raise ValueError("deterministic verification failed")
        credential = await self._credential(run)
        bindings = await self._bindings(run, credential)
        await self._generations.verify_bindings(
            run.organisation_id,
            _required(run.target_generation_id, "target generation"),
            report.id,
            tuple(item.id for item in bindings),
        )
        checks = await self._verification_checks(run, report, Stage.VERIFY)
        return (
            checks,
            report.evidence_ids,
            StageBindings(),
            {"report": report.model_dump(mode="json")},
        )

    async def _rollout(
        self, run: RotationRun
    ) -> tuple[frozenset[str], tuple[str, ...], StageBindings, dict[str, Any]]:
        credential = await self._credential(run)
        if not run.deployments:
            raise ValueError("runtime deployment identities are missing")
        plan = await self._catalog.get(
            FirestorePaths.plan(run.organisation_id, _required(run.plan_id, "rotation plan")),
            RotationPlan,
        )
        if digest(plan) != run.plan_hash:
            raise ValueError("rotation plan digest changed")
        outputs: list[dict[str, Any]] = []
        evidence: list[str] = []
        report: VerificationReport | None = None
        for percent in plan.rollout:
            for deployment in run.deployments:
                result, ids = await self._execute_operation(
                    run,
                    OperationStep(
                        id=f"rollout_{deployment.binding_id}_{percent}",
                        stage=Stage.ROLLOUT,
                        tool="runtime.shiftTraffic",
                        operation="shift-traffic",
                        objective="Promote the verified replacement candidate under controls.",
                        parameters={
                            "connection_id": deployment.connection_id,
                            "service": deployment.service,
                            "candidate_revision": deployment.candidate_revision,
                            "rollback_revision": deployment.rollback_revision,
                            "percent": percent,
                        },
                        evidence_checks=frozenset({"traffic-shifted"}),
                    ),
                )
                outputs.append(result)
                evidence.extend(ids)
            report = await self._run_verification(
                run,
                negative=False,
                probe_stage=Stage.VERIFY,
                report_suffix=f"rollout-{percent}",
            )
            if report.status is not VerificationStatus.PASSED:
                raise ValueError(f"rollout verification failed at {percent} percent")
            await self._verification_checks(run, report, Stage.VERIFY)
            evidence.extend(report.evidence_ids)
        if report is None:
            raise ValueError("rotation plan has no rollout percentages")
        bindings = await self._bindings(run, credential)
        await self._generations.promote(
            run.organisation_id,
            credential.id,
            _required(run.target_generation_id, "target generation"),
            _required(run.current_generation_id, "current generation"),
            report.id,
            tuple(item.id for item in bindings),
        )
        return (
            frozenset({"production-promoted", "rollout-healthy"}),
            tuple(dict.fromkeys((*evidence, *report.evidence_ids))),
            StageBindings(),
            {
                "steps": outputs,
                "milestones": list(plan.rollout),
                "active_generation": run.target_generation_id,
                "observation_seconds": plan.observation_seconds,
            },
        )

    async def _observe(
        self, run: RotationRun
    ) -> tuple[frozenset[str], tuple[str, ...], StageBindings, dict[str, Any]]:
        plan = await self._catalog.get(
            FirestorePaths.plan(run.organisation_id, _required(run.plan_id, "rotation plan")),
            RotationPlan,
        )
        report = await self._run_verification(run, negative=False, observation=True)
        if report.status is not VerificationStatus.PASSED:
            raise ValueError("observation probes failed")
        checks = await self._verification_checks(run, report, Stage.OBSERVE)
        return (
            checks,
            report.evidence_ids,
            StageBindings(),
            {
                "report": report.model_dump(mode="json"),
                "observation_seconds": plan.observation_seconds,
            },
        )

    async def _approval(
        self, run: RotationRun
    ) -> tuple[frozenset[str], tuple[str, ...], StageBindings, dict[str, Any]]:
        control_version, _ = await self._control(run)
        if not control_version.definition.require_revoke_approval:
            return (
                frozenset({"approval-not-required", "evidence-current"}),
                (),
                StageBindings(),
                {"approval_required": False},
            )
        approvals = []
        rotation = await self._rotation_context(run)
        step_context = await self._step_context(run)
        steps = list(await self._provider_steps(run, rotation, Stage.REVOKE))
        old = await self._catalog.get(
            FirestorePaths.generation(
                run.organisation_id, _required(run.current_generation_id, "old generation")
            ),
            CredentialGeneration,
        )
        if old.secret_reference is not None:
            steps.append(
                OperationStep(
                    id="secret_store_disable_old",
                    stage=Stage.REVOKE,
                    tool="secretStore.disableVersion",
                    operation="disable",
                    objective="Disable the superseded secret-store version.",
                    parameters={
                        "connection_id": rotation.secret_store.id,
                        "version": old.secret_reference,
                    },
                    evidence_checks=frozenset({"old-secret-disabled"}),
                )
            )
        for step in steps:
            if step.tool not in control_version.definition.protected_tools:
                continue
            protected = step.model_copy(update={"protected": True})
            payload = _resolve(step.parameters, step_context)
            if not isinstance(payload, dict):
                raise ValueError(f"protected operation {step.id} parameters are invalid")
            connection_id = payload.pop("connection_id", None)
            if not isinstance(connection_id, str):
                raise ValueError(f"protected operation {step.id} has no connection_id")
            payload.pop("approval_id", None)
            approvals.append(
                await self._approval_for_step(
                    run,
                    protected,
                    payload,
                    connection_id,
                    allow_resume_fence=True,
                )
            )
        if not approvals:
            raise ValueError("revocation controls declare no protected approval action")
        return (
            frozenset({"approval-valid", "action-digest-valid", "evidence-current"}),
            (),
            StageBindings(),
            {
                "approvals": [
                    {"approval_id": item.id, "action_id": item.action_id} for item in approvals
                ]
            },
        )

    async def _revoke(
        self, run: RotationRun
    ) -> tuple[frozenset[str], tuple[str, ...], StageBindings, dict[str, Any]]:
        context = await self._rotation_context(run)
        old = await self._catalog.get(
            FirestorePaths.generation(
                run.organisation_id, _required(run.current_generation_id, "old generation")
            ),
            CredentialGeneration,
        )
        provider_revocation_required = _requires_provider_revocation(
            context.browser_playbook is not None, old.provider_id
        )
        if provider_revocation_required:
            outputs, evidence = await self._execute_provider_steps(run, context, Stage.REVOKE)
        else:
            outputs = [
                {
                    "outcome": "Provider revocation not applicable",
                    "reason": "bootstrap generation has no provider credential",
                }
            ]
            evidence = ()
        replacement_report = await self._run_verification(
            run,
            negative=False,
            probe_stage=Stage.VERIFY,
            report_suffix="replacement",
        )
        if replacement_report.status is not VerificationStatus.PASSED:
            raise ValueError("replacement credential failed verification before revocation")
        rejection_report = await self._run_verification(
            run,
            negative=True,
            probe_stage=Stage.REVOKE,
            report_suffix="old-rejected",
        )
        if rejection_report.status is not VerificationStatus.PASSED:
            raise ValueError("old credential still works after provider revocation")
        browser_revoked = context.browser_playbook is not None and any(
            output.get("outcome") == "Step completed"
            for output in outputs
            if isinstance(output, dict)
        )
        if (
            context.browser_playbook is not None
            and provider_revocation_required
            and not browser_revoked
        ):
            raise ValueError("browser playbook did not prove credential revocation")
        if old.secret_reference is not None:
            result, ids = await self._execute_operation(
                run,
                OperationStep(
                    id="secret_store_disable_old",
                    stage=Stage.REVOKE,
                    tool="secretStore.disableVersion",
                    operation="disable",
                    objective="Disable the superseded secret-store version.",
                    parameters={
                        "connection_id": context.secret_store.id,
                        "version": old.secret_reference,
                    },
                    evidence_checks=frozenset({"old-secret-disabled"}),
                ),
            )
            if result.get("state") != "DISABLED":
                raise ValueError("old secret version was not disabled")
            outputs.append(result)
            evidence = tuple(dict.fromkeys((*evidence, *ids)))
        await self._generations.revoke(
            run.organisation_id,
            _required(run.current_generation_id, "old generation"),
            rejection_report.id,
        )
        await self._incidents.advance_run(run.organisation_id, run.id, IncidentStatus.CONTAINED)
        await self._verification_checks(run, replacement_report, Stage.VERIFY)
        checks = _revocation_checks(
            replacement_report,
            rejection_report,
            old.secret_reference,
            browser_managed=context.browser_playbook is not None,
        )
        return (
            checks,
            tuple(
                dict.fromkeys(
                    (
                        *evidence,
                        *replacement_report.evidence_ids,
                        *rejection_report.evidence_ids,
                    )
                )
            ),
            StageBindings(),
            {
                "steps": outputs,
                "browser_activity": _safe_browser_activity(outputs),
                "replacement_report": replacement_report.model_dump(mode="json"),
                "rejection_report": rejection_report.model_dump(mode="json"),
            },
        )

    async def _complete(
        self, run: RotationRun
    ) -> tuple[frozenset[str], tuple[str, ...], StageBindings, dict[str, Any]]:
        credential = await self._credential(run)
        if credential.active_generation_id != run.target_generation_id:
            raise ValueError("credential inventory does not identify the replacement")
        old = await self._catalog.get(
            FirestorePaths.generation(
                run.organisation_id, _required(run.current_generation_id, "old generation")
            ),
            CredentialGeneration,
        )
        if old.state is not GenerationState.REVOKED:
            raise ValueError("old generation is not revoked")
        audit_evidence = await self._verify_audit(run)
        await self._browser.terminate(run)
        replacement_report = await self._latest_report(run, run.target_generation_id, Stage.VERIFY)
        rejection_report = await self._latest_report(run, run.current_generation_id, Stage.REVOKE)
        await self._verification_checks(run, replacement_report, Stage.VERIFY)
        browser_managed = (
            await self._rotation_context(run)
        ).provider.interface is ConnectionInterface.BROWSER
        if not browser_managed and "credential-rejected" not in rejection_report.checks:
            raise ValueError("completion has no proof that the old credential is rejected")
        await self._incidents.advance_run(run.organisation_id, run.id, IncidentStatus.RESOLVED)
        return (
            frozenset({"consumers-current", "replacement-valid", "old-rejected", "audit-complete"}),
            tuple(
                dict.fromkeys(
                    (
                        *replacement_report.evidence_ids,
                        *rejection_report.evidence_ids,
                        audit_evidence,
                    )
                )
            ),
            StageBindings(),
            {"active_generation": credential.active_generation_id, "audit_valid": True},
        )

    async def _provider_steps(
        self,
        run: RotationRun,
        context: RotationContext,
        stage: Stage,
    ) -> tuple[OperationStep, ...]:
        control_version, _ = await self._control(run)
        if context.browser_playbook is not None:
            steps = tuple(
                item for item in context.browser_playbook.definition.steps if item.stage is stage
            )
            return tuple(
                item.model_copy(
                    update={"protected": item.tool in control_version.definition.protected_tools}
                )
                for item in steps
            )
        if stage is Stage.REVOKE:
            return (
                OperationStep(
                    id="provider_revoke",
                    stage=Stage.REVOKE,
                    tool="provider.revokeCredential",
                    operation="revoke",
                    objective="Revoke the superseded credential through the typed provider API.",
                    parameters={
                        "connection_id": context.provider.id,
                        "provider_id": "${old_provider_id}",
                    },
                    protected=(
                        "provider.revokeCredential" in control_version.definition.protected_tools
                    ),
                    evidence_checks=frozenset({"provider-revoked"}),
                ),
            )
        return ()

    async def _execute_provider_steps(
        self,
        run: RotationRun,
        context: RotationContext,
        stage: Stage,
    ) -> tuple[list[dict[str, Any]], tuple[str, ...]]:
        steps = await self._provider_steps(run, context, stage)
        if not steps:
            raise ValueError(f"provider execution has no {stage.value} steps")
        outputs: list[dict[str, Any]] = []
        evidence: list[str] = []
        control_version, _ = await self._control(run)
        for step in steps:
            if isinstance(step, PlaybookStep):
                step_context = await self._step_context(run)
                resolved = resolve_playbook_step(step, step_context)
                payload = resolved.parameters
                try:
                    approval = (
                        await self._approval_for_step(
                            run,
                            resolved,
                            payload,
                            context.provider.id,
                            allow_previous_fence=stage is Stage.CREATE,
                        )
                        if resolved.protected
                        else None
                    )
                except BrowserPauseError:
                    await self._browser.pause_for_approval(run)
                    raise
                decision = (
                    None
                    if is_deterministic_browser_step(resolved)
                    else await self._operator_decision(run, resolved, context, control_version)
                )
                browser_output = await self._browser.execute(
                    run,
                    context.provider,
                    _required_playbook(context.browser_playbook),
                    context.credential,
                    control_version.definition.protected_tools,
                    resolved,
                    approval,
                )
                output = dict(browser_output)
                if decision is not None:
                    output["operator"] = decision.model_dump(mode="json")
                outputs.append(output)
            else:
                result, ids = await self._execute_operation(run, step)
                outputs.append(result)
                evidence.extend(ids)
        return outputs, tuple(dict.fromkeys(evidence))

    async def _execute_browser_steps(
        self,
        run: RotationRun,
        context: RotationContext,
        stage: Stage,
    ) -> tuple[list[dict[str, Any]], tuple[str, ...]]:
        return await self._execute_provider_steps(run, context, stage)

    async def _execute_operation(
        self,
        run: RotationRun,
        step: OperationStep,
    ) -> tuple[dict[str, Any], tuple[str, ...]]:
        control_version, _ = await self._control(run)
        if step.tool not in control_version.definition.allowed_tools:
            raise ValueError(f"operation {step.tool} is forbidden by controls")
        step_context = await self._step_context(run)
        payload = _resolve(step.parameters, step_context)
        if not isinstance(payload, dict):
            raise ValueError(f"operation {step.id} parameters are invalid")
        connection_id = payload.pop("connection_id", None)
        if not isinstance(connection_id, str):
            raise ValueError(f"operation {step.id} has no connection_id")
        protected = step.tool in control_version.definition.protected_tools
        effective = step.model_copy(update={"protected": protected})
        approval = (
            await self._approval_for_step(run, effective, payload, connection_id)
            if protected
            else None
        )
        result = await self._broker.execute(
            run,
            _id("tool", run.id, step.id, str(run.fencing_token)),
            connection_id,
            step.tool,
            payload,
            approval.id if approval is not None else None,
        )
        if not result.succeeded:
            raise StageExecutionError(
                f"operation {step.tool} failed: {result.error_code}",
                retryable=result.result.get("retryable") is True,
            )
        return result.result, result.evidence_ids

    async def _operator_decision(
        self,
        run: RotationRun,
        step: PlaybookStep,
        context: RotationContext,
        control_version: ControlVersion,
    ) -> OperatorDecision:
        result = await self._agents.execute(
            AgentTask(
                id=_operator_task_id(run.id, step.id, run.fencing_token),
                organisation_id=run.organisation_id,
                run_id=run.id,
                agent=AgentKind.OPERATOR,
                skill="execute_console_playbook",
                objective=_operator_objective(step.id),
                context={
                    "run": run.model_dump(mode="json"),
                    "inventory_item": context.credential.model_dump(mode="json"),
                    "provider_connection": context.provider.model_dump(mode="json"),
                    "controls": control_version.model_dump(mode="json"),
                    "published_playbook": (
                        context.browser_playbook.model_dump(mode="json")
                        if context.browser_playbook is not None
                        else None
                    ),
                    "step_id": step.id,
                    "stage": step.stage.value,
                },
                requested_at=self._clock(),
            )
        )
        if not result.succeeded:
            raise BrowserPauseError(
                "Console Operator Agent could not authorise the browser step",
                {"run_id": run.id, "step_id": step.id, "agent_error": result.error},
            )
        try:
            decision = OperatorDecision.model_validate(result.output)
        except ValueError as error:
            raise BrowserPauseError(
                "Console Operator Agent returned an invalid decision",
                {"run_id": run.id, "step_id": step.id},
            ) from error
        if decision.step_id != step.id:
            raise BrowserPauseError(
                "Console Operator Agent changed the immutable step binding",
                {"run_id": run.id, "step_id": step.id},
            )
        decision = decision.model_copy(
            update={
                "expected_checkpoint": (
                    f"checkpoint:{digest(step.checkpoint)}"
                    if step.checkpoint is not None
                    else "checkpoint:none"
                )
            }
        )
        if not decision.ready or decision.drift_detected:
            raise BrowserPauseError(
                decision.pause_reason or "Console Operator Agent detected interface drift",
                {
                    "run_id": run.id,
                    "step_id": step.id,
                    "operator": decision.model_dump(mode="json"),
                },
            )
        return decision

    async def _step_context(self, run: RotationRun) -> dict[str, Any]:
        target = await self._optional_target(run)
        old = await self._optional_generation(run.organisation_id, run.current_generation_id)
        credential = await self._credential(run)
        return browser_step_context(run, credential, old, target)

    async def _approval_for_step(
        self,
        run: RotationRun,
        step: OperationStep,
        payload: dict[str, Any],
        connection_id: str,
        *,
        allow_previous_fence: bool = False,
        allow_resume_fence: bool = False,
    ) -> Approval:
        if run.plan_id is None or run.plan_hash is None:
            raise ValueError("protected action has no immutable rotation plan")
        plan_hash = run.plan_hash
        credential = await self._credential(run)
        generation_id = _required(run.current_generation_id, "current generation")
        generation = await self._catalog.get(
            FirestorePaths.generation(run.organisation_id, generation_id), CredentialGeneration
        )
        evidence_hash = await self._approval_evidence_hash(run, step.stage)
        parameters = _approval_parameters(payload)
        resource = next(
            (
                value
                for key, value in parameters.items()
                if key in {"provider_id", "secret_resource", "version", "resource", "service"}
                and isinstance(value, str)
            ),
            step.operation,
        )

        def action_for(fencing_token: int) -> ProtectedAction:
            return ProtectedAction(
                id=_id(
                    "action",
                    run.id,
                    step.id,
                    str(fencing_token),
                    _json_digest(parameters),
                ),
                organisation_id=run.organisation_id,
                run_id=run.id,
                kind=step.tool,
                resource=resource,
                credential_id=credential.id,
                generation_id=generation_id,
                provider_id=_required(
                    generation.provider_id or credential.provider_id,
                    "protected action provider ID",
                ),
                control_version=run.control_version,
                playbook_version=run.browser_playbook_version,
                plan_hash=plan_hash,
                evidence_hash=evidence_hash,
                preconditions={
                    "stage": step.stage.value,
                    "fencing_token": fencing_token,
                    "connection_id": connection_id,
                    "current_generation_id": run.current_generation_id,
                    "target_generation_id": run.target_generation_id,
                },
                parameters={"step_id": step.id, **parameters},
            )

        approvals = await self._query(run.organisation_id, "approvals", "run_id", run.id, Approval)
        candidates = [action_for(run.fencing_token)]
        if allow_previous_fence and run.fencing_token > 1:
            candidates.append(action_for(run.fencing_token - 1))
        requested_action = action_for(run.fencing_token)
        if allow_resume_fence:
            requested_action = action_for(run.fencing_token + 1)
        action = requested_action
        matching: list[Approval] = []
        for candidate in candidates:
            current = []
            for approval in approvals:
                if approval.action_id != candidate.id:
                    continue
                stored = await self._catalog.get(
                    FirestorePaths.action(run.organisation_id, approval.action_id),
                    ProtectedAction,
                )
                if stored != candidate:
                    raise ValueError("protected action changed after approval request")
                current.append(approval)
            if current:
                action = candidate
                matching = current
                break
        approval_request = {
            "action": action.model_dump(mode="json"),
            "plan_hash": plan_hash,
            "evidence_hash": evidence_hash,
        }
        if not matching:
            raise BrowserPauseError(
                "protected action approval is pending",
                {
                    "run_id": run.id,
                    "step_id": step.id,
                    "approval_request": approval_request,
                },
            )
        if len(matching) != 1:
            raise ValueError("protected action has multiple approval records")
        approval = matching[0]
        expected = (digest(action), plan_hash, evidence_hash, generation_id)
        actual = (
            approval.action_digest,
            approval.plan_hash,
            approval.evidence_hash,
            approval.generation_id,
        )
        if not all(
            hmac.compare_digest(left, right) for left, right in zip(actual, expected, strict=True)
        ):
            raise ValueError("protected action approval bindings changed")
        if approval.decision is ApprovalDecision.REJECTED:
            raise ValueError("protected action approval was rejected")
        if approval.decision is not ApprovalDecision.APPROVED or approval.consumed_at is None:
            raise BrowserPauseError(
                "protected action approval must be granted and consumed",
                {
                    "run_id": run.id,
                    "step_id": step.id,
                    "approval_id": approval.id,
                    "approval_request": approval_request,
                },
            )
        if approval.expires_at <= self._clock():
            raise ValueError("protected action approval expired")
        return approval

    async def _approval_evidence_hash(self, run: RotationRun, stage: Stage) -> str:
        if stage is Stage.REVOKE:
            report = await self._latest_report(run, run.target_generation_id)
            return digest(report)
        plan = await self._catalog.get(
            FirestorePaths.plan(run.organisation_id, _required(run.plan_id, "rotation plan")),
            RotationPlan,
        )
        if digest(plan) != run.plan_hash:
            raise ValueError("rotation plan digest changed")
        return digest(plan)

    async def _run_verification(
        self,
        run: RotationRun,
        negative: bool,
        observation: bool = False,
        probe_stage: Stage | None = None,
        report_suffix: str | None = None,
    ) -> VerificationReport:
        credential = await self._credential(run)
        selected_stage = probe_stage or run.stage
        definitions = await self._probes(run, negative, observation, selected_stage)
        connection = await self._catalog.get(
            FirestorePaths.connection(run.organisation_id, credential.connection_id), Connection
        )
        verification_scope = report_suffix or selected_stage.value
        context = ConnectorContext(
            request_id=_id("verify", run.id, run.stage.value, verification_scope),
            agent_id="coordinator_one",
            connection=connection,
            run=run,
            now=self._clock(),
            idempotency_key=_id("verify", run.id, run.stage.value, verification_scope),
        )
        generation_id = run.current_generation_id if negative else run.target_generation_id
        return await self._verifier.verify(
            _id(
                "report",
                run.id,
                run.stage.value,
                verification_scope,
                str(run.revision),
            ),
            run.organisation_id,
            run.id,
            _required(generation_id, "verification generation"),
            definitions,
            context,
        )

    async def _probes(
        self,
        run: RotationRun,
        negative: bool,
        observation: bool,
        probe_stage: Stage,
    ) -> tuple[ProbeDefinition, ...]:
        control_version, _ = await self._control(run)
        ids = list(control_version.definition.probe_versions.get(probe_stage, ()))
        if not ids:
            raise ValueError(f"controls declare no deterministic probes for {probe_stage.value}")
        resolved = []
        for item in ids:
            probe_version = await self._catalog.get(
                FirestorePaths.probe_version(run.organisation_id, item), ProbeVersion
            )
            if probe_version.state not in {
                ProbeState.ACTIVE,
                ProbeState.SUPERSEDED,
            } or probe_version.digest != digest(probe_version.definition):
                raise ValueError(f"probe version {item} is not immutable and authorised")
            resolved.append(await self._bind_probe(run, probe_version.definition, negative))
        values = tuple(resolved)
        if probe_stage is Stage.VERIFY:
            bound_kinds = {
                ProbeKind.PROVIDER,
                ProbeKind.SECRET,
                ProbeKind.RUNTIME,
                ProbeKind.HTTP,
                ProbeKind.EMAIL,
                ProbeKind.TELEMETRY,
                ProbeKind.GENERATION,
                ProbeKind.CREDENTIAL,
            }
            unbound = [
                item.id
                for item in values
                if item.kind in bound_kinds and item.expected_generation_id is None
            ]
            if unbound:
                raise ValueError(
                    "verification probes are not generation-bound: " + ", ".join(unbound)
                )
        browser_managed = not any(
            item.kind in {ProbeKind.PROVIDER, ProbeKind.CREDENTIAL} for item in values
        )
        expected_negative = (
            set()
            if browser_managed
            else {ProbeKind.PROVIDER, ProbeKind.CREDENTIAL}
            if negative
            else set()
        )
        if negative and not expected_negative.issubset(
            {item.kind for item in values if item.negative}
        ):
            raise ValueError("revocation requires negative provider and credential probes")
        if (
            observation
            and control_version.definition.require_generation_telemetry
            and (
                not any(item.kind is ProbeKind.TELEMETRY and not item.negative for item in values)
                or not any(item.kind is ProbeKind.TELEMETRY and item.negative for item in values)
            )
        ):
            raise ValueError("observation requires target-health and old-use telemetry probes")
        if probe_stage is Stage.VERIFY:
            required = {ProbeKind.SECRET, ProbeKind.RUNTIME, ProbeKind.TELEMETRY}
            if not browser_managed:
                required.update({ProbeKind.PROVIDER, ProbeKind.CREDENTIAL})
            if not control_version.definition.require_generation_telemetry:
                required.remove(ProbeKind.TELEMETRY)
            kinds = {item.kind for item in values}
            if not required.issubset(kinds):
                raise ValueError("verification probe coverage is incomplete")
        return values

    async def _require_probes(self, run: RotationRun) -> None:
        control_version, _ = await self._control(run)
        required_stages = {Stage.VERIFY, Stage.OBSERVE, Stage.REVOKE}
        missing = required_stages.difference(control_version.definition.probe_versions)
        if missing:
            names = ", ".join(sorted(stage.value for stage in missing))
            raise ValueError(f"controls have no deterministic probes for: {names}")
        ids = {
            probe_id
            for stage in required_stages
            for probe_id in control_version.definition.probe_versions.get(stage, ())
        }
        for probe_id in ids:
            probe_version = await self._catalog.get(
                FirestorePaths.probe_version(run.organisation_id, probe_id), ProbeVersion
            )
            if probe_version.state is not ProbeState.ACTIVE or probe_version.digest != digest(
                probe_version.definition
            ):
                raise ValueError(f"probe version {probe_id} is not active and immutable")

    async def _bind_probe(
        self,
        run: RotationRun,
        definition: ProbeDefinition,
        negative: bool,
    ) -> ProbeDefinition:
        from contracts import GenerationBinding, TargetBinding

        revocation_probe = definition.kind in {
            ProbeKind.PROVIDER,
            ProbeKind.CREDENTIAL,
        }
        expected_negative = negative and revocation_probe
        if revocation_probe and definition.negative != expected_negative:
            raise ValueError("probe polarity does not match the verification stage")
        generation_id = None
        if definition.generation_binding is GenerationBinding.TARGET:
            generation_id = _required(run.target_generation_id, "target generation")
        elif definition.generation_binding is GenerationBinding.CURRENT:
            generation_id = _required(run.current_generation_id, "current generation")
        if definition.expected_generation_id not in {None, generation_id}:
            raise ValueError("probe generation binding changed after activation")
        target = definition.target
        bound = None
        if (
            definition.target_binding is not TargetBinding.STATIC
            or definition.kind is ProbeKind.CREDENTIAL
        ):
            bound = await self._catalog.get(
                FirestorePaths.generation(
                    run.organisation_id, _required(generation_id, "probe generation")
                ),
                CredentialGeneration,
            )
            if definition.target_binding is TargetBinding.PROVIDER_ID:
                target = _required(bound.provider_id, "provider generation ID")
            elif definition.target_binding is TargetBinding.SECRET_REFERENCE:
                target = _required(bound.secret_reference, "generation secret reference")
        secret_reference = definition.secret_reference
        if definition.kind is ProbeKind.CREDENTIAL:
            assert bound is not None
            secret_reference = _required(
                bound.secret_reference, "credential probe secret reference"
            )
        return definition.model_copy(
            update={
                "target": target,
                "secret_reference": secret_reference,
                "expected_generation_id": generation_id,
            }
        )

    async def _credential(self, run: RotationRun) -> ManagedCredential:
        return await self._catalog.get(
            FirestorePaths.credential(run.organisation_id, run.credential_id),
            ManagedCredential,
        )

    async def _rotation_context(self, run: RotationRun) -> RotationContext:
        credential = await self._credential(run)
        provider = await self._catalog.get(
            FirestorePaths.connection(run.organisation_id, credential.connection_id), Connection
        )
        secret_store = await self._catalog.get(
            FirestorePaths.connection(run.organisation_id, credential.secret_store_connection_id),
            Connection,
        )
        if (
            ConnectionRole.PROVIDER not in provider.roles
            or provider.platform != credential.provider
        ):
            raise ValueError("credential provider connection changed")
        if (
            ConnectionRole.SECRET_STORE not in secret_store.roles
            or secret_store.interface is not ConnectionInterface.API
        ):
            raise ValueError("credential secret-store connection changed")
        bindings = await self._bindings(run, credential)
        runtimes: dict[str, Connection] = {}
        for binding in bindings:
            connection = await self._catalog.get(
                FirestorePaths.connection(run.organisation_id, binding.runtime_connection_id),
                Connection,
            )
            if (
                ConnectionRole.RUNTIME not in connection.roles
                or connection.interface is not ConnectionInterface.API
            ):
                raise ValueError("consumer runtime connection changed")
            runtimes[connection.id] = connection
        version = None
        if provider.interface is ConnectionInterface.BROWSER:
            version = await self._catalog.get(
                FirestorePaths.playbook_version(
                    run.organisation_id,
                    _required(provider.playbook_id, "browser connection playbook"),
                    _required(provider.playbook_version_id, "browser connection playbook version"),
                ),
                PlaybookVersion,
            )
            if (
                version.state is not PlaybookState.PUBLISHED
                or version.digest != digest(version.definition)
                or version.definition.platform != provider.platform
            ):
                raise ValueError("browser connection playbook is not published and immutable")
            if (
                run.browser_playbook_version is not None
                and run.browser_playbook_version != version.id
            ):
                raise ValueError("run browser playbook binding changed")
        elif run.browser_playbook_version is not None:
            raise ValueError("API rotation cannot acquire a browser playbook")
        return RotationContext(
            credential=credential,
            provider=provider,
            secret_store=secret_store,
            bindings=bindings,
            runtimes=runtimes,
            browser_playbook=version,
        )

    async def _bindings(
        self, run: RotationRun, credential: ManagedCredential
    ) -> tuple[ConsumerBinding, ...]:
        return await self._query(
            run.organisation_id,
            "bindings",
            "credential_id",
            credential.id,
            ConsumerBinding,
        )

    async def _query[T](
        self,
        organisation_id: str,
        collection: str,
        field: str,
        value: str,
        model: type[T],
    ) -> tuple[T, ...]:
        path = f"{FirestorePaths.organisation(organisation_id)}/{collection}"
        adapter = TypeAdapter(model)
        results: list[T] = []
        async for snapshot in (
            self._catalog.client.collection(path).where(field, "==", value).stream()
        ):
            data = snapshot.to_dict()
            if data is not None:
                results.append(adapter.validate_python(data))
        return tuple(results)

    async def _target(self, run: RotationRun) -> CredentialGeneration:
        value = await self._optional_target(run)
        if value is None:
            raise ValueError("run has no target generation")
        return value

    async def _optional_target(self, run: RotationRun) -> CredentialGeneration | None:
        return await self._optional_generation(run.organisation_id, run.target_generation_id)

    async def _optional_generation(
        self, organisation_id: str, generation_id: str | None
    ) -> CredentialGeneration | None:
        if generation_id is None:
            return None
        return await self._catalog.get(
            FirestorePaths.generation(organisation_id, generation_id), CredentialGeneration
        )

    async def _latest_report(
        self,
        run: RotationRun,
        generation_id: str | None,
        probe_stage: Stage | None = None,
    ) -> VerificationReport:
        path = f"{FirestorePaths.organisation(run.organisation_id)}/reports"
        values = []
        async for snapshot in (
            self._catalog.client.collection(path)
            .where("run_id", "==", run.id)
            .where("generation_id", "==", generation_id)
            .stream()
        ):
            data = snapshot.to_dict()
            if data is not None:
                values.append(VerificationReport.model_validate(data))
        expected_ids: set[str] | None = None
        if probe_stage is not None:
            control_version, _ = await self._control(run)
            expected_ids = set(control_version.definition.probe_versions.get(probe_stage, ()))
        passed = [
            item
            for item in values
            if item.status is VerificationStatus.PASSED
            and (
                expected_ids is None or {result.probe_id for result in item.results} == expected_ids
            )
        ]
        if not passed:
            raise ValueError("no passed verification report exists")
        return max(passed, key=lambda item: item.completed_at)

    async def _verification_checks(
        self,
        run: RotationRun,
        report: VerificationReport,
        stage: Stage,
    ) -> frozenset[str]:
        if report.status is not VerificationStatus.PASSED:
            raise ValueError("verification report did not pass")
        control_version, _ = await self._control(run)
        probe_ids = control_version.definition.probe_versions.get(stage, ())
        if {result.probe_id for result in report.results} != set(probe_ids):
            raise ValueError("verification report does not cover the pinned probe set")
        versions = [
            await self._catalog.get(
                FirestorePaths.probe_version(run.organisation_id, probe_id), ProbeVersion
            )
            for probe_id in probe_ids
        ]
        results = {result.probe_id: result for result in report.results}

        def require(
            kind: ProbeKind, required: frozenset[str], *, negative: bool | None = None
        ) -> None:
            selected = [
                results[version.id]
                for version in versions
                if version.definition.kind is kind
                and (negative is None or version.definition.negative is negative)
            ]
            if not selected or any(not required.issubset(result.checks) for result in selected):
                polarity = " negative" if negative else ""
                raise ValueError(f"{stage.value} lacks{polarity} {kind.value} evidence")

        browser_managed = not any(
            version.definition.kind in {ProbeKind.PROVIDER, ProbeKind.CREDENTIAL}
            for version in versions
        )
        if stage is Stage.VERIFY:
            if not browser_managed:
                require(ProbeKind.PROVIDER, frozenset({"provider-credential-exists"}))
                require(ProbeKind.CREDENTIAL, frozenset({"credential-accepted"}))
            require(ProbeKind.SECRET, frozenset({"secret-version-enabled"}))
            require(
                ProbeKind.RUNTIME,
                frozenset({"runtime-ready", "runtime-binding-inspected", "generation-identified"}),
            )
            checks = {
                "provider-valid",
                "store-valid",
                "deployment-valid",
                "coverage-complete",
                "rollback-ready",
            }
            if not run.deployments or any(not item.rollback_revision for item in run.deployments):
                raise ValueError("verification has no pinned rollback deployment")
            if control_version.definition.require_generation_telemetry:
                require(
                    ProbeKind.TELEMETRY,
                    frozenset(
                        {
                            "telemetry-query-executed",
                            "telemetry-generation-bound",
                            "telemetry-threshold-met",
                        }
                    ),
                    negative=False,
                )
                checks.add("telemetry-healthy")
            return frozenset(checks)

        if stage is Stage.OBSERVE:
            require(
                ProbeKind.RUNTIME,
                frozenset({"runtime-ready", "runtime-binding-inspected", "generation-identified"}),
            )
            checks = {"consumers-current"}
            if control_version.definition.require_generation_telemetry:
                require(
                    ProbeKind.TELEMETRY,
                    frozenset(
                        {
                            "telemetry-query-executed",
                            "telemetry-generation-bound",
                            "telemetry-threshold-met",
                        }
                    ),
                    negative=False,
                )
                require(
                    ProbeKind.TELEMETRY,
                    frozenset(
                        {
                            "telemetry-query-executed",
                            "telemetry-generation-bound",
                            "telemetry-no-old-use",
                        }
                    ),
                    negative=True,
                )
                checks.update({"telemetry-healthy", "old-use-clear"})
            return frozenset(checks)
        raise ValueError(f"stage {stage.value} does not use verification report checks")

    async def _approvers_known(self, organisation_id: str) -> bool:
        path = f"{FirestorePaths.organisation(organisation_id)}/principals"
        async for snapshot in self._catalog.client.collection(path).stream():
            data = snapshot.to_dict() or {}
            if data.get("enabled") is True and "administrator" in data.get("roles", []):
                return True
        return False

    async def _verify_audit(self, run: RotationRun) -> str:
        path = f"{FirestorePaths.organisation(run.organisation_id)}/audit"
        events = []
        async for snapshot in self._catalog.client.collection(path).order_by("sequence").stream():
            data = snapshot.to_dict()
            if data is not None:
                events.append(AuditEvent.model_validate(data))
        previous = GENESIS
        for sequence, event in enumerate(events):
            expected = event_hash(
                event.organisation_id,
                sequence,
                event.kind,
                event.actor_id,
                event.resource,
                event.run_id,
                event.payload,
                event.evidence_ids,
                previous,
                event.occurred_at,
                event.region,
            )
            if (
                event.sequence != sequence
                or event.previous_hash != previous
                or event.event_hash != expected
            ):
                raise ValueError("audit hash chain validation failed")
            previous = event.event_hash
        if not events:
            raise ValueError("audit chain is empty")
        manifest = json.dumps(
            [event.model_dump(mode="json") for event in events],
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        evidence = await self._evidence.store(
            run.organisation_id,
            run.id,
            "audit-chain",
            manifest,
            "application/json",
            self._clock(),
        )
        return evidence.id

    async def _stage_evidence(
        self, run: RotationRun, checks: frozenset[str], output: dict[str, Any]
    ) -> tuple[str, ...]:
        content = json.dumps(
            {"stage": run.stage.value, "checks": sorted(checks), "output": output},
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        stored = await self._evidence.store(
            run.organisation_id,
            run.id,
            f"stage-{run.stage.value}",
            content,
            "application/json",
            self._clock(),
        )
        return (stored.id,)

    async def _failure_evidence(self, run: RotationRun, reason: str) -> tuple[str, ...]:
        content = json.dumps(
            {"stage": run.stage.value, "reason": reason},
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        stored = await self._evidence.store(
            run.organisation_id,
            run.id,
            f"stage-{run.stage.value}-failure",
            content,
            "application/json",
            self._clock(),
        )
        return (stored.id,)

    async def _create_once(self, path: str, value: Any) -> None:
        from core.errors import ResourceConflictError

        try:
            await self._catalog.create(path, value)
        except ResourceConflictError:
            current = await self._catalog.get(path, type(value))
            if current != value:
                raise

    async def _control(self, run: RotationRun) -> tuple[ControlVersion, GatePolicy]:
        key = (run.organisation_id, run.credential_id, run.control_version)
        current = self._controls.get(key)
        if current is not None:
            return current
        version = await self._catalog.get(
            FirestorePaths.control_version(
                run.organisation_id, run.credential_id, run.control_version
            ),
            ControlVersion,
        )
        if (
            version.organisation_id != run.organisation_id
            or version.credential_id != run.credential_id
            or version.id != run.control_version
            or version.digest != digest(version.definition)
        ):
            raise ValueError("run control version is not immutable and authorised")
        gates = GatePolicy(version.definition.required_checks)
        current = (version, gates)
        self._controls[key] = current
        return current


def _execution_id(request: StageExecutionRequest) -> str:
    return _id("stage", request.run_id, request.stage.value, str(request.expected_revision))


def _revocation_checks(
    replacement: VerificationReport,
    rejection: VerificationReport,
    old_secret_reference: str | None,
    *,
    browser_managed: bool = False,
) -> frozenset[str]:
    if not browser_managed and "credential-accepted" not in replacement.checks:
        raise ValueError("revocation has no replacement credential authentication proof")
    required_rejection = {"secret-version-enabled"}
    if not browser_managed:
        required_rejection.update({"provider-credential-revoked", "credential-rejected"})
    if not required_rejection.issubset(rejection.checks):
        raise ValueError("revocation has incomplete old-credential rejection proof")
    if old_secret_reference is None:
        raise ValueError("revocation has no old secret version to disable")
    return frozenset({"old-revoked", "replacement-valid", "old-rejected", "old-secret-disabled"})


def _requires_provider_revocation(browser_managed: bool, provider_id: str | None) -> bool:
    return not browser_managed or provider_id is not None


def required_connection_roles() -> frozenset[ConnectionRole]:
    return frozenset({ConnectionRole.PROVIDER, ConnectionRole.SECRET_STORE, ConnectionRole.RUNTIME})


def _required_playbook(value: PlaybookVersion | None) -> PlaybookVersion:
    if value is None:
        raise ValueError("browser playbook is missing")
    return value


def _secret_parts(reference: str) -> tuple[str, str]:
    marker = "/versions/"
    secret, separator, version = reference.rpartition(marker)
    if not separator or not secret or not version:
        raise ValueError("secret reference must identify one immutable version")
    return secret, version


def _id(prefix: str, *values: str) -> str:
    checksum = hashlib.sha256("\0".join(values).encode()).hexdigest()[:40]
    return f"{prefix}_{checksum}"


async def _flag_reauthentication(
    catalog: FirestoreCatalog,
    notifications: NotificationService | None,
    now: datetime,
    run: RotationRun,
    output: dict[str, Any],
    execution_id: str,
) -> None:
    # The paused run is the authoritative state; the connection flag and the
    # notification are best-effort signals that must not fail the pause.
    connection_id = output.get("connection_id")
    if not isinstance(connection_id, str):
        return
    with contextlib.suppress(Exception):
        path = FirestorePaths.connection(run.organisation_id, connection_id)
        connection = await catalog.get(path, Connection)
        if connection.status is not ConnectionStatus.REAUTHENTICATION:
            await catalog.replace(
                path,
                Connection,
                connection.revision,
                lambda current: current.model_copy(
                    update={
                        "status": ConnectionStatus.REAUTHENTICATION,
                        "updated_at": now,
                        "revision": current.revision + 1,
                    }
                ),
            )
    with contextlib.suppress(Exception):
        await _remember_waiting_run(catalog, run, connection_id, now)
    if notifications is not None:
        with contextlib.suppress(Exception):
            await notifications.emit(
                _id("reauthentication", execution_id),
                run.organisation_id,
                NotificationKind.CONNECTION_UNHEALTHY,
                Severity.HIGH,
                "Provider session requires reauthentication",
                f"Run {run.id} paused: the browser session for {connection_id} "
                "landed on the provider login page.",
                f"/organisations/{run.organisation_id}/connections/{connection_id}",
                connection_id,
                run_id=run.id,
            )


async def _remember_waiting_run(
    catalog: FirestoreCatalog,
    run: RotationRun,
    connection_id: str,
    now: datetime,
) -> None:
    del now
    path = FirestorePaths.connection_waiter(run.organisation_id, connection_id)
    for _ in range(3):
        try:
            waiter = await catalog.get(path, ConnectionWaiter)
        except ResourceNotFoundError:
            try:
                await catalog.create(
                    path,
                    ConnectionWaiter(
                        organisation_id=run.organisation_id,
                        connection_id=connection_id,
                        run_ids=(run.id,),
                    ),
                )
                return
            except ResourceConflictError:
                continue
        if run.id in waiter.run_ids:
            return
        try:
            await catalog.replace(
                path,
                ConnectionWaiter,
                waiter.revision,
                lambda current: current.model_copy(
                    update={
                        "run_ids": (*current.run_ids, run.id),
                        "revision": current.revision + 1,
                    }
                ),
            )
            return
        except ResourceConflictError:
            continue
    raise ResourceConflictError("could not record a paused run waiting for reauthentication")


def _json_digest(value: Any) -> str:
    canonical = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _approval_parameters(value: dict[str, Any]) -> dict[str, Any]:
    if _contains_secret_value(value):
        raise ValueError("protected action parameters contain credential material")
    try:
        copied = json.loads(
            json.dumps(value, allow_nan=False, ensure_ascii=False, separators=(",", ":"))
        )
    except (TypeError, ValueError) as error:
        raise ValueError("protected action parameters are not canonical JSON") from error
    if not isinstance(copied, dict):
        raise ValueError("protected action parameters are invalid")
    return copied


def _resolve(value: Any, context: dict[str, Any]) -> Any:
    if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
        key = value[2:-1]
        if key not in context or context[key] is None:
            raise ValueError(f"playbook variable {key} is unavailable")
        return context[key]
    if isinstance(value, tuple):
        return [_resolve(item, context) for item in value]
    if isinstance(value, dict):
        return {key: _resolve(item, context) for key, item in value.items()}
    return value


def _flatten(values: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(values, dict):
        found.append(values)
        for value in values.values():
            found.extend(_flatten(value))
    elif isinstance(values, list | tuple):
        for value in values:
            found.extend(_flatten(value))
    return found


def _find_string(values: list[dict[str, Any]], name: str) -> str:
    value = _find_optional(values, name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"stage output has no {name}")
    return value


def _find_optional(values: list[dict[str, Any]], name: str) -> Any:
    return next((value[name] for value in values if name in value), None)


def _safe_browser_activity(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    activity: list[dict[str, Any]] = []
    for value in values:
        step_id = value.get("step_id")
        objective = value.get("objective")
        operation = value.get("operation")
        outcome = value.get("outcome")
        if not all(
            isinstance(item, str) and item for item in (step_id, objective, operation, outcome)
        ):
            continue
        item: dict[str, Any] = {
            "step_id": step_id,
            "objective": objective,
            "operation": operation,
            "outcome": outcome,
        }
        operator = value.get("operator")
        if isinstance(operator, dict):
            item["operator"] = {
                key: operator[key]
                for key in ("ready", "expected_checkpoint", "drift_detected")
                if key in operator
            }
        activity.append(item)
    return activity


def _contains_secret_value(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if key.lower() in {
                "api_key",
                "authorization",
                "credential_material",
                "password",
                "private_key",
                "secret",
                "token",
                "value",
            }:
                return True
            if _contains_secret_value(item):
                return True
    if isinstance(value, list | tuple):
        return any(_contains_secret_value(item) for item in value)
    return False


def _required(value: str | None, label: str) -> str:
    if value is None:
        raise ValueError(f"{label} is missing")
    return value


def _integer(value: Any, default: int) -> int:
    return value if isinstance(value, int) and value > 0 else default
