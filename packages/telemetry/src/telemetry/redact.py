from collections.abc import Mapping, Sequence
from typing import Any

REDACTED = "[REDACTED]"
SAFE_KEYS = frozenset({"credential-id", "generation-id", "secret-reference"})
SENSITIVE_KEYS = frozenset({"authorization", "cookie", "password", "secret", "token"})


def redact(value: Any) -> Any:
    if isinstance(value, bytes | bytearray):
        return REDACTED
    if isinstance(value, Mapping):
        return {
            str(key): REDACTED if _sensitive(str(key)) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [redact(item) for item in value]
    return value


def _sensitive(key: str) -> bool:
    normalised = key.lower().replace("_", "-")
    if normalised in SAFE_KEYS:
        return False
    return normalised == "api-key" or bool(SENSITIVE_KEYS.intersection(normalised.split("-")))
