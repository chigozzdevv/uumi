from typing import Any, Protocol
from urllib.parse import quote, unquote, urlparse

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

    async def inspect(self, resource: str) -> tuple[str, str, int, str, str]:
        bucket, object_name = _gcs_resource(resource)
        value = await self._client.request(
            "GET",
            f"https://storage.googleapis.com/storage/v1/b/{bucket}/o/{quote(object_name, safe='')}",
        )
        content_type = value.get("contentType")
        raw_size = value.get("size")
        checksum = value.get("crc32c")
        generation = value.get("generation")
        if not isinstance(content_type, str) or content_type not in {
            "video/mp4",
            "video/webm",
            "video/quicktime",
        }:
            raise ConnectorError("video-type-invalid", "linked video has an unsupported type")
        if not isinstance(raw_size, (str, int)):
            raise ConnectorError("video-size-invalid", "linked video has no valid size")
        try:
            size = int(raw_size)
        except ValueError as error:
            raise ConnectorError("video-size-invalid", "linked video has no valid size") from error
        if size <= 0 or size > 2_000_000_000:
            raise ConnectorError("video-size-invalid", "linked video size is outside the limit")
        if not isinstance(checksum, str) or not checksum:
            raise ConnectorError("video-checksum-missing", "linked video has no checksum")
        if not isinstance(generation, str) or not generation:
            raise ConnectorError("video-generation-missing", "linked video has no generation")
        canonical = f"gs://{bucket}/{object_name}"
        return canonical, generation, size, checksum, content_type

    async def import_video(self, resource: str, object_name: str) -> tuple[str, str, int, str, str]:
        canonical, source_generation, size, checksum, content_type = await self.inspect(resource)
        source_bucket, source_object = _gcs_resource(canonical)
        token: str | None = None
        for _attempt in range(100):
            params = {
                "ifSourceGenerationMatch": source_generation,
                "ifGenerationMatch": "0",
            }
            if token is not None:
                params["rewriteToken"] = token
            value = await self._client.request(
                "POST",
                (
                    f"https://storage.googleapis.com/storage/v1/b/{source_bucket}/o/"
                    f"{quote(source_object, safe='')}/rewriteTo/b/{self._bucket}/o/"
                    f"{quote(object_name, safe='')}"
                ),
                params=params,
                json={
                    "name": object_name,
                    "contentType": content_type,
                    "metadata": {
                        "firekey-source-resource": canonical,
                        "firekey-source-generation": source_generation,
                    },
                },
            )
            if value.get("done") is True:
                copied = value.get("resource")
                if not isinstance(copied, dict):
                    raise ConnectorError("video-import-invalid", "Cloud Storage returned no copy")
                if (
                    copied.get("name") != object_name
                    or copied.get("contentType") != content_type
                    or copied.get("size") != str(size)
                    or copied.get("crc32c") != checksum
                ):
                    raise ConnectorError(
                        "video-import-mismatch",
                        "imported video does not match its source generation",
                    )
                generation = copied.get("generation")
                if not isinstance(generation, str) or not generation:
                    raise ConnectorError(
                        "video-generation-missing", "imported video has no generation"
                    )
                return (
                    f"gs://{self._bucket}/{object_name}",
                    generation,
                    size,
                    checksum,
                    content_type,
                )
            token = value.get("rewriteToken")
            if not isinstance(token, str) or not token:
                raise ConnectorError(
                    "video-import-invalid", "Cloud Storage returned no rewrite token"
                )
        raise ConnectorError("video-import-timeout", "Cloud Storage video import did not finish")


def _gcs_resource(value: str) -> tuple[str, str]:
    parsed = urlparse(value)
    if parsed.scheme == "gs":
        bucket = parsed.netloc
        object_name = parsed.path.lstrip("/")
    elif parsed.scheme == "https" and parsed.hostname == "storage.googleapis.com":
        parts = parsed.path.lstrip("/").split("/", 1)
        bucket, object_name = parts if len(parts) == 2 else ("", "")
    elif (
        parsed.scheme == "https"
        and parsed.hostname is not None
        and parsed.hostname.endswith(".storage.googleapis.com")
    ):
        bucket = parsed.hostname.removesuffix(".storage.googleapis.com")
        object_name = parsed.path.lstrip("/")
    else:
        raise ConnectorError(
            "video-resource-invalid",
            "video links must identify an accessible Cloud Storage object",
        )
    object_name = unquote(object_name)
    if (
        not bucket
        or not object_name
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or any(part in {"", ".", ".."} for part in object_name.split("/"))
    ):
        raise ConnectorError(
            "video-resource-invalid",
            "video links must identify an accessible Cloud Storage object",
        )
    return bucket, object_name
