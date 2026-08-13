import hashlib
import json

from contracts import Contract


def digest(contract: Contract) -> str:
    payload = contract.model_dump(mode="json", exclude_none=True)
    canonical = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()
