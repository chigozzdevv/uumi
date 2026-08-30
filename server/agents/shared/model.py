import os

from google.adk.models import Gemini

MODEL_ID = "gemini-3.7-flash"
MODEL_LOCATIONS = frozenset({"global", "us", "eu"})


def managed_model() -> Gemini:
    project = os.environ.get("UUMI_GOOGLE_CLOUD_PROJECT")
    if not project:
        raise RuntimeError("managed agent environment is missing UUMI_GOOGLE_CLOUD_PROJECT")
    location = os.environ.get("UUMI_GOOGLE_CLOUD_MODEL_LOCATION")
    if not location:
        raise RuntimeError("managed agent environment is missing UUMI_GOOGLE_CLOUD_MODEL_LOCATION")
    if location not in MODEL_LOCATIONS:
        raise RuntimeError("managed agent environment has an unsupported model location")
    return Gemini(
        model=MODEL_ID,
        client_kwargs={
            "vertexai": True,
            "project": project,
            "location": location,
        },
    )
