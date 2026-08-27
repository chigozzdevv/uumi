import hashlib
import re
from datetime import datetime
from urllib.parse import quote, urlsplit

from connectors.base.errors import ConnectorError
from connectors.google import GoogleRestClient
from contracts import Evidence
from core.errors import ResourceConflictError, StorageIntegrityError
from core.storage import FirestoreCatalog
from core.storage.paths import FirestorePaths
from google.cloud.firestore_v1 import AsyncClient


class GcsEvidenceSink:
    def __init__(
        self,
        client: GoogleRestClient,
        firestore: AsyncClient,
        bucket: str,
        region: str,
    ) -> None:
        if not bucket or "/" in bucket:
            raise ValueError("evidence bucket must be a bucket name")
        self._client = client
        self._catalog = FirestoreCatalog(firestore)
        self._bucket = bucket
        self._region = region

    async def store(
        self,
        organisation_id: str,
        run_id: str,
        kind: str,
        content: bytes,
        content_type: str,
        now: datetime,
    ) -> Evidence:
        content_digest = hashlib.sha256(content).hexdigest()
        identity = hashlib.sha256(
            f"{organisation_id}\0{run_id}\0{kind}\0{content_digest}".encode()
        ).hexdigest()
        evidence_id = f"evidence_{identity[:40]}"
        path = FirestorePaths.evidence(organisation_id, evidence_id)
        snapshot = await self._catalog.client.document(path).get()
        if snapshot.exists:
            data = snapshot.to_dict()
            if data is None:
                raise StorageIntegrityError(f"evidence {evidence_id} has no metadata")
            existing = Evidence.model_validate(data)
            if existing.digest != content_digest:
                raise ResourceConflictError(f"evidence {evidence_id} digest changed")
            return existing

        object_name = _evidence_object(organisation_id, run_id, kind, evidence_id)
        uploaded = await self._upload(object_name, content, content_type, content_digest)
        generation = uploaded.get("generation")
        if not isinstance(generation, str):
            raise ConnectorError("evidence-upload-invalid", "GCS returned no object generation")
        evidence = Evidence(
            id=evidence_id,
            organisation_id=organisation_id,
            kind=kind,
            resource=f"gs://{self._bucket}/{object_name}#{generation}",
            digest=content_digest,
            content_type=content_type,
            size=len(content),
            created_at=now,
            region=self._region,
        )
        try:
            await self._catalog.create(path, evidence)
        except ResourceConflictError:
            existing = await self._catalog.get(path, Evidence)
            if existing.digest != content_digest:
                raise
            return existing
        return evidence

    async def read_image(
        self,
        organisation_id: str,
        run_id: str,
        resource: str,
        expected_digest: str,
    ) -> tuple[bytes, str]:
        parsed = urlsplit(resource)
        object_name = parsed.path.lstrip("/")
        expected_prefix = f"organisations/{organisation_id}/runs/{run_id}/"
        if (
            parsed.scheme != "gs"
            or parsed.netloc != self._bucket
            or not object_name.startswith(expected_prefix)
            or re.fullmatch(r"[1-9][0-9]*", parsed.fragment) is None
        ):
            raise ResourceConflictError("browser evidence is outside the authorised run")
        response = await self._client.response(
            "GET",
            f"https://storage.googleapis.com/download/storage/v1/b/{self._bucket}/o/"
            f"{quote(object_name, safe='')}",
            params={"alt": "media", "generation": parsed.fragment},
            headers={"Accept": "image/png"},
        )
        content = response.content
        if len(content) > 10 * 1024 * 1024:
            raise StorageIntegrityError("browser image exceeds its display limit")
        if not hashlib.sha256(content).hexdigest() == expected_digest:
            raise StorageIntegrityError("browser image digest does not match its activity")
        content_type = response.headers.get("content-type", "image/png").partition(";")[0]
        if content_type != "image/png":
            raise StorageIntegrityError("browser image is not a PNG image")
        return content, content_type

    async def _upload(
        self,
        object_name: str,
        content: bytes,
        content_type: str,
        content_digest: str,
    ) -> dict[str, object]:
        url = f"https://storage.googleapis.com/upload/storage/v1/b/{self._bucket}/o"
        params = {
            "uploadType": "media",
            "name": object_name,
            "ifGenerationMatch": "0",
        }
        headers = {
            "Content-Type": content_type,
            "x-goog-meta-uumi-digest": content_digest,
        }
        try:
            return await self._client.request(
                "POST", url, params=params, headers=headers, content=content
            )
        except ConnectorError as error:
            if error.code != "google-api-412":
                raise
        metadata_url = (
            f"https://storage.googleapis.com/storage/v1/b/{self._bucket}/o/"
            f"{quote(object_name, safe='')}"
        )
        existing = await self._client.request("GET", metadata_url)
        metadata = existing.get("metadata")
        if not isinstance(metadata, dict) or metadata.get("uumi-digest") != content_digest:
            raise ResourceConflictError("immutable evidence object contains different bytes")
        return existing


def _evidence_object(organisation_id: str, run_id: str, kind: str, evidence_id: str) -> str:
    if re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", kind) is None:
        raise ValueError("evidence kind must be a lowercase hyphenated identifier")
    return f"organisations/{organisation_id}/runs/{run_id}/types/{kind}/{evidence_id}"
