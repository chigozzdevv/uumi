import hashlib
import os
import re

from fastapi import FastAPI


def create_app(api_key: str | None = None) -> FastAPI:
    secret = api_key if api_key is not None else os.environ["RESEND_API_KEY"]
    if re.fullmatch(r"re_[A-Za-z0-9_-]{16,}", secret) is None:
        raise ValueError("Resend API key is invalid")
    fingerprint = hashlib.sha256(secret.encode()).hexdigest()[:16]
    app = FastAPI(title="Uumi Resend Demo", docs_url=None, redoc_url=None)

    @app.get("/health/live")
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready")
    async def ready() -> dict[str, str]:
        return {"status": "ready", "credential_fingerprint": fingerprint}

    return app
