from datetime import UTC, datetime
from typing import Any

import pytest
from capture import CaptureError, SecureCapture
from contracts import (
    Connection,
    ConnectionAuthorization,
    ConnectionInterface,
    ConnectionRole,
    ConnectionStatus,
    PageCheckpoint,
    SecureField,
    Selector,
    SelectorKind,
)

NOW = datetime(2026, 8, 13, 12, tzinfo=UTC)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class Locator:
    def __init__(self, value: str = "one-time-key") -> None:
        self.masked = False
        self.value = value

    async def evaluate(self, script: str, values: Any = None) -> str:
        if "tagName" in script:
            return "input"
        assert values == {"secret": "one-time-key", "mask": "••••••••"}
        self.masked = True
        return '<input data-firekey-capture="masked" aria-label="Credential stored securely">'

    async def input_value(self) -> str:
        return self.value

    async def text_content(self) -> str | None:
        return None


class Text:
    async def count(self) -> int:
        return 1


class Page:
    url = "https://console.vendor.example.com/keys/created"

    def __init__(self, locator: Locator, exposed: bool = False) -> None:
        self.locator = locator
        self.exposed = exposed
        self.clipboard_cleared = False

    def get_by_text(self, text: str, exact: bool = True) -> Text:
        assert text == "Key created" and exact
        return Text()

    async def evaluate(self, script: str, value: Any = None) -> Any:
        if "navigator.clipboard" in script:
            self.clipboard_cleared = True
            return None
        assert value == "one-time-key"
        assert self.locator.masked is True
        return self.exposed


class Driver:
    def __init__(self, locator: Locator) -> None:
        self.value = locator
        self.provider = Locator("provider-key-one")

    async def locator(self, selector: Selector) -> Locator:
        return self.provider if selector.value == "new-key-id" else self.value


class Secrets:
    def __init__(self) -> None:
        self.seen: bytes | None = None

    async def add_version_for(
        self, connection: Connection, secret: str, value: Any
    ) -> dict[str, str]:
        assert connection.id == "sink_one"
        assert secret == "projects/project-one/secrets/key"
        self.seen = value.bytes()
        return {
            "secret_reference": "projects/project-one/secrets/key/versions/7",
        }


class Connections:
    async def get_connection(self, organisation_id: str, resource_id: str) -> Connection:
        return Connection(
            id=resource_id,
            organisation_id=organisation_id,
            platform="google-secret-manager",
            display_name="Production secret store",
            roles=frozenset({ConnectionRole.SECRET_STORE}),
            interface=ConnectionInterface.API,
            authorization=ConnectionAuthorization.WORKLOAD_IDENTITY,
            authorization_reference=(
                "workload-identity://capture@project-one.iam.gserviceaccount.com"
            ),
            capabilities=frozenset({"secretStore.createVersion"}),
            allowed_resources=("projects/project-one/secrets/key",),
            status=ConnectionStatus.READY,
            region="us-east1",
            created_at=NOW,
            updated_at=NOW,
        )


@pytest.mark.anyio
async def test_capture_stores_masks_checks_and_only_returns_reference() -> None:
    locator = Locator()
    page = Page(locator)
    secrets = Secrets()
    capture = SecureCapture(
        page,  # type: ignore[arg-type]
        Driver(locator),  # type: ignore[arg-type]
        secrets,  # type: ignore[arg-type]
        Connections(),
        lambda: NOW,
    )

    result = await capture.transfer(
        "capture_one",
        "org_one",
        "session_one",
        _field(),
        _checkpoint(),
        "sink_one",
        "projects/project-one/secrets/key",
    )

    assert secrets.seen == b"one-time-key"
    assert locator.masked is True
    assert page.clipboard_cleared is True
    assert result.secret_reference.endswith("/versions/7")
    assert result.provider_id == "provider-key-one"
    assert "one-time-key" not in result.model_dump_json()


@pytest.mark.anyio
async def test_capture_fails_closed_if_secret_remains_elsewhere_in_dom() -> None:
    locator = Locator()
    capture = SecureCapture(
        Page(locator, exposed=True),  # type: ignore[arg-type]
        Driver(locator),  # type: ignore[arg-type]
        Secrets(),  # type: ignore[arg-type]
        Connections(),
        lambda: NOW,
    )

    with pytest.raises(CaptureError) as raised:
        await capture.transfer(
            "capture_one",
            "org_one",
            "session_one",
            _field(),
            _checkpoint(),
            "sink_one",
            "projects/project-one/secrets/key",
        )

    assert raised.value.secret_reference is not None
    assert "remains visible" in str(raised.value)


def _field() -> SecureField:
    return SecureField(
        name="api_key",
        selector=Selector(kind=SelectorKind.TEST_ID, value="new-api-key"),
        provider_id_selector=Selector(kind=SelectorKind.TEST_ID, value="new-key-id"),
    )


def _checkpoint() -> PageCheckpoint:
    return PageCheckpoint(
        url_pattern="https://console.vendor.example.com/keys/*",
        required_text=("Key created",),
    )
