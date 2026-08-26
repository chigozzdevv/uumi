from google.auth.credentials import Credentials
from google.cloud.firestore_v1 import Client
from google.cloud.firestore_v1.services.firestore import FirestoreClient


def rest_client(project: str, database: str, credentials: Credentials | None = None) -> Client:
    client = Client(project=project, database=database, credentials=credentials)
    # Agent Gateway registers Firestore as HTTP/JSON; gRPC is intentionally not allowed through it.
    client._firestore_api_internal = FirestoreClient(
        credentials=client._credentials,
        transport="rest",
    )
    return client
