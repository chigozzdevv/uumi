from collections.abc import Mapping
from typing import Any

import pytest
from core.auth import (
    AccessControl,
    AuthenticatedIdentity,
    CompositeTokenVerifier,
    FirebaseTokenVerifier,
    Permission,
    PrincipalGrant,
    Role,
)
from core.errors import AuthenticationError, AuthorizationError

IDENTITY = AuthenticatedIdentity(
    subject="107777777777777777777",
    issuer="https://accounts.google.com",
)


class AccessRepository:
    def __init__(self, grant: PrincipalGrant | None) -> None:
        self._grant = grant

    async def get(
        self,
        organisation_id: str,
        identity: AuthenticatedIdentity,
    ) -> PrincipalGrant | None:
        if organisation_id != "org_one" or identity != IDENTITY:
            return None
        return self._grant


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_viewer_can_read_but_cannot_mutate() -> None:
    grant = PrincipalGrant(
        subject=IDENTITY.subject,
        roles=frozenset({Role.VIEWER}),
    )
    access = AccessControl(AccessRepository(grant))

    await access.require(IDENTITY, "org_one", Permission.RUN_READ)
    await access.require(IDENTITY, "org_one", Permission.AGENT_READ)
    permissions = await access.permissions(IDENTITY, "org_one")
    assert Permission.RUN_READ in permissions
    assert Permission.NOTIFICATION_WRITE not in permissions

    with pytest.raises(AuthorizationError, match=r"run\.write"):
        await access.require(IDENTITY, "org_one", Permission.RUN_WRITE)

    with pytest.raises(AuthorizationError, match=r"agent\.write"):
        await access.require(IDENTITY, "org_one", Permission.AGENT_WRITE)


@pytest.mark.anyio
async def test_disabled_principal_has_no_access() -> None:
    grant = PrincipalGrant(
        subject=IDENTITY.subject,
        roles=frozenset({Role.ADMINISTRATOR}),
        enabled=False,
    )

    with pytest.raises(AuthorizationError, match="not enabled"):
        await AccessControl(AccessRepository(grant)).require(
            IDENTITY,
            "org_one",
            Permission.RUN_WRITE,
        )


def test_actor_identity_is_stable_and_contract_safe() -> None:
    assert IDENTITY.actor_id == IDENTITY.actor_id
    assert IDENTITY.actor_id.startswith("actor_")
    assert len(IDENTITY.actor_id) == 38


class StubFirebase(FirebaseTokenVerifier):
    def __init__(self, project_id: str, claims: dict[str, Any] | None = None) -> None:
        super().__init__(project_id)
        self._claims = claims

    def _verify(self, token: str) -> Mapping[str, Any]:
        if self._claims is None:
            raise ValueError("invalid signature")
        return self._claims


class StubVerifier:
    def __init__(self, identity: AuthenticatedIdentity | None) -> None:
        self._identity = identity

    async def verify(self, token: str) -> AuthenticatedIdentity:
        if self._identity is None:
            raise AuthenticationError("identity token is invalid")
        return self._identity


GCIP_CLAIMS = {
    "iss": "https://securetoken.google.com/firekey-project",
    "sub": "gcip-user-one",
    "email": "chigozie@acme.example",
    "email_verified": True,
    "name": "Chigozie Okafor",
    "firebase": {"sign_in_provider": "google.com"},
}

GCIP_IDENTITY = AuthenticatedIdentity(
    subject="gcip-user-one",
    issuer="https://securetoken.google.com/firekey-project",
    email="chigozie@acme.example",
    email_verified=True,
    display_name="Chigozie Okafor",
    connected_via="Google",
)


@pytest.mark.anyio
async def test_identity_platform_token_maps_to_identity() -> None:
    verifier = StubFirebase("firekey-project", GCIP_CLAIMS)

    identity = await verifier.verify("user-token")

    assert identity == GCIP_IDENTITY
    assert identity.actor_id.startswith("actor_")


@pytest.mark.anyio
async def test_identity_platform_rejects_foreign_project_issuer() -> None:
    claims = {**GCIP_CLAIMS, "iss": "https://securetoken.google.com/other-project"}
    verifier = StubFirebase("firekey-project", claims)

    with pytest.raises(AuthenticationError, match="issuer is invalid"):
        await verifier.verify("user-token")


@pytest.mark.anyio
async def test_identity_platform_rejects_invalid_signature() -> None:
    verifier = StubFirebase("firekey-project")

    with pytest.raises(AuthenticationError, match="identity platform token is invalid"):
        await verifier.verify("forged-token")


@pytest.mark.anyio
async def test_composite_falls_through_to_the_next_verifier() -> None:
    verifier = CompositeTokenVerifier(
        (StubVerifier(None), StubVerifier(IDENTITY)),
    )

    identity = await verifier.verify("workload-token")

    assert identity == IDENTITY


@pytest.mark.anyio
async def test_composite_rejects_when_every_verifier_fails() -> None:
    verifier = CompositeTokenVerifier((StubVerifier(None), StubVerifier(None)))

    with pytest.raises(AuthenticationError, match="identity token is invalid"):
        await verifier.verify("unknown-token")


def test_composite_requires_at_least_one_verifier() -> None:
    with pytest.raises(ValueError, match="at least one verifier"):
        CompositeTokenVerifier(())
