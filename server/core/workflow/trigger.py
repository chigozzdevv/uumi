from datetime import datetime

from contracts import CreateRunCommand, RotationRun


def build_run(command: CreateRunCommand, run_id: str, now: datetime) -> RotationRun:
    return RotationRun(
        id=run_id,
        organisation_id=command.organisation_id,
        credential_id=command.credential_id,
        trigger=command.trigger,
        policy_version=command.policy_version,
        dry_run_id=command.dry_run.id if command.dry_run is not None else None,
        dry_run_playbook_id=(command.dry_run.playbook_id if command.dry_run is not None else None),
        created_at=now,
        updated_at=now,
    )
