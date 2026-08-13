from typing import Any

from pydantic import Field

from contracts.base import Contract, Identifier


class ToolRequest(Contract):
    id: Identifier
    organisation_id: Identifier
    run_id: Identifier
    agent_id: Identifier
    tool: str = Field(min_length=1, max_length=128)
    connection_id: Identifier
    payload: dict[str, Any] = Field(default_factory=dict)
    fencing_token: int = Field(gt=0)


class ToolResult(Contract):
    request_id: Identifier
    succeeded: bool
    result: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = Field(default=None, max_length=96)
    evidence_ids: tuple[Identifier, ...] = ()
