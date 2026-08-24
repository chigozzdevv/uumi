import httpx
import pytest
from publisher import app as publisher_app
from publisher.app import PublishResponse, app
from publisher.config import PublisherSettings
from pydantic import ValidationError

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class Runtime:
    async def publish(self) -> PublishResponse:
        return PublishResponse(claimed=3, published=2, failed=1, dead_lettered=0)


async def test_publish_endpoint_reports_delivery_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(publisher_app, "_runtime", Runtime())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/publish")

    assert response.status_code == 200
    assert response.json() == {
        "claimed": 3,
        "published": 2,
        "failed": 1,
        "dead_lettered": 0,
    }


async def test_live_endpoint_has_no_provider_dependency() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_settings_require_lease_longer_than_publish_timeout() -> None:
    with pytest.raises(ValidationError, match="lease must exceed"):
        PublisherSettings(
            project_id="uumi-test",
            region="us-east1",
            publish_timeout_seconds=20,
            outbox_lease_seconds=20,
        )
