import re
from datetime import datetime
from typing import Any, Protocol

from contracts import TimedText, VideoShot, WalkthroughAnalysis

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


class VideoIntelligenceConnector:
    def __init__(self, client: GoogleClient) -> None:
        self._client = client

    async def start(self, resource: str) -> str:
        value = await self._client.request(
            "POST",
            "https://videointelligence.googleapis.com/v1/videos:annotate",
            json={
                "inputUri": resource,
                "features": ["SPEECH_TRANSCRIPTION", "TEXT_DETECTION", "SHOT_CHANGE_DETECTION"],
                "videoContext": {
                    "speechTranscriptionConfig": {
                        "languageCode": "en-US",
                        "enableAutomaticPunctuation": True,
                        "filterProfanity": True,
                    }
                },
            },
        )
        name = value.get("name")
        if not isinstance(name, str) or not name:
            raise ConnectorError(
                "video-operation-invalid", "Video Intelligence returned no operation"
            )
        return name

    async def result(
        self, operation: str, source_id: str, now: datetime
    ) -> WalkthroughAnalysis | None:
        value = await self._client.request(
            "GET", f"https://videointelligence.googleapis.com/v1/{operation}"
        )
        if value.get("done") is not True:
            return None
        if value.get("error") is not None:
            raise ConnectorError("video-analysis-failed", "Video Intelligence analysis failed")
        response = value.get("response")
        if not isinstance(response, dict):
            raise ConnectorError(
                "video-response-invalid", "Video Intelligence returned no response"
            )
        results = response.get("annotationResults")
        if not isinstance(results, list) or not results:
            raise ConnectorError(
                "video-response-empty", "Video Intelligence returned no annotations"
            )
        transcript: list[TimedText] = []
        screen_text: list[TimedText] = []
        shots: list[VideoShot] = []
        redactions = 0
        for result in results:
            if not isinstance(result, dict):
                continue
            for speech in _objects(result.get("speechTranscriptions")):
                alternatives = _objects(speech.get("alternatives"))
                if not alternatives:
                    continue
                words = _objects(alternatives[0].get("words"))
                raw = alternatives[0].get("transcript")
                if not isinstance(raw, str) or not raw.strip():
                    continue
                text, count = _redact(raw)
                redactions += count
                transcript.append(
                    TimedText(
                        start_seconds=_seconds(words[0].get("startTime")) if words else 0,
                        end_seconds=_seconds(words[-1].get("endTime")) if words else 0,
                        text=text,
                    )
                )
            for annotation in _objects(result.get("textAnnotations")):
                raw = annotation.get("text")
                segments = _objects(annotation.get("segments"))
                if not isinstance(raw, str) or not raw.strip() or not segments:
                    continue
                segment = segments[0].get("segment")
                if not isinstance(segment, dict):
                    continue
                text, count = _redact(raw)
                redactions += count
                screen_text.append(
                    TimedText(
                        start_seconds=_seconds(segment.get("startTimeOffset")),
                        end_seconds=_seconds(segment.get("endTimeOffset")),
                        text=text,
                    )
                )
            for shot in _objects(result.get("shotAnnotations")):
                shots.append(
                    VideoShot(
                        start_seconds=_seconds(shot.get("startTimeOffset")),
                        end_seconds=_seconds(shot.get("endTimeOffset")),
                    )
                )
        return WalkthroughAnalysis(
            source_id=source_id,
            transcript=tuple(transcript),
            screen_text=tuple(screen_text),
            shots=tuple(shots),
            redaction_count=redactions,
            created_at=now,
        )


def _objects(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _seconds(value: object) -> float:
    if not isinstance(value, str) or not value.endswith("s"):
        return 0
    try:
        return max(float(value[:-1]), 0)
    except ValueError:
        return 0


def _redact(value: str) -> tuple[str, int]:
    patterns = (
        r"(?i)(bearer\s+)[a-z0-9._~+/=-]{16,}",
        r"(?i)((?:api[-_ ]?key|token|secret|password)\s*[:=]\s*)\S{8,}",
        r"\b[A-Za-z0-9_-]{32,}\b",
    )
    result = value
    count = 0

    def replacement(match: re.Match[str]) -> str:
        prefix = match.group(1) if match.lastindex else ""
        return f"{prefix}[redacted]"

    for pattern in patterns:
        result, changed = re.subn(pattern, replacement, result)
        count += changed
    return result.strip(), count
