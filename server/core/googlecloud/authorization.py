import base64
import hashlib
from datetime import datetime
from typing import Protocol

from connectors.base import SecretValue
from connectors.base.errors import ConnectorError
from contracts import GoogleCloudOnboardingSession


class GoogleKmsClient(Protocol):
    async def request(
        self,
        method: str,
        url: str,
        *,
        json: object | None = None,
    ) -> dict[str, object]: ...


class GoogleCloudAuthorizationCipher:
    def __init__(self, client: GoogleKmsClient, key: str) -> None:
        self._client = client
        self._key = key

    async def seal(
        self,
        session: GoogleCloudOnboardingSession,
        token: SecretValue,
        expires_at: datetime,
    ) -> tuple[str, datetime]:
        response = await self._client.request(
            "POST",
            f"https://cloudkms.googleapis.com/v1/{self._key}:encrypt",
            json={
                "plaintext": base64.b64encode(token.bytes()).decode(),
                "additionalAuthenticatedData": _aad(session),
            },
        )
        ciphertext = response.get("ciphertext")
        if not isinstance(ciphertext, str) or not ciphertext:
            raise ConnectorError(
                "google-onboarding-encryption",
                "Google Cloud onboarding authorization could not be protected",
            )
        return ciphertext, min(session.expires_at, expires_at)

    async def open(self, session: GoogleCloudOnboardingSession, now: datetime) -> SecretValue:
        if (
            session.authorization_ciphertext is None
            or session.authorization_expires_at is None
            or session.authorization_expires_at <= now
        ):
            raise ConnectorError(
                "google-onboarding-expired",
                "Google Cloud authorization expired; reconnect Google Cloud",
            )
        response = await self._client.request(
            "POST",
            f"https://cloudkms.googleapis.com/v1/{self._key}:decrypt",
            json={
                "ciphertext": session.authorization_ciphertext,
                "additionalAuthenticatedData": _aad(session),
            },
        )
        plaintext = response.get("plaintext")
        if not isinstance(plaintext, str) or not plaintext:
            raise ConnectorError(
                "google-onboarding-decryption",
                "Google Cloud onboarding authorization could not be opened",
            )
        try:
            token = base64.b64decode(plaintext, validate=True)
        except ValueError:
            raise ConnectorError(
                "google-onboarding-decryption",
                "Google Cloud onboarding authorization was invalid",
            ) from None
        if not token:
            raise ConnectorError(
                "google-onboarding-decryption",
                "Google Cloud onboarding authorization was empty",
            )
        return SecretValue(token)


def _aad(session: GoogleCloudOnboardingSession) -> str:
    value = "\n".join(
        (
            "uumi-google-cloud-onboarding-v1",
            session.organisation_id,
            session.id,
            session.subject,
            session.expires_at.isoformat(),
        )
    )
    return base64.b64encode(hashlib.sha256(value.encode()).digest()).decode()
