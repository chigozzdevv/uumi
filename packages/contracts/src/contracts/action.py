from pydantic import Field

from contracts.base import Contract, Identifier


class ProtectedAction(Contract):
    id: Identifier
    organisation_id: Identifier
    run_id: Identifier
    kind: str = Field(min_length=1, max_length=96)
    resource: str = Field(min_length=1, max_length=512)
    credential_id: Identifier
    generation_id: Identifier
    provider_id: str = Field(min_length=1, max_length=256)
    parameters: dict[str, str | int | bool] = Field(default_factory=dict)
