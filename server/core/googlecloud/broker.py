import asyncio

from contracts import Connection
from google.auth.transport.requests import Request
from google.oauth2 import id_token
from mcp import Client

from core.errors import ResourceConflictError
from core.ids import new_id
from core.mcpclient import authenticated_streamable_http


class GoogleCloudBrokerValidator:
    def __init__(self, url: str) -> None:
        self._url = url.rstrip("/") + "/mcp"
        self._audience = url.rstrip("/")

    async def validate(self, connection: Connection) -> None:
        try:
            headers = {"Authorization": f"Bearer {await self._identity_token()}"}
            transport = authenticated_streamable_http(self._url, headers)
            async with Client(transport, raise_exceptions=True) as client:
                response = await client.call_tool(
                    "connection.validateGoogleCloud",
                    {
                        "call": {
                            "id": new_id("verify"),
                            "organisation_id": connection.organisation_id,
                            "connection_id": connection.id,
                        }
                    },
                )
        except Exception as error:
            raise ResourceConflictError("Google Cloud access verification failed") from error
        if response.is_error or not isinstance(response.structured_content, dict):
            raise ResourceConflictError("Google Cloud access is incomplete")
        if response.structured_content.get("ready") is not True:
            raise ResourceConflictError("Google Cloud access is incomplete")

    async def _identity_token(self) -> str:
        return await asyncio.to_thread(id_token.fetch_id_token, Request(), self._audience)
