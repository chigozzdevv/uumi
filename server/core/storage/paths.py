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
