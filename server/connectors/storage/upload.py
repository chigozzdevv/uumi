from typing import Any, Protocol
from urllib.parse import quote

import httpx

from connectors.base.errors import ConnectorError


class GoogleClient(Protocol):
    async def request(
        self,
        method: str,
        url: str,
        *,
        json: Any | None = None,
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        expected: frozenset[int] = frozenset({200}),
    ) -> dict[str, Any]: ...

    async def response(
        self,
        method: str,
        url: str,
        *,
        json: Any | None = None,
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        expected: frozenset[int] = frozenset({200}),
    ) -> httpx.Response: ...


class GcsUploadConnector:
    def __init__(self, client: GoogleClient, bucket: str) -> None:
        if not bucket or "/" in bucket:
            raise ValueError("walkthrough bucket must be a bucket name")
        self._client = client
        self._bucket = bucket

    async def begin(
        self,
        object_name: str,
        content_type: str,
        size: int,
        crc32c: str,
    ) -> str:
        response = await self._client.response(
            "POST",
            f"https://storage.googleapis.com/upload/storage/v1/b/{self._bucket}/o",
            params={"uploadType": "resumable", "ifGenerationMatch": "0"},
            headers={
                "X-Upload-Content-Type": content_type,
                "X-Upload-Content-Length": str(size),
            },
            json={
                "name": object_name,
                "contentType": content_type,
                "metadata": {"firekey-crc32c": crc32c},
            },
        )
        location = response.headers.get("location")
        if not location or not location.startswith("https://"):
            raise ConnectorError("upload-session-invalid", "Cloud Storage returned no upload URL")
        return str(location)

    async def verify(
        self,
        object_name: str,
        content_type: str,
        size: int,
        crc32c: str,
    ) -> str:
        value = await self._client.request(
            "GET",
            (
                f"https://storage.googleapis.com/storage/v1/b/{self._bucket}/o/"
                f"{quote(object_name, safe='')}"
            ),
        )
        if value.get("name") != object_name:
            raise ConnectorError("upload-object-mismatch", "uploaded object name changed")
        if value.get("contentType") != content_type:
            raise ConnectorError("upload-type-mismatch", "uploaded object content type changed")
        if value.get("size") != str(size):
            raise ConnectorError("upload-size-mismatch", "uploaded object size changed")
        if value.get("crc32c") != crc32c:
            raise ConnectorError("upload-checksum-mismatch", "uploaded object checksum changed")
        metadata = value.get("metadata")
        if not isinstance(metadata, dict) or metadata.get("firekey-crc32c") != crc32c:
            raise ConnectorError("upload-metadata-mismatch", "uploaded object binding changed")
        generation = value.get("generation")
        if not isinstance(generation, str) or not generation:
            raise ConnectorError("upload-generation-missing", "uploaded object has no generation")
        return generation
