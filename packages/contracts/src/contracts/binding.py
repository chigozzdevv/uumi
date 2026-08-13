from pydantic import Field

from contracts.base import Contract, Identifier


class ConsumerBinding(Contract):
    id: Identifier
    organisation_id: Identifier
    credential_id: Identifier
    service_id: Identifier
    environment_id: Identifier
    runtime_connection_id: Identifier
    runtime_resource: str = Field(min_length=1, max_length=512)
    secret_reference: str = Field(min_length=1, max_length=1024)
    current_generation_id: Identifier
    target_generation_id: Identifier | None = None
    verification_id: Identifier
    required: bool = True
    revision: int = Field(default=0, ge=0)
