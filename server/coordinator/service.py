import hashlib
import json
from collections.abc import Callable
from datetime import datetime
from typing import Any

from agents.runtime import AgentRuntimeService
from agents.shared.models import OperatorDecision
from broker.evidence import GcsEvidenceSink
from connectors import ConnectorContext
from contracts import (
    AgentKind,
    AgentTask,
    Approval,
    ApprovalDecision,
    AuditEvent,
    Connection,
    ConnectionKind,
    ConnectionStatus,
    ConsumerBinding,
    CredentialGeneration,
    DryRun,
    DryRunStatus,
    Environment,
    GenerationState,
    IncidentStatus,
    ManagedCredential,
    PlaybookAssignment,
    PlaybookState,
    PlaybookStep,
    PlaybookVersion,
    ProbeDefinition,
    ProbeKind,
    ProtectedAction,
    RecoveryMode,
    RecoveryPlan,
    RotationPlan,
    RotationRun,
    RotationStrategy,
    RunStatus,
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
from core.generation import GenerationService
from core.incident import IncidentService
from core.storage.catalog import FirestoreCatalog
from core.storage.paths import FirestorePaths
from policy import GatePolicy, digest
from pydantic import TypeAdapter
from verifier import VerificationService

from coordinator.broker import McpBrokerClient
from coordinator.browser import BrowserPauseError, BrowserStepExecutor


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
        self._policy = GatePolicy()

    async def execute(self, request: StageExecutionRequest) -> StageExecutionResult:
        started = self._clock()
        execution_id = _execution_id(request)
        path = FirestorePaths.stage(request.organisation_id, execution_id)
        try:
            current = await self._catalog.get(path, StageExecutionResult)
        except Exception as error:
            from core.errors import ResourceNotFoundError

            if not isinstance(error, ResourceNotFoundError):
                raise
        else:
            return current
        run = await self._catalog.get(
            FirestorePaths.run(request.organisation_id, request.run_id), RotationRun
        )
        self._validate_request(request, run)
        try:
            checks, evidence_ids, bindings, output = await self._dispatch(run)
            proof_evidence = await self._stage_evidence(run, checks, output)
            all_evidence = tuple(dict.fromkeys((*evidence_ids, *proof_evidence)))
            result = StageExecutionResult(
                id=execution_id,
                organisation_id=run.organisation_id,
                run_id=run.id,
                stage=run.stage,
                status=StageExecutionStatus.SUCCEEDED,
                checks=checks,
                evidence_ids=all_evidence,
                bindings=bindings,
                output=output,
                started_at=started,
                completed_at=self._clock(),
            )
            self._policy.validate(
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
            Stage.PLAYBOOK: self._playbook,
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
        credential = await self._credential(run)
        if (
            credential.active_generation_id is None
            or not credential.scopes
            or not credential.consumer_ids
        ):
            raise ValueError("credential inventory is incomplete")
        assignment, version = await self._playbook_context(run, credential)
        if run.dry_run_id is not None:
            dryrun = await self._catalog.get(
                FirestorePaths.dryrun(
                    run.organisation_id,
                    _required(run.dry_run_playbook_id, "dry-run playbook"),
                    run.dry_run_id,
                ),
                DryRun,
            )
            environment = await self._catalog.get(
                FirestorePaths.environment(run.organisation_id, dryrun.environment_id),
                Environment,
            )
            if (
                dryrun.status is not DryRunStatus.RUNNING
                or environment.production
                or version.state is not PlaybookState.TEST
                or not assignment.dry_run_only
                or assignment.environment_id != environment.id
                or dryrun.version_id != version.id
                or dryrun.credential_id != credential.id
            ):
                raise ValueError("dry-run isolation or immutable playbook binding is invalid")
        elif version.state is not PlaybookState.ACTIVE or assignment.dry_run_only:
            raise ValueError("production rotation requires an active production assignment")
        elif credential.playbook_version != version.id:
            raise ValueError("credential inventory and production playbook assignment differ")
        connections = [
            await self._catalog.get(
                FirestorePaths.connection(run.organisation_id, item), Connection
            )
            for item in assignment.connection_ids
        ]
        if any(item.status is not ConnectionStatus.READY for item in connections):
            raise ValueError("one or more playbook connections are not ready")
        kinds = {item.kind for item in connections}
        if not {ConnectionKind.PROVIDER, ConnectionKind.SECRET, ConnectionKind.RUNTIME}.issubset(
            kinds
        ):
            raise ValueError("provider, secret store, and runtime connections are required")
        bindings = await self._bindings(run, credential)
        if {item.service_id for item in bindings} != set(credential.consumer_ids):
            raise ValueError("consumer bindings do not cover credential inventory")
        await self._require_probes(run, version)
        lock = await self._catalog.client.document(
            FirestorePaths.lock(run.organisation_id, credential.id)
        ).get()
        if not lock.exists or (lock.to_dict() or {}).get("run_id") != run.id:
            raise ValueError("credential rotation lock changed")
        if not await self._approvers_known(run.organisation_id):
            raise ValueError("organisation has no active approver")
        task = AgentTask(
            id=_id("task", run.id, "preflight"),
            organisation_id=run.organisation_id,
            run_id=run.id,
            agent=AgentKind.INVENTORY,
            skill="detect_stale_mapping",
            objective="Confirm every observed credential consumer is represented in inventory.",
            context={"credential_id": credential.id},
            requested_at=self._clock(),
        )
        agent = await self._agents.execute(task)
        if not agent.succeeded or agent.output.get("missing_inventory"):
            raise ValueError("inventory agent found unresolved consumer mappings")
        output = {
            "credential_id": credential.id,
            "playbook_version": version.id,
            "connections": [item.id for item in connections],
            "bindings": [item.id for item in bindings],
            "agent": agent.output,
        }
        return (
            self._policy.checks(Stage.PREFLIGHT),
            agent.evidence_ids,
            StageBindings(
                playbook_version=version.id,
                current_generation_id=credential.active_generation_id,
            ),
            output,
        )

    async def _playbook(
        self, run: RotationRun
    ) -> tuple[frozenset[str], tuple[str, ...], StageBindings, dict[str, Any]]:
        credential = await self._credential(run)
        _, version = await self._playbook_context(run, credential)
        task = AgentTask(
            id=_id("task", run.id, "plan"),
            organisation_id=run.organisation_id,
            run_id=run.id,
            agent=AgentKind.PLANNER,
            skill="plan_rotation",
            objective="Select a complete rotation strategy bound to the assigned playbook.",
            context={"credential_id": credential.id, "playbook_version": version.id},
            requested_at=self._clock(),
        )
        agent = await self._agents.execute(task)
        if not agent.succeeded:
            raise ValueError("rotation planner agent failed")
        strategy_value = agent.output.get("strategy")
        if not isinstance(strategy_value, str):
            raise ValueError("rotation planner returned no valid strategy")
        try:
            strategy = RotationStrategy(strategy_value)
        except (TypeError, ValueError) as error:
            raise ValueError("rotation planner returned no valid strategy") from error
        if strategy is RotationStrategy.IMMEDIATE and len(credential.consumer_ids) > 1:
            raise ValueError("immediate rotation is invalid for multiple consumers")
        recovery = RecoveryPlan(
            id=_id("recovery", run.id, version.id),
            organisation_id=run.organisation_id,
            run_id=run.id,
            failed_stage=Stage.DEPLOY,
            mode=RecoveryMode.ROLLBACK,
            steps=version.definition.recovery.get("deploy", ("runtime.rollback",)),
            preserves_old_generation=True,
            requires_approval=False,
        )
        plan = RotationPlan(
            id=_id("plan", run.id, version.id),
            organisation_id=run.organisation_id,
            run_id=run.id,
            credential_id=credential.id,
            policy_version=run.policy_version,
            playbook_version=version.id,
            strategy=strategy,
            target_scopes=credential.scopes,
            consumer_ids=credential.consumer_ids,
            observation_seconds=_integer(agent.output.get("observation_seconds"), 300),
            recovery_id=recovery.id,
        )
        await self._create_once(FirestorePaths.recovery(run.organisation_id, recovery.id), recovery)
        await self._create_once(FirestorePaths.plan(run.organisation_id, plan.id), plan)
        checksum = digest(plan)
        return (
            self._policy.checks(Stage.PLAYBOOK),
            agent.evidence_ids,
            StageBindings(plan_id=plan.id, plan_hash=checksum),
            {"plan": plan.model_dump(mode="json"), "plan_hash": checksum},
        )

    async def _create(
        self, run: RotationRun
    ) -> tuple[frozenset[str], tuple[str, ...], StageBindings, dict[str, Any]]:
        credential = await self._credential(run)
        assignment, version = await self._playbook_context(run, credential)
        outputs, evidence = await self._execute_steps(run, assignment, version, Stage.CREATE)
        flattened = _flatten(outputs)
        provider_id = _find_string(flattened, "provider_id")
        secret_reference = _find_string(flattened, "secret_reference")
        fingerprint = _find_string(flattened, "fingerprint")
        generation_id = _id("generation", run.id, provider_id)
        generation = CredentialGeneration(
            id=generation_id,
            organisation_id=run.organisation_id,
            credential_id=credential.id,
            provider_id=provider_id,
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
            self._policy.checks(Stage.CREATE),
            evidence,
            StageBindings(target_generation_id=generation.id),
            {"generation": generation.model_dump(mode="json")},
        )

    async def _store(
        self, run: RotationRun
    ) -> tuple[frozenset[str], tuple[str, ...], StageBindings, dict[str, Any]]:
        credential = await self._credential(run)
        assignment, version = await self._playbook_context(run, credential)
        target = await self._target(run)
        outputs, evidence = await self._execute_steps(run, assignment, version, Stage.STORE)
        states = {value.get("state") for value in _flatten(outputs) if isinstance(value, dict)}
        if states and "ENABLED" not in states:
            raise ValueError("stored secret version is not enabled")
        if "secret" in json.dumps(outputs).lower() and _contains_secret_value(outputs):
            raise ValueError("secret value escaped into stage output")
        bindings = await self._bindings(run, credential)
        await self._generations.stage_bindings(
            run.organisation_id,
            credential.id,
            target.id,
            _required(target.secret_reference, "target secret reference"),
            tuple(item.id for item in bindings),
        )
        return (
            self._policy.checks(Stage.STORE),
            evidence,
            StageBindings(),
            {
                "secret_reference": target.secret_reference,
                "bindings": [item.id for item in bindings],
            },
        )

    async def _deploy(
        self, run: RotationRun
    ) -> tuple[frozenset[str], tuple[str, ...], StageBindings, dict[str, Any]]:
        credential = await self._credential(run)
        assignment, version = await self._playbook_context(run, credential)
        outputs, evidence = await self._execute_steps(run, assignment, version, Stage.DEPLOY)
        flat = _flatten(outputs)
        if not _find_optional(flat, "candidate_revision") or not _find_optional(
            flat, "rollback_revision"
        ):
            raise ValueError("deployment returned no candidate or rollback revision")
        if _find_optional(flat, "generation_id") != run.target_generation_id:
            raise ValueError("runtime candidate does not carry the target generation")
        return self._policy.checks(Stage.DEPLOY), evidence, StageBindings(), {"steps": outputs}

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
        return (
            self._policy.checks(Stage.VERIFY),
            report.evidence_ids,
            StageBindings(),
            {"report": report.model_dump(mode="json")},
        )

    async def _rollout(
        self, run: RotationRun
    ) -> tuple[frozenset[str], tuple[str, ...], StageBindings, dict[str, Any]]:
        credential = await self._credential(run)
        assignment, version = await self._playbook_context(run, credential)
        outputs, evidence = await self._execute_steps(run, assignment, version, Stage.ROLLOUT)
        report = await self._latest_report(run, run.target_generation_id)
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
            self._policy.checks(Stage.ROLLOUT),
            tuple(dict.fromkeys((*evidence, *report.evidence_ids))),
            StageBindings(),
            {"steps": outputs, "active_generation": run.target_generation_id},
        )

    async def _observe(
        self, run: RotationRun
    ) -> tuple[frozenset[str], tuple[str, ...], StageBindings, dict[str, Any]]:
        report = await self._run_verification(run, negative=False, observation=True)
        if report.status is not VerificationStatus.PASSED:
            raise ValueError("observation probes failed")
        return (
            self._policy.checks(Stage.OBSERVE),
            report.evidence_ids,
            StageBindings(),
            {"report": report.model_dump(mode="json")},
        )

    async def _approval(
        self, run: RotationRun
    ) -> tuple[frozenset[str], tuple[str, ...], StageBindings, dict[str, Any]]:
        credential = await self._credential(run)
        _, version = await self._playbook_context(run, credential)
        approvals = []
        context = await self._step_context(run)
        for step in version.definition.steps:
            if step.stage is not Stage.REVOKE or not step.protected:
                continue
            payload = _resolve(step.parameters, context)
            if not isinstance(payload, dict):
                raise ValueError(f"playbook step {step.id} parameters are invalid")
            payload.pop("connection_id", None)
            payload.pop("approval_id", None)
            approvals.append(await self._approval_for_step(run, step, payload))
        if not approvals:
            raise ValueError("revocation has no protected action approval")
        return (
            self._policy.checks(Stage.APPROVAL),
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
        credential = await self._credential(run)
        assignment, version = await self._playbook_context(run, credential)
        outputs, evidence = await self._execute_steps(run, assignment, version, Stage.REVOKE)
        report = await self._run_verification(run, negative=True)
        if report.status is not VerificationStatus.PASSED:
            raise ValueError("old credential still works after revocation")
        await self._generations.revoke(
            run.organisation_id,
            _required(run.current_generation_id, "old generation"),
            report.id,
        )
        await self._incidents.advance_run(run.organisation_id, run.id, IncidentStatus.CONTAINED)
        return (
            self._policy.checks(Stage.REVOKE),
            tuple(dict.fromkeys((*evidence, *report.evidence_ids))),
            StageBindings(),
            {"steps": outputs, "report": report.model_dump(mode="json")},
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
        report = await self._latest_report(run, run.current_generation_id)
        await self._incidents.advance_run(run.organisation_id, run.id, IncidentStatus.RESOLVED)
        return (
            self._policy.checks(Stage.COMPLETE),
            tuple(dict.fromkeys((*report.evidence_ids, audit_evidence))),
            StageBindings(),
            {"active_generation": credential.active_generation_id, "audit_valid": True},
        )

    async def _execute_steps(
        self,
        run: RotationRun,
        assignment: PlaybookAssignment,
        version: PlaybookVersion,
        stage: Stage,
    ) -> tuple[list[dict[str, Any]], tuple[str, ...]]:
        steps = tuple(item for item in version.definition.steps if item.stage is stage)
        if not steps:
            raise ValueError(f"playbook has no {stage.value} execution step")
        context = await self._step_context(run)
        outputs = []
        evidence: list[str] = []
        for step in steps:
            if step.tool == "verification.run":
                continue
            payload = _resolve(step.parameters, context)
            if not isinstance(payload, dict):
                raise ValueError(f"playbook step {step.id} parameters are invalid")
            connection_id = payload.pop("connection_id", None)
            declared_approval = payload.pop("approval_id", None)
            if declared_approval is not None:
                raise ValueError("approval IDs are runtime bindings, not playbook parameters")
            approval = await self._approval_for_step(run, step, payload) if step.protected else None
            if step.tool.startswith("browser."):
                decision = await self._operator_decision(run, step)
                resolved = step.model_copy(update={"parameters": payload})
                browser_output = await self._browser.execute(run, assignment, resolved, approval)
                outputs.append(
                    {
                        **browser_output,
                        "operator": decision.model_dump(mode="json"),
                    }
                )
                continue
            if not isinstance(connection_id, str):
                raise ValueError(f"playbook step {step.id} has no connection_id")
            result = await self._broker.execute(
                run,
                _id("tool", run.id, step.id),
                connection_id,
                step.tool,
                payload,
                approval.id if approval is not None else None,
            )
            if not result.succeeded:
                raise ValueError(f"playbook tool {step.tool} failed: {result.error_code}")
            outputs.append(result.result)
            evidence.extend(result.evidence_ids)
        return outputs, tuple(dict.fromkeys(evidence))

    async def _operator_decision(self, run: RotationRun, step: PlaybookStep) -> OperatorDecision:
        result = await self._agents.execute(
            AgentTask(
                id=_id("task", run.id, step.id, "operator"),
                organisation_id=run.organisation_id,
                run_id=run.id,
                agent=AgentKind.OPERATOR,
                skill="execute_console_playbook",
                objective=(
                    f"Load immutable browser step {step.id} and decide whether it is ready "
                    "for the isolated Computer Use worker. Do not execute the browser action."
                ),
                context={"step_id": step.id, "stage": step.stage.value},
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
        return {
            "run_id": run.id,
            "credential_id": run.credential_id,
            "target_generation_id": run.target_generation_id,
            "target_provider_id": target.provider_id if target else None,
            "target_secret_reference": target.secret_reference if target else None,
            "old_generation_id": run.current_generation_id,
            "old_provider_id": old.provider_id if old else None,
            "old_secret_reference": old.secret_reference if old else None,
        }

    async def _approval_for_step(
        self,
        run: RotationRun,
        step: PlaybookStep,
        payload: dict[str, Any],
    ) -> Approval:
        if run.plan_id is None or run.plan_hash is None:
            raise ValueError("protected action has no immutable rotation plan")
        credential = await self._credential(run)
        generation_id = _required(run.current_generation_id, "current generation")
        generation = await self._catalog.get(
            FirestorePaths.generation(run.organisation_id, generation_id), CredentialGeneration
        )
        evidence_hash = await self._approval_evidence_hash(run, step.stage)
        parameters = {
            key: value
            for key, value in payload.items()
            if isinstance(value, str | int | bool) and key not in {"secret", "value", "token"}
        }
        resource = next(
            (
                value
                for key, value in parameters.items()
                if key in {"provider_id", "secret_resource", "version", "resource", "service"}
                and isinstance(value, str)
            ),
            step.operation,
        )
        action = ProtectedAction(
            id=_id("action", run.id, step.id, _json_digest(parameters)),
            organisation_id=run.organisation_id,
            run_id=run.id,
            kind=step.tool,
            resource=resource,
            credential_id=credential.id,
            generation_id=generation_id,
            provider_id=_required(
                generation.provider_id or credential.provider_id, "protected action provider ID"
            ),
            parameters={"step_id": step.id, **parameters},
        )
        approvals = await self._query(run.organisation_id, "approvals", "run_id", run.id, Approval)
        matching = []
        for approval in approvals:
            if approval.action_id != action.id:
                continue
            stored = await self._catalog.get(
                FirestorePaths.action(run.organisation_id, approval.action_id), ProtectedAction
            )
            if stored != action:
                raise ValueError("protected action changed after approval request")
            matching.append(approval)
        approval_request = {
            "action": action.model_dump(mode="json"),
            "plan_hash": run.plan_hash,
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
        expected = (digest(action), run.plan_hash, evidence_hash, generation_id)
        actual = (
            approval.action_digest,
            approval.plan_hash,
            approval.evidence_hash,
            approval.generation_id,
        )
        if actual != expected:
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
    ) -> VerificationReport:
        credential = await self._credential(run)
        _, version = await self._playbook_context(run, credential)
        definitions = await self._probes(run, version, negative, observation)
        connection = await self._catalog.get(
            FirestorePaths.connection(run.organisation_id, credential.connection_id), Connection
        )
        context = ConnectorContext(
            request_id=_id("verify", run.id, run.stage.value),
            agent_id="coordinator_one",
            connection=connection,
            run=run,
            now=self._clock(),
            idempotency_key=_id("verify", run.id, run.stage.value),
        )
        generation_id = run.current_generation_id if negative else run.target_generation_id
        return await self._verifier.verify(
            _id("report", run.id, run.stage.value),
            run.organisation_id,
            run.id,
            _required(generation_id, "verification generation"),
            definitions,
            context,
        )

    async def _probes(
        self,
        run: RotationRun,
        version: PlaybookVersion,
        negative: bool,
        observation: bool,
    ) -> tuple[ProbeDefinition, ...]:
        ids: list[str] = []
        for step in version.definition.steps:
            if step.stage is not run.stage or step.tool != "verification.run":
                continue
            value = step.parameters.get("probe_ids", ())
            if isinstance(value, tuple):
                ids.extend(item for item in value if isinstance(item, str))
        if not ids:
            raise ValueError(f"stage {run.stage.value} declares no deterministic probes")
        resolved = []
        for item in ids:
            resolved.append(
                await self._catalog.get(
                    FirestorePaths.probe(run.organisation_id, item), ProbeDefinition
                )
            )
        values = tuple(resolved)
        expected_negative = {ProbeKind.PROVIDER, ProbeKind.SECRET} if negative else set()
        if negative and not expected_negative.issubset(
            {item.kind for item in values if item.negative}
        ):
            raise ValueError("revocation requires negative provider and secret probes")
        if observation and ProbeKind.TELEMETRY not in {item.kind for item in values}:
            raise ValueError("observation requires a telemetry probe")
        if run.stage is Stage.VERIFY:
            required = {
                ProbeKind.PROVIDER,
                ProbeKind.SECRET,
                ProbeKind.RUNTIME,
                ProbeKind.HTTP,
                ProbeKind.TELEMETRY,
            }
            if not required.issubset({item.kind for item in values}):
                raise ValueError("verification probe coverage is incomplete")
        return values

    async def _require_probes(self, run: RotationRun, version: PlaybookVersion) -> None:
        ids: set[str] = set()
        for step in version.definition.steps:
            value = step.parameters.get("probe_ids", ())
            if step.tool == "verification.run" and isinstance(value, tuple):
                ids.update(item for item in value if isinstance(item, str))
        if not ids:
            raise ValueError("playbook has no deterministic verification probes")
        for probe_id in ids:
            await self._catalog.get(
                FirestorePaths.probe(run.organisation_id, probe_id), ProbeDefinition
            )

    async def _credential(self, run: RotationRun) -> ManagedCredential:
        return await self._catalog.get(
            FirestorePaths.credential(run.organisation_id, run.credential_id),
            ManagedCredential,
        )

    async def _playbook_context(
        self, run: RotationRun, credential: ManagedCredential
    ) -> tuple[PlaybookAssignment, PlaybookVersion]:
        assignment = await self._catalog.get(
            FirestorePaths.assignment(run.organisation_id, credential.id), PlaybookAssignment
        )
        version = await self._catalog.get(
            FirestorePaths.playbook_version(
                run.organisation_id, assignment.playbook_id, assignment.version_id
            ),
            PlaybookVersion,
        )
        if run.playbook_version is not None and run.playbook_version != version.id:
            raise ValueError("run playbook binding changed")
        if run.dry_run_id is not None and (
            not assignment.dry_run_only or run.dry_run_playbook_id != assignment.playbook_id
        ):
            raise ValueError("dry-run assignment changed")
        return assignment, version

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
        self, run: RotationRun, generation_id: str | None
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
        passed = [item for item in values if item.status is VerificationStatus.PASSED]
        if not passed:
            raise ValueError("no passed verification report exists")
        return max(passed, key=lambda item: item.completed_at)

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

    async def _create_once(self, path: str, value: Any) -> None:
        from core.errors import ResourceConflictError

        try:
            await self._catalog.create(path, value)
        except ResourceConflictError:
            current = await self._catalog.get(path, type(value))
            if current != value:
                raise


def _execution_id(request: StageExecutionRequest) -> str:
    return _id("stage", request.run_id, request.stage.value, str(request.expected_revision))


def _id(prefix: str, *values: str) -> str:
    checksum = hashlib.sha256("\0".join(values).encode()).hexdigest()[:40]
    return f"{prefix}_{checksum}"


def _json_digest(value: Any) -> str:
    canonical = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


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


def _contains_secret_value(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if key.lower() in {"secret", "api_key", "password", "token", "value"}:
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
