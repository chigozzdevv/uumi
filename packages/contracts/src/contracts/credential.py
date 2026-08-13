from pydantic import AwareDatetime, Field

from contracts.base import Contract, Identifier


class ManagedCredential(Contract):
    id: Identifier
    organisation_id: Identifier
    connection_id: Identifier
    provider: str = Field(min_length=1, max_length=64)
    kind: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=160)
    provider_id: str | None = Field(default=None, max_length=256)
    scopes: frozenset[str] = frozenset()
    consumer_ids: tuple[Identifier, ...] = ()
    active_generation_id: Identifier | None = None
    policy_version: Identifier
    playbook_version: Identifier
    created_at: AwareDatetime
    updated_at: AwareDatetime
    revision: int = Field(default=0, ge=0)
