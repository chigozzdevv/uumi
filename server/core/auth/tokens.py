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


class IapTokenVerifier:
    def __init__(self, audience: str) -> None:
        self._audience = audience
        session = cachecontrol.CacheControl(requests.Session())
        self._request = Request(session=session)

    async def verify(self, token: str) -> AuthenticatedIdentity:
        try:
            claims = await asyncio.to_thread(
                id_token.verify_token,
                token,
                self._request,
                self._audience,
                "https://www.gstatic.com/iap/verify/public_key",
            )
        except ValueError as error:
            raise AuthenticationError("IAP identity token is invalid") from error
        if claims.get("iss") != "https://cloud.google.com/iap":
            raise AuthenticationError("IAP identity token issuer is invalid")
        return _identity(claims)


class FirebaseTokenVerifier:
    def __init__(self, project_id: str) -> None:
        self._project_id = project_id
        session = cachecontrol.CacheControl(requests.Session())
        self._request = Request(session=session)

    async def verify(self, token: str) -> AuthenticatedIdentity:
        try:
            claims = await asyncio.to_thread(self._verify, token)
        except ValueError as error:
            raise AuthenticationError("identity platform token is invalid") from error
        if claims.get("iss") != f"https://securetoken.google.com/{self._project_id}":
            raise AuthenticationError("identity platform token issuer is invalid")
        return _identity(claims)

    def _verify(self, token: str) -> Mapping[str, Any]:
        claims = id_token.verify_firebase_token(  # type: ignore[no-untyped-call]
            token,
            self._request,
            audience=self._project_id,
            clock_skew_in_seconds=30,
        )
        return dict(claims)


class CompositeTokenVerifier:
    def __init__(self, verifiers: tuple[IdentityTokenVerifier, ...]) -> None:
        if not verifiers:
            raise ValueError("composite verifier requires at least one verifier")
        self._verifiers = verifiers

    async def verify(self, token: str) -> AuthenticatedIdentity:
        last: AuthenticationError | None = None
        for verifier in self._verifiers:
            try:
                return await verifier.verify(token)
            except AuthenticationError as error:
                last = error
        raise AuthenticationError("identity token is invalid") from last


def _identity(claims: Mapping[str, Any]) -> AuthenticatedIdentity:
    subject = claims.get("sub")
    issuer = claims.get("iss")
    email = claims.get("email")
    email_verified = claims.get("email_verified", False)
    display_name = claims.get("name")
    if not isinstance(subject, str) or not subject:
        raise AuthenticationError("identity token has no subject")
    if not isinstance(issuer, str) or not issuer:
        raise AuthenticationError("identity token has no issuer")
    if email is not None and not isinstance(email, str):
        raise AuthenticationError("identity token email is invalid")
    if not isinstance(email_verified, bool):
        raise AuthenticationError("identity token email verification is invalid")
    if display_name is not None and not isinstance(display_name, str):
        raise AuthenticationError("identity token display name is invalid")
    return AuthenticatedIdentity(
        subject=subject,
        issuer=issuer,
        email=email,
        email_verified=email_verified,
        display_name=display_name,
        connected_via=_provider(claims, issuer),
    )


def _provider(claims: Mapping[str, Any], issuer: str) -> str:
    firebase = claims.get("firebase")
    provider = firebase.get("sign_in_provider") if isinstance(firebase, Mapping) else None
    if provider == "google.com" or issuer == "https://accounts.google.com":
        return "Google"
    if provider == "password":
        return "Email"
    if isinstance(provider, str) and provider.startswith("saml."):
        return "Organisation SSO"
    if isinstance(provider, str) and provider.startswith("oidc."):
        return "Organisation SSO"
    return "Identity Platform"
