from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from capture import SecureCapture
from contracts import (
    BrowserAction,
    BrowserActionKind,
    BrowserSession,
    BrowserStatus,
    PlaybookStep,
    SecureCaptureResult,
    Selector,
)
from core.errors import ResourceConflictError

from browser.driver import BrowserDriver
from browser.model import ComputerProposal, ComputerUseClient
from browser.service import BrowserService


@dataclass(frozen=True, slots=True)
class ProposedBrowserAction:
    action: BrowserAction
    model: ComputerProposal
    requires_confirmation: bool


class ComputerUseWorker:
    def __init__(
        self,
        model: ComputerUseClient,
        driver: BrowserDriver,
        sessions: BrowserService,
        capture: SecureCapture,
        id_factory: Callable[[str], str],
        masked_selectors: tuple[Selector, ...] = (),
    ) -> None:
        self._model = model
        self._driver = driver
        self._sessions = sessions
        self._capture = capture
        self._id = id_factory
        self._masked_selectors = masked_selectors

    async def propose(
        self,
        session: BrowserSession,
        step: PlaybookStep,
        objective: str,
        previous: ComputerProposal | None = None,
        outcome: dict[str, str | int | bool] | None = None,
    ) -> ProposedBrowserAction | None:
        if session.status is not BrowserStatus.RUNNING:
            raise ResourceConflictError("Computer Use requires a running browser session")
        frame = await self._driver.screenshot(session, self._masked_selectors)
        proposal = await self._model.propose(objective, frame, previous, outcome)
        if proposal is None:
            await self._driver.validate_step(step)
            return None
        action = await self._bind(session, step, proposal)
        return ProposedBrowserAction(
            action=action,
            model=proposal,
            requires_confirmation=step.protected or proposal.requires_confirmation,
        )

    async def execute(
        self,
        session: BrowserSession,
        proposal: ProposedBrowserAction,
        confirmed: bool,
    ) -> BrowserSession:
        if proposal.requires_confirmation and not confirmed:
            return await self._sessions.freeze(
                session.organisation_id, session.id, session.revision
            )
        authorised = await self._sessions.authorize_action(
            session.organisation_id,
            session.id,
            session.revision,
            proposal.action,
        )
        try:
            await self._driver.execute(proposal.action)
        except Exception as error:
            await self._sessions.finish_action(
                session.organisation_id,
                session.id,
                proposal.action.id,
                False,
                _error(error),
            )
            return await self._sessions.freeze(
                authorised.organisation_id, authorised.id, authorised.revision
            )
        await self._sessions.finish_action(
            session.organisation_id, session.id, proposal.action.id, True
        )
        return authorised

    async def execute_protected_capture(
        self,
        session: BrowserSession,
        proposal: ProposedBrowserAction,
        step: PlaybookStep,
        confirmed: bool,
    ) -> tuple[BrowserSession, SecureCaptureResult | None]:
        if not step.protected or step.secure_field is None or step.checkpoint is None:
            raise ResourceConflictError(
                "protected capture requires a protected step, field and checkpoint"
            )
        if not confirmed:
            return (
                await self._sessions.freeze(session.organisation_id, session.id, session.revision),
                None,
            )
        armed = await self._sessions.arm_capture(
            session.organisation_id, session.id, session.revision
        )
        authorised = await self._sessions.authorize_action(
            armed.organisation_id, armed.id, armed.revision, proposal.action
        )
        try:
            await self._driver.execute(proposal.action)
            await self._sessions.finish_action(
                session.organisation_id, session.id, proposal.action.id, True
            )
            result = await self._capture.transfer(
                self._id("capture"),
                session.organisation_id,
                session.id,
                step.secure_field,
                step.checkpoint,
                session.secret_store_connection_id,
                session.secret_resource,
            )
            resumed = await self._sessions.complete_capture(result, authorised.revision)
            return resumed, result
        except Exception as error:
            await self._finish_failure(session, proposal.action, error)
            frozen = await self._sessions.freeze(
                authorised.organisation_id, authorised.id, authorised.revision
            )
            return frozen, None

    async def _bind(
        self,
        session: BrowserSession,
        step: PlaybookStep,
        proposal: ComputerProposal,
    ) -> BrowserAction:
        selector = step.selectors[0] if step.selectors else None
        parameters = step.parameters
        kind: BrowserActionKind
        value: str | None = None
        url: str | None = None
        if proposal.name == "click":
            kind = BrowserActionKind.CLICK
            if selector is None:
                raise ResourceConflictError("approved click has no selector")
            await self._driver.validate_coordinate(
                selector,
                _argument(proposal.arguments, "x", int),
                _argument(proposal.arguments, "y", int),
            )
        elif proposal.name == "type":
            kind = BrowserActionKind.TYPE
            value = _argument(proposal.arguments, "text", str)
            if parameters.get("value") != value or selector is None:
                raise ResourceConflictError("model input differs from the approved value")
            if proposal.arguments.get("press_enter") not in {None, False}:
                raise ResourceConflictError("model input cannot add an undeclared Enter key")
        elif proposal.name == "press_key":
            kind = BrowserActionKind.KEY
            value = _argument(proposal.arguments, "key", str)
            if parameters.get("key") != value:
                raise ResourceConflictError("model key differs from the approved key")
        elif proposal.name == "scroll":
            kind = BrowserActionKind.SCROLL
            if selector is None:
                raise ResourceConflictError("approved scroll has no selector")
            await self._driver.validate_coordinate(
                selector,
                _argument(proposal.arguments, "x", int),
                _argument(proposal.arguments, "y", int),
            )
            magnitude = _argument(proposal.arguments, "magnitude_in_pixels", int, 300)
            direction = _argument(proposal.arguments, "direction", str)
            if direction not in {"up", "down"}:
                raise ResourceConflictError("horizontal Computer Use scroll is disabled")
            value = str(-magnitude if direction == "up" else magnitude)
        elif proposal.name == "wait":
            kind = BrowserActionKind.WAIT
            value = str(_argument(proposal.arguments, "seconds", int, 1))
        else:
            raise ResourceConflictError(f"model action {proposal.name} is unsupported")
        return BrowserAction(
            id=self._id("browser-action"),
            session_id=session.id,
            kind=kind,
            selector=selector,
            value=value,
            url=url,
            protected=step.protected,
            expected_url=step.checkpoint.url_pattern if step.checkpoint else None,
            expected_text=step.checkpoint.required_text if step.checkpoint else (),
            forbidden_text=step.checkpoint.forbidden_text if step.checkpoint else (),
            fencing_token=session.fencing_token,
        )

    async def _finish_failure(
        self, session: BrowserSession, action: BrowserAction, error: Exception
    ) -> None:
        with suppress(ResourceConflictError):
            await self._sessions.finish_action(
                session.organisation_id,
                session.id,
                action.id,
                False,
                _error(error),
            )


def _argument[T](arguments: dict[str, Any], key: str, kind: type[T], default: T | None = None) -> T:
    value = arguments.get(key, default)
    if not isinstance(value, kind) or isinstance(value, bool):
        raise ResourceConflictError(f"Computer Use argument {key} is invalid")
    return value


def _error(error: Exception) -> str:
    return f"{type(error).__name__}: {error}".replace("\n", " ")[:1024]
