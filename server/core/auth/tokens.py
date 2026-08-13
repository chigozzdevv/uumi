import asyncio
from collections.abc import Mapping
from typing import Any, Protocol

import cachecontrol
import requests
from google.auth.transport.requests import Request
from google.oauth2 import id_token

from core.auth.identity import AuthenticatedIdentity
from core.errors import AuthenticationError


class IdentityTokenVerifier(Protocol):
    async def verify(self, token: str) -> AuthenticatedIdentity: ...


class GoogleTokenVerifier:
    def __init__(self, audience: str) -> None:
        self._audience = audience
        session = cachecontrol.CacheControl(requests.Session())
        self._request = Request(session=session)

    async def verify(self, token: str) -> AuthenticatedIdentity:
        try:
            claims = await asyncio.to_thread(self._verify, token)
        except ValueError as error:
            raise AuthenticationError("identity token is invalid") from error
        return _identity(claims)

    def _verify(self, token: str) -> Mapping[str, Any]:
        claims = id_token.verify_oauth2_token(  # type: ignore[no-untyped-call]
            token,
            self._request,
            audience=self._audience,
            clock_skew_in_seconds=30,
        )
        return dict(claims)


def _identity(claims: Mapping[str, Any]) -> AuthenticatedIdentity:
    subject = claims.get("sub")
    issuer = claims.get("iss")
    email = claims.get("email")
    if not isinstance(subject, str) or not subject:
        raise AuthenticationError("identity token has no subject")
    if not isinstance(issuer, str) or not issuer:
        raise AuthenticationError("identity token has no issuer")
    if email is not None and not isinstance(email, str):
        raise AuthenticationError("identity token email is invalid")
    return AuthenticatedIdentity(subject=subject, issuer=issuer, email=email)
