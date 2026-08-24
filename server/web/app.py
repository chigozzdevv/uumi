import asyncio
import os
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Annotated, Protocol

import httpx
from core.auth import FirebaseTokenVerifier, IdentityTokenVerifier
from core.errors import AuthenticationError
from fastapi import FastAPI, Header, HTTPException, Request, Response, status
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import id_token
from telemetry import instrument

from web.config import WebSettings

TokenProvider = Callable[[str], Awaitable[str]]


class UpstreamClient(Protocol):
    async def request(
        self,
        method: str,
        url: str,
        *,
        content: bytes,
        headers: dict[str, str],
    ) -> httpx.Response: ...

    async def aclose(self) -> None: ...


class Runtime:
    def __init__(
        self,
        settings: WebSettings,
        verifier: IdentityTokenVerifier | None = None,
        tokens: TokenProvider | None = None,
        client: UpstreamClient | None = None,
    ) -> None:
        self.settings = settings
        self.verifier = verifier or FirebaseTokenVerifier(settings.project_id)
        self.tokens = tokens or _service_token
        self.client = client or httpx.AsyncClient(
            timeout=settings.upstream_timeout_seconds,
            follow_redirects=False,
        )

    async def close(self) -> None:
        await self.client.aclose()


def create_app(
    settings: WebSettings | None = None,
    verifier: IdentityTokenVerifier | None = None,
    tokens: TokenProvider | None = None,
    client: UpstreamClient | None = None,
) -> FastAPI:
    configured = settings or WebSettings(
        project_id=os.environ["UUMI_PROJECT_ID"],
        api_url=os.environ["UUMI_API_URL"],
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        runtime = Runtime(configured, verifier, tokens, client)
        app.state.runtime = runtime
        try:
            yield
        finally:
            await runtime.close()

    app = FastAPI(title="Uumi Web Gateway", docs_url=None, redoc_url=None, lifespan=lifespan)
    instrument(app, "uumi-web")

    @app.get("/health/live")
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.api_route(
        "/v1/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    )
    async def proxy(
        path: str,
        request: Request,
        authorization: Annotated[str | None, Header()] = None,
    ) -> Response:
        runtime: Runtime = request.app.state.runtime
        bearer = _bearer(authorization)
        try:
            await runtime.verifier.verify(bearer)
        except AuthenticationError as error:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(error)) from error

        body = await request.body()
        if len(body) > runtime.settings.maximum_body_bytes:
            raise HTTPException(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "request body is too large"
            )

        service_token = await runtime.tokens(runtime.settings.api_url)
        target = f"{runtime.settings.api_url.rstrip('/')}/v1/{path}"
        if request.url.query:
            target = f"{target}?{request.url.query}"
        try:
            upstream = await runtime.client.request(
                request.method,
                target,
                content=body,
                headers=_upstream_headers(request, authorization or "", service_token),
            )
        except (httpx.TimeoutException, httpx.NetworkError) as error:
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                "the Uumi API is temporarily unavailable",
            ) from error
        return Response(
            content=upstream.content,
            status_code=upstream.status_code,
            headers=_response_headers(upstream),
        )

    return app


def _bearer(authorization: str | None) -> str:
    if authorization is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "bearer identity token is required")
    scheme, separator, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not separator or not token:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "authorization must use a bearer token",
        )
    return token


def _upstream_headers(
    request: Request,
    authorization: str,
    service_token: str,
) -> dict[str, str]:
    headers = {
        "Authorization": authorization,
        "X-Serverless-Authorization": f"Bearer {service_token}",
    }
    for name in ("accept", "content-type", "idempotency-key", "if-match"):
        value = request.headers.get(name)
        if value is not None:
            headers[name] = value
    return headers


def _response_headers(response: httpx.Response) -> dict[str, str]:
    headers: dict[str, str] = {"Cache-Control": "no-store"}
    content_type = response.headers.get("content-type")
    if content_type is not None:
        headers["Content-Type"] = content_type
    retry_after = response.headers.get("retry-after")
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return headers


async def _service_token(audience: str) -> str:
    return await asyncio.to_thread(id_token.fetch_id_token, GoogleRequest(), audience)
