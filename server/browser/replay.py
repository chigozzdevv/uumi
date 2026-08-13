import hashlib
from collections.abc import Callable
from datetime import datetime

from broker.evidence import GcsEvidenceSink
from contracts import BrowserSession, ReplayCheckpoint, Selector

from browser.driver import BrowserDriver
from browser.service import BrowserService


class ReplayRecorder:
    def __init__(
        self,
        driver: BrowserDriver,
        sessions: BrowserService,
        evidence: GcsEvidenceSink,
        clock: Callable[[], datetime],
        id_factory: Callable[[str], str],
    ) -> None:
        self._driver = driver
        self._sessions = sessions
        self._evidence = evidence
        self._clock = clock
        self._id = id_factory

    async def record(
        self,
        session: BrowserSession,
        action: str,
        masked_selectors: tuple[Selector, ...] = (),
        safety: tuple[str, ...] = (),
    ) -> ReplayCheckpoint:
        frame = await self._driver.screenshot(session, masked_selectors)
        evidence = await self._evidence.store(
            session.organisation_id,
            session.run_id,
            "browser-replay-frame",
            frame,
            "image/png",
            self._clock(),
        )
        checkpoint = ReplayCheckpoint(
            id=self._id("checkpoint"),
            organisation_id=session.organisation_id,
            session_id=session.id,
            sequence=session.step_count,
            url=self._driver.url,
            action=action,
            image_reference=evidence.resource,
            image_digest=hashlib.sha256(frame).hexdigest(),
            safety=safety,
            human_takeover=session.takeover_subject is not None,
            recorded_at=self._clock(),
        )
        return await self._sessions.checkpoint(session, checkpoint)
