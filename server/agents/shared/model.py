import os

from google.adk.models import Gemini

MODEL_ID = "gemini-3.7-flash"
MODEL_LOCATION = "us"


def managed_model() -> Gemini:
    project = os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project:
        raise RuntimeError("managed agent environment is missing GOOGLE_CLOUD_PROJECT")
    return Gemini(
        model=MODEL_ID,
        client_kwargs={
            "vertexai": True,
            "project": project,
            "location": MODEL_LOCATION,
        },
    )
