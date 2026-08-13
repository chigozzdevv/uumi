import os

import vertexai
from google.adk.apps import App
from vertexai.agent_engines import AdkApp


def managed_app(app: App) -> AdkApp:
    vertexai.init(
        project=os.environ.get("GOOGLE_CLOUD_PROJECT", "firekey-local"),
        location=os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"),
    )
    return AdkApp(app=app, enable_tracing=True)
