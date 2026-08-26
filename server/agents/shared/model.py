import os

from google.adk.models import Gemini

# Agent Identity routes Google API calls through the Gateway's regional mTLS endpoint;
# Vertex's US multi-region model endpoint has no corresponding mTLS hostname.
MODEL_ID = "gemini-2.5-flash"


def managed_model() -> Gemini:
    project = os.environ.get("UUMI_GOOGLE_CLOUD_PROJECT")
    if not project:
        raise RuntimeError("managed agent environment is missing UUMI_GOOGLE_CLOUD_PROJECT")
    location = os.environ.get("UUMI_GOOGLE_CLOUD_LOCATION")
    if not location:
        raise RuntimeError("managed agent environment is missing UUMI_GOOGLE_CLOUD_LOCATION")
    return Gemini(
        model=MODEL_ID,
        client_kwargs={
            "vertexai": True,
            "project": project,
            "location": location,
        },
    )
