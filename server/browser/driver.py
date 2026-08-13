import fnmatch
from collections.abc import Iterable
from urllib.parse import urlparse

from contracts import (
    BrowserAction,
    BrowserActionKind,
    BrowserPolicy,
    BrowserSession,
    Selector,
    SelectorKind,
)
from core.errors import ResourceConflictError
from playwright.async_api import Locator, Page, Request, Route


class BrowserDriver:
    def __init__(self, page: Page, policy: BrowserPolicy) -> None:
        self._page = page
        self._policy = policy

    @property
    def url(self) -> str:
        return self._page.url

    async def enforce_egress(self) -> None:
        await self._page.route("**/*", self._route)

    async def execute(self, action: BrowserAction) -> None:
        if action.kind not in self._policy.allowed_actions:
            raise ResourceConflictError("browser action is excluded by policy")
        if action.kind is BrowserActionKind.NAVIGATE:
            assert action.url is not None
            self.validate_url(action.url)
            await self._page.goto(action.url, wait_until="domcontentloaded")
        elif action.kind is BrowserActionKind.CLICK:
            await (await self.locator(_selector(action))).click()
        elif action.kind is BrowserActionKind.TYPE:
            assert action.value is not None
            await (await self.locator(_selector(action))).fill(action.value)
        elif action.kind is BrowserActionKind.SELECT:
            assert action.value is not None
            await (await self.locator(_selector(action))).select_option(action.value)
        elif action.kind is BrowserActionKind.SCROLL:
            value = _bounded_integer(action.value, -2000, 2000)
            await self._page.mouse.wheel(0, value)
        elif action.kind is BrowserActionKind.KEY:
            if action.value not in _KEYS:
                raise ResourceConflictError("browser key is not in the safe key allowlist")
            await self._page.keyboard.press(action.value)
        elif action.kind is BrowserActionKind.WAIT:
            seconds = _bounded_integer(action.value, 0, 10)
            await self._page.wait_for_timeout(seconds * 1000)
        else:
            raise ResourceConflictError(f"browser action {action.kind.value} is unsupported")
        await self._validate_checkpoint(action)

    async def screenshot(
        self,
        session: BrowserSession,
        masked_selectors: Iterable[Selector] = (),
    ) -> bytes:
        if session.model_paused or session.recording_paused:
            raise ResourceConflictError("browser screenshot barriers are armed")
        masks = [
            await self.locator(selector, require_unique=False) for selector in masked_selectors
        ]
        return await self._page.screenshot(type="png", animations="disabled", mask=masks)

    async def validate_coordinate(self, selector: Selector, x: int, y: int) -> None:
        if not 0 <= x <= 999 or not 0 <= y <= 999:
            raise ResourceConflictError("model coordinate is outside the normalised viewport")
        locator = await self.locator(selector)
        box = await locator.bounding_box()
        viewport = self._page.viewport_size
        if box is None or viewport is None:
            raise ResourceConflictError("approved control has no visible viewport bounds")
        actual_x = x / 1000 * viewport["width"]
        actual_y = y / 1000 * viewport["height"]
        inside = (
            box["x"] <= actual_x <= box["x"] + box["width"]
            and box["y"] <= actual_y <= box["y"] + box["height"]
        )
        if not inside:
            raise ResourceConflictError("model coordinate does not hit the approved control")

    async def locator(self, selector: Selector, require_unique: bool = True) -> Locator:
        if selector.kind is SelectorKind.ROLE:
            locator = self._page.get_by_role(
                selector.value,  # type: ignore[arg-type]
                name=selector.name,
                exact=selector.exact,
            )
        elif selector.kind is SelectorKind.LABEL:
            locator = self._page.get_by_label(selector.value, exact=selector.exact)
        elif selector.kind is SelectorKind.TEXT:
            locator = self._page.get_by_text(selector.value, exact=selector.exact)
        elif selector.kind is SelectorKind.TEST_ID:
            locator = self._page.get_by_test_id(selector.value)
        elif selector.kind is SelectorKind.CSS:
            locator = self._page.locator(selector.value)
        else:
            raise ResourceConflictError(f"selector kind {selector.kind.value} is unsupported")
        count = await locator.count()
        if require_unique and count != 1:
            raise ResourceConflictError(
                f"approved selector resolved to {count} elements instead of one"
            )
        if require_unique and not await locator.is_visible():
            raise ResourceConflictError("approved selector is not visible")
        return locator

    def validate_url(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ResourceConflictError("browser navigation requires a credential-free HTTPS URL")
        hostname = parsed.hostname.lower().rstrip(".")
        if not any(_domain(hostname, value) for value in self._policy.allowed_domains):
            raise ResourceConflictError(f"browser domain {hostname} is outside the allowlist")

    async def _route(self, route: Route, request: Request) -> None:
        parsed = urlparse(request.url)
        if parsed.scheme in {"about", "blob", "data"}:
            await route.continue_()
            return
        try:
            self.validate_url(request.url)
        except ResourceConflictError:
            await route.abort("blockedbyclient")
            return
        await route.continue_()

    async def _validate_checkpoint(self, action: BrowserAction) -> None:
        self.validate_url(self._page.url)
        if action.expected_url and not fnmatch.fnmatchcase(self._page.url, action.expected_url):
            raise ResourceConflictError("browser URL does not match the approved checkpoint")
        for text in action.expected_text:
            if await self._page.get_by_text(text, exact=True).count() == 0:
                raise ResourceConflictError(f"browser checkpoint text is missing: {text}")


def _selector(action: BrowserAction) -> Selector:
    if action.selector is None:
        raise ResourceConflictError("browser action requires an approved selector")
    return action.selector


def _domain(hostname: str, pattern: str) -> bool:
    expected = pattern.lower().rstrip(".")
    if expected.startswith("*."):
        suffix = expected[2:]
        return hostname.endswith("." + suffix) and hostname != suffix
    return hostname == expected


def _bounded_integer(value: str | None, minimum: int, maximum: int) -> int:
    try:
        result = int(value or "")
    except ValueError as error:
        raise ResourceConflictError("browser action requires an integer value") from error
    if not minimum <= result <= maximum:
        raise ResourceConflictError("browser action integer is outside its safe range")
    return result


_KEYS = frozenset(
    {
        "ArrowDown",
        "ArrowLeft",
        "ArrowRight",
        "ArrowUp",
        "Backspace",
        "Enter",
        "Escape",
        "Tab",
    }
)
