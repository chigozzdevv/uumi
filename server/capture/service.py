import fnmatch
import hashlib
from collections.abc import Callable
from datetime import datetime

from browser.driver import BrowserDriver
from connectors.secrets import SecretManagerConnector
from contracts import PageCheckpoint, SecureCaptureResult, SecureField
from playwright.async_api import Locator, Page

MASK = "••••••••"


class CaptureError(Exception):
    def __init__(self, message: str, secret_reference: str | None = None) -> None:
        super().__init__(message)
        self.secret_reference = secret_reference


class SecureCapture:
    def __init__(
        self,
        page: Page,
        driver: BrowserDriver,
        secrets: SecretManagerConnector,
        clock: Callable[[], datetime],
    ) -> None:
        self._page = page
        self._driver = driver
        self._secrets = secrets
        self._clock = clock

    async def transfer(
        self,
        capture_id: str,
        organisation_id: str,
        session_id: str,
        field: SecureField,
        checkpoint: PageCheckpoint,
    ) -> SecureCaptureResult:
        await self._checkpoint(checkpoint)
        locator = await self._driver.locator(field.selector)
        provider = await self._driver.locator(field.provider_id_selector)
        raw = await _read(locator)
        provider_id = await _read(provider)
        if not raw or len(raw) > 16384:
            raise CaptureError("declared secure field is empty or exceeds the capture limit")
        if not provider_id or len(provider_id) > 256:
            raise CaptureError("declared provider identifier is empty or invalid")
        return await self._store_and_mask(
            capture_id, organisation_id, session_id, field, raw, provider_id, locator
        )

    async def transfer_supplied(
        self,
        capture_id: str,
        organisation_id: str,
        session_id: str,
        field: SecureField,
        checkpoint: PageCheckpoint,
        supplied: bytearray,
    ) -> SecureCaptureResult:
        await self._checkpoint(checkpoint)
        locator = await self._driver.locator(field.selector)
        provider = await self._driver.locator(field.provider_id_selector)
        try:
            raw = supplied.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise CaptureError("secure input is not valid UTF-8") from error
        if not raw or len(raw) > 16384:
            raise CaptureError("secure input is empty or exceeds the capture limit")
        provider_id = await _read(provider)
        if not provider_id or len(provider_id) > 256:
            raise CaptureError("declared provider identifier is empty or invalid")
        return await self._store_and_mask(
            capture_id, organisation_id, session_id, field, raw, provider_id, locator
        )

    async def _store_and_mask(
        self,
        capture_id: str,
        organisation_id: str,
        session_id: str,
        field: SecureField,
        raw: str,
        provider_id: str,
        locator: Locator,
    ) -> SecureCaptureResult:
        secret_bytes = bytearray(raw.encode())
        secret_reference: str | None = None
        try:
            from connectors.base import SecretValue

            value = SecretValue(secret_bytes)
            try:
                stored = await self._secrets.add_version(field.secret_resource, value)
            finally:
                value.clear()
            secret_reference = _string(stored.get("secret_reference"), "secret reference")
            fingerprint = hashlib.sha256(secret_bytes).hexdigest()
            masked_markup = await locator.evaluate(_MASK_SCRIPT, {"secret": raw, "mask": MASK})
            if not isinstance(masked_markup, str):
                raise CaptureError("secure field masking returned no markup", secret_reference)
            exposed = await self._page.evaluate(_EXPOSURE_SCRIPT, raw)
            if exposed is not False:
                raise CaptureError(
                    "secret remains visible in the document after masking", secret_reference
                )
            await self._clear_clipboard()
            return SecureCaptureResult(
                id=capture_id,
                organisation_id=organisation_id,
                session_id=session_id,
                field_name=field.name,
                provider_id=provider_id,
                secret_reference=secret_reference,
                fingerprint=fingerprint,
                masked_value_digest=hashlib.sha256(masked_markup.encode()).hexdigest(),
                captured_at=self._clock(),
            )
        except CaptureError:
            raise
        except Exception as error:
            raise CaptureError("secure capture transfer failed", secret_reference) from error
        finally:
            for index in range(len(secret_bytes)):
                secret_bytes[index] = 0
            raw = ""

    async def _checkpoint(self, checkpoint: PageCheckpoint) -> None:
        if not fnmatch.fnmatchcase(self._page.url, checkpoint.url_pattern):
            raise CaptureError("secure capture URL checkpoint changed")
        for text in checkpoint.required_text:
            if await self._page.get_by_text(text, exact=True).count() == 0:
                raise CaptureError(f"secure capture checkpoint text is missing: {text}")
        for text in checkpoint.forbidden_text:
            if await self._page.get_by_text(text, exact=True).count() > 0:
                raise CaptureError(f"secure capture found forbidden text: {text}")

    async def _clear_clipboard(self) -> None:
        await self._page.evaluate(
            """async () => {
              try { await navigator.clipboard.writeText(''); } catch (_) {}
            }"""
        )


async def _read(locator: Locator) -> str:
    tag = await locator.evaluate("element => element.tagName.toLowerCase()")
    if tag in {"input", "textarea"}:
        return await locator.input_value()
    value = await locator.text_content()
    return value.strip() if value else ""


def _string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise CaptureError(f"Secret Manager returned no {label}")
    return value


_MASK_SCRIPT = """
(element, values) => {
  const mask = values.mask;
  if (element instanceof HTMLInputElement || element instanceof HTMLTextAreaElement) {
    element.value = mask;
    element.removeAttribute('value');
  } else {
    element.textContent = mask;
  }
  element.dataset.firekeyCapture = 'masked';
  element.setAttribute('aria-label', 'Credential stored securely');
  const observer = new MutationObserver(() => {
    if (element instanceof HTMLInputElement || element instanceof HTMLTextAreaElement) {
      if (element.value !== mask) element.value = mask;
      element.removeAttribute('value');
    } else if (element.textContent !== mask) {
      element.textContent = mask;
    }
  });
  const options = {attributes: true, childList: true, characterData: true, subtree: true};
  observer.observe(element, options);
  return element.outerHTML;
}
"""

_EXPOSURE_SCRIPT = """
(secret) => {
  if (!secret) return true;
  if ((document.body?.innerText || '').includes(secret)) return true;
  for (const item of document.querySelectorAll('input, textarea')) {
    if ((item.value || '').includes(secret)) return true;
    if ((item.getAttribute('value') || '').includes(secret)) return true;
  }
  return false;
}
"""
