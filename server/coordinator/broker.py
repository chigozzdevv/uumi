import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from broker.capability import CapabilityClaims, CapabilitySigner, request_digest
from contracts import Approval, ProtectedAction, RotationRun, ToolRequest, ToolResult
from core.mcpclient import authenticated_streamable_http
from core.storage.catalog import FirestoreCatalog
from core.storage.paths import FirestorePaths
from google.auth.transport.requests import Request
from google.oauth2 import id_token
from mcp import Client


class McpBrokerClient:
    def __init__(
        self,
        url: str,
        signer: CapabilitySigner,
        catalog: FirestoreCatalog,
    ) -> None:
        self._url = url.rstrip("/") + "/mcp"
        self._audience = url.rstrip("/")
        self._signer = signer
        self._catalog = catalog

    async def execute(
        self,
        run: RotationRun,
        request_id: str,
        connection_id: str,
        tool: str,
        payload: dict[str, Any],
        approval_id: str | None = None,
    ) -> ToolResult:
        request = ToolRequest(
            id=request_id,
            organisation_id=run.organisation_id,
            run_id=run.id,
            agent_id="coordinator_one",
            tool=tool,
            connection_id=connection_id,
            payload=payload,
            fencing_token=run.fencing_token,
        )
        headers = {"Authorization": f"Bearer {await self._identity_token()}"}
        if tool not in _READ_TOOLS:
            headers["X-Uumi-Capability"] = await self._capability(run, request, approval_id)
        transport = authenticated_streamable_http(self._url, headers)
        async with Client(transport, raise_exceptions=True) as client:
            response = await client.call_tool(
                tool,
                {"call": request.model_dump(mode="json", exclude={"tool"})},
            )
        if response.is_error or not isinstance(response.structured_content, dict):
            raise RuntimeError(f"MCP broker rejected {tool}")
        return ToolResult.model_validate(response.structured_content)

    async def _capability(
        self, run: RotationRun, request: ToolRequest, approval_id: str | None
    ) -> str:
        request_hash = request_digest(request.tool, request.payload)
        action_hash = request_hash
        if approval_id is not None:
            approval = await self._catalog.get(
                FirestorePaths.approval(run.organisation_id, approval_id), Approval
            )
            action = await self._catalog.get(
                FirestorePaths.action(run.organisation_id, approval.action_id),
                ProtectedAction,
            )
            from policy import digest

            action_hash = digest(action)
        return self._signer.mint(
            CapabilityClaims(
                organisation_id=run.organisation_id,
                run_id=run.id,
                agent_id=request.agent_id,
                tool=request.tool,
                connection_id=request.connection_id,
                stage=run.stage,
                fencing_token=run.fencing_token,
                request_digest=request_hash,
                action_digest=action_hash,
                expires_at=int((datetime.now(UTC) + timedelta(minutes=5)).timestamp()),
                nonce=request.id,
                approval_id=approval_id,
            )
        )

    async def _identity_token(self) -> str:
        return await asyncio.to_thread(id_token.fetch_id_token, Request(), self._audience)


_READ_TOOLS = frozenset(
    {
        "provider.listCredentialMetadata",
        "provider.getCredentialStatus",
        "secretStore.getVersion",
        "secretStore.testConsumerAccess",
        "runtime.inspectSecretBindings",
    }
)
