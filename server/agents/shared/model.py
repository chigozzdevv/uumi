from google.adk.models import Gemini

MODEL_ID = "gemini-3.7-flash"
MODEL_LOCATION = "us"


def managed_model() -> Gemini:
    return Gemini(
        model=MODEL_ID,
        client_kwargs={"vertexai": True, "location": MODEL_LOCATION},
    )
