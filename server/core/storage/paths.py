import hashlib

from contracts import Identifier
from pydantic import TypeAdapter

_IDENTIFIER = TypeAdapter(Identifier)


class FirestorePaths:
    @staticmethod
    def run(organisation_id: str, run_id: str) -> str:
        return f"{FirestorePaths.organisation(organisation_id)}/runs/{_segment(run_id)}"

    @staticmethod
    def step(organisation_id: str, run_id: str, command_id: str) -> str:
        return f"{FirestorePaths.run(organisation_id, run_id)}/steps/{_segment(command_id)}"

    @staticmethod
    def outbox(organisation_id: str, event_id: str) -> str:
        return f"{FirestorePaths.organisation(organisation_id)}/outbox/{_segment(event_id)}"

    @staticmethod
    def delivery(organisation_id: str, run_id: str) -> str:
        return f"{FirestorePaths.organisation(organisation_id)}/deliveries/{_segment(run_id)}"

    @staticmethod
    def connection(organisation_id: str, connection_id: str) -> str:
        return (
            f"{FirestorePaths.organisation(organisation_id)}/connections/{_segment(connection_id)}"
        )

    @staticmethod
    def application(organisation_id: str, application_id: str) -> str:
        organisation = FirestorePaths.organisation(organisation_id)
        return f"{organisation}/applications/{_segment(application_id)}"

    @staticmethod
    def environment(organisation_id: str, environment_id: str) -> str:
        organisation = FirestorePaths.organisation(organisation_id)
        return f"{organisation}/environments/{_segment(environment_id)}"

    @staticmethod
    def service(organisation_id: str, service_id: str) -> str:
        return f"{FirestorePaths.organisation(organisation_id)}/services/{_segment(service_id)}"

    @staticmethod
    def credential(organisation_id: str, credential_id: str) -> str:
        return (
            f"{FirestorePaths.organisation(organisation_id)}/credentials/{_segment(credential_id)}"
        )

    @staticmethod
    def binding(organisation_id: str, binding_id: str) -> str:
        return f"{FirestorePaths.organisation(organisation_id)}/bindings/{_segment(binding_id)}"

    @staticmethod
    def generation(organisation_id: str, generation_id: str) -> str:
        return (
            f"{FirestorePaths.organisation(organisation_id)}/generations/{_segment(generation_id)}"
        )

    @staticmethod
    def playbook(organisation_id: str, playbook_id: str) -> str:
        return f"{FirestorePaths.organisation(organisation_id)}/playbooks/{_segment(playbook_id)}"

    @staticmethod
    def playbook_version(organisation_id: str, playbook_id: str, version_id: str) -> str:
        root = FirestorePaths.playbook(organisation_id, playbook_id)
        return f"{root}/versions/{_segment(version_id)}"

    @staticmethod
    def dryrun(organisation_id: str, playbook_id: str, run_id: str) -> str:
        root = FirestorePaths.playbook(organisation_id, playbook_id)
        return f"{root}/dryruns/{_segment(run_id)}"

    @staticmethod
    def assignment(organisation_id: str, credential_id: str) -> str:
        return (
            f"{FirestorePaths.organisation(organisation_id)}/assignments/{_segment(credential_id)}"
        )

    @staticmethod
    def incident(organisation_id: str, incident_id: str) -> str:
        return f"{FirestorePaths.organisation(organisation_id)}/incidents/{_segment(incident_id)}"

    @staticmethod
    def ingestion(organisation_id: str, source: str, event_id: str, kind: str) -> str:
        identity = hashlib.sha256(f"{source}\0{event_id}\0{kind}".encode()).hexdigest()
        return f"{FirestorePaths.organisation(organisation_id)}/ingestion/event_{identity}"

    @staticmethod
    def approval(organisation_id: str, approval_id: str) -> str:
        return f"{FirestorePaths.organisation(organisation_id)}/approvals/{_segment(approval_id)}"

    @staticmethod
    def action(organisation_id: str, action_id: str) -> str:
        return f"{FirestorePaths.organisation(organisation_id)}/actions/{_segment(action_id)}"

    @staticmethod
    def audit(organisation_id: str, event_id: str) -> str:
        return f"{FirestorePaths.organisation(organisation_id)}/audit/{_segment(event_id)}"

    @staticmethod
    def audit_head(organisation_id: str) -> str:
        return f"{FirestorePaths.organisation(organisation_id)}/audit-state/head"

    @staticmethod
    def evidence(organisation_id: str, evidence_id: str) -> str:
        return f"{FirestorePaths.organisation(organisation_id)}/evidence/{_segment(evidence_id)}"

    @staticmethod
    def browser(organisation_id: str, session_id: str) -> str:
        return f"{FirestorePaths.organisation(organisation_id)}/browsers/{_segment(session_id)}"

    @staticmethod
    def replay(organisation_id: str, session_id: str, checkpoint_id: str) -> str:
        root = FirestorePaths.browser(organisation_id, session_id)
        return f"{root}/replay/{_segment(checkpoint_id)}"

    @staticmethod
    def probe(organisation_id: str, probe_id: str) -> str:
        return f"{FirestorePaths.organisation(organisation_id)}/probes/{_segment(probe_id)}"

    @staticmethod
    def report(organisation_id: str, report_id: str) -> str:
        return f"{FirestorePaths.organisation(organisation_id)}/reports/{_segment(report_id)}"

    @staticmethod
    def tool(organisation_id: str, request_id: str) -> str:
        return f"{FirestorePaths.organisation(organisation_id)}/tools/{_segment(request_id)}"

    @staticmethod
    def agent(organisation_id: str, agent_id: str) -> str:
        return f"{FirestorePaths.organisation(organisation_id)}/agents/{_segment(agent_id)}"

    @staticmethod
    def agent_session(organisation_id: str, session_id: str) -> str:
        return (
            f"{FirestorePaths.organisation(organisation_id)}/agent-sessions/{_segment(session_id)}"
        )

    @staticmethod
    def agent_memory(organisation_id: str, memory_id: str) -> str:
        return f"{FirestorePaths.organisation(organisation_id)}/agent-memory/{_segment(memory_id)}"

    @staticmethod
    def lock(organisation_id: str, credential_id: str) -> str:
        organisation = FirestorePaths.organisation(organisation_id)
        return f"{organisation}/credentials/{_segment(credential_id)}/locks/rotation"

    @staticmethod
    def dedupe(organisation_id: str, source: str, event_id: str) -> str:
        identity = hashlib.sha256(f"{source}\0{event_id}".encode()).hexdigest()
        return f"{FirestorePaths.organisation(organisation_id)}/dedupe/event_{identity}"

    @staticmethod
    def principal(organisation_id: str, principal_id: str) -> str:
        organisation = FirestorePaths.organisation(organisation_id)
        return f"{organisation}/principals/{principal_id}"

    @staticmethod
    def organisation(organisation_id: str) -> str:
        return f"organisations/{_segment(organisation_id)}"


def _segment(value: str) -> str:
    return _IDENTIFIER.validate_python(value)
