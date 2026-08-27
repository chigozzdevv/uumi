import os

from google.adk.models import Gemini

# Model calls use the regional endpoint registered with Agent Gateway.
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
