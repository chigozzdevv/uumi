import inspect
from functools import wraps
from typing import Any

from google.auth.aio.transport.sessions import AsyncAuthorizedSession
from google.auth.transport.requests import AuthorizedSession

_MULTI_REGION_MTLS_HOSTS = {
    "aiplatform.eu.rep.mtls.googleapis.com": "aiplatform.eu.rep.googleapis.com",
    "aiplatform.us.rep.mtls.googleapis.com": "aiplatform.us.rep.googleapis.com",
}


def gateway_destination_url(url: str) -> str:
    for source, destination in _MULTI_REGION_MTLS_HOSTS.items():
        url = url.replace(f"://{source}/", f"://{destination}/", 1)
    return url


def install_gateway_transport() -> None:
    _install_request_rewrite(AuthorizedSession)
    _install_request_rewrite(AsyncAuthorizedSession)


def _install_request_rewrite(session_type: type[Any]) -> None:
    current = session_type.request
    if getattr(current, "_uumi_gateway_transport", False):
        return
    if inspect.iscoroutinefunction(current):

        @wraps(current)
        async def async_request(self: Any, method: str, url: str, *args: Any, **kwargs: Any) -> Any:
            return await current(self, method, gateway_destination_url(url), *args, **kwargs)

        async_request._uumi_gateway_transport = True  # type: ignore[attr-defined]
        session_type.request = async_request
        return

    @wraps(current)
    def request(self: Any, method: str, url: str, *args: Any, **kwargs: Any) -> Any:
        return current(self, method, gateway_destination_url(url), *args, **kwargs)

    request._uumi_gateway_transport = True  # type: ignore[attr-defined]
    session_type.request = request
