from datetime import UTC, datetime
from typing import Any

import pytest
from connectors.video import VideoIntelligenceConnector
from contracts import (
    TimedText,
    WalkthroughAnalysis,
    WalkthroughKind,
    WalkthroughSource,
    WalkthroughStatus,
)
from core.errors import PlaybookError
from core.playbook import WalkthroughService

NOW = datetime(2026, 8, 13, 12, tzinfo=UTC)


class Repository:
    def __init__(self) -> None:
        self.source: WalkthroughSource | None = None

    async def reserve(self, source: WalkthroughSource) -> tuple[WalkthroughSource, bool]:
        if self.source is None:
            self.source = source
            return source, True
        return self.source, False

    async def get(
        self,
        organisation_id: str,
        playbook_id: str,
        source_id: str,
    ) -> WalkthroughSource:
        assert self.source is not None
        assert (organisation_id, playbook_id, source_id) == (
            self.source.organisation_id,
            self.source.playbook_id,
            self.source.id,
        )
        return self.source

    async def replace(
        self,
        current: WalkthroughSource,
        changed: WalkthroughSource,
    ) -> WalkthroughSource:
        assert self.source == current
        assert changed.revision == current.revision + 1
        self.source = changed
        return changed


class Uploads:
    async def begin(
        self,
        object_name: str,
        content_type: str,
        size: int,
        crc32c: str,
    ) -> str:
        assert object_name.endswith("/walkthroughs/source_one/video")
        assert (content_type, size, crc32c) == ("video/mp4", 120, "ImIEBA==")
        return "https://storage.googleapis.com/upload/session"

    async def verify(
        self,
        object_name: str,
        content_type: str,
        size: int,
        crc32c: str,
    ) -> str:
        assert object_name.endswith("/walkthroughs/source_one/video")
        assert (content_type, size, crc32c) == ("video/mp4", 120, "ImIEBA==")
        return "7"


class Video:
    async def start(self, resource: str) -> str:
        assert resource.startswith("gs://walkthroughs/")
        return "operations/video-one"

    async def result(
        self,
        operation: str,
        source_id: str,
        now: datetime,
    ) -> WalkthroughAnalysis | None:
        assert operation == "operations/video-one"
        return WalkthroughAnalysis(
            source_id=source_id,
            transcript=(TimedText(start_seconds=1, end_seconds=2, text="Open settings"),),
            created_at=now,
        )


@pytest.mark.anyio
async def test_walkthrough_upload_analysis_and_ready_handoff() -> None:
    repository = Repository()
    service = WalkthroughService(
        repository,
        Uploads(),
        Video(),
        "walkthroughs",
        lambda: NOW,
    )

    source, upload_url, created = await service.begin(
        "org_one",
        "playbook_one",
        "source_one",
        "video/mp4",
        120,
        "ImIEBA==",
        "user_one",
    )
    analysing = await service.complete("org_one", "playbook_one", "source_one")
    ready = await service.refresh("org_one", "playbook_one", "source_one")

    assert created is True
    assert upload_url.startswith("https://")
    assert source.status is WalkthroughStatus.UPLOADING
    assert analysing.status is WalkthroughStatus.ANALYSING
    assert analysing.resource.endswith("#7")
    assert ready.status is WalkthroughStatus.READY
    assert ready.analysis is not None
    assert ready.analysis.transcript[0].text == "Open settings"


@pytest.mark.anyio
async def test_text_source_is_stored_as_sanitised_evidence_with_digest_only() -> None:
    repository = Repository()
    service = WalkthroughService(
        repository,
        Uploads(),
        Video(),
        "walkthroughs",
        lambda: NOW,
    )

    source, created = await service.register(
        "org_one",
        "playbook_one",
        "source_text",
        WalkthroughKind.TEXT,
        "Open settings. password=do-not-store then click Create.",
        "user_one",
    )

    assert created is True
    assert source.resource.startswith("sha256:")
    assert "do-not-store" not in source.model_dump_json()
    assert source.analysis is not None
    assert source.analysis.transcript[0].text == "Open settings. [REDACTED] then click Create."
    assert source.analysis.redaction_count == 1


@pytest.mark.anyio
async def test_link_source_rejects_credentials_or_queries_in_its_url() -> None:
    service = WalkthroughService(
        Repository(),
        Uploads(),
        Video(),
        "walkthroughs",
        lambda: NOW,
    )

    with pytest.raises(PlaybookError, match="cannot contain credentials or queries"):
        await service.register(
            "org_one",
            "playbook_one",
            "source_link",
            WalkthroughKind.LINK,
            "Open credential settings",
            "user_one",
            "https://docs.example.com/rotation?token=do-not-store",
        )


class Google:
    def __init__(self) -> None:
        self.requests: list[tuple[str, str, dict[str, Any]]] = []

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
    ) -> dict[str, Any]:
        self.requests.append((method, url, {"json": json, "expected": expected}))
        if method == "POST":
            return {"name": "operations/analysis-one"}
        return {
            "done": True,
            "response": {
                "annotationResults": [
                    {
                        "speechTranscriptions": [
                            {
                                "alternatives": [
                                    {
                                        "transcript": "Set API key: SG.secret-value-that-must-hide",
                                        "words": [
                                            {"startTime": "1.2s", "endTime": "1.4s"},
                                            {"startTime": "1.4s", "endTime": "2.8s"},
                                        ],
                                    }
                                ]
                            }
                        ],
                        "textAnnotations": [
                            {
                                "text": "Bearer abcdefghijklmnopqrstuvwxyz012345",
                                "segments": [
                                    {
                                        "segment": {
                                            "startTimeOffset": "3s",
                                            "endTimeOffset": "4s",
                                        }
                                    }
                                ],
                            }
                        ],
                        "shotAnnotations": [{"startTimeOffset": "0s", "endTimeOffset": "5s"}],
                    }
                ]
            },
        }


@pytest.mark.anyio
async def test_video_intelligence_returns_timestamped_sanitised_annotations() -> None:
    google = Google()
    connector = VideoIntelligenceConnector(google)

    operation = await connector.start("gs://walkthroughs/video")
    analysis = await connector.result(operation, "source_one", NOW)

    assert analysis is not None
    assert operation == "operations/analysis-one"
    assert analysis.transcript[0].start_seconds == 1.2
    assert analysis.transcript[0].text == "Set API key: [redacted]"
    assert analysis.screen_text[0].text == "Bearer [redacted]"
    assert analysis.redaction_count == 2
    assert analysis.shots[0].end_seconds == 5
