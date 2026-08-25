from collections.abc import AsyncGenerator

import pytest
from google.cloud.firestore_v1 import AsyncClient


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="session")
async def firestore_client() -> AsyncGenerator[AsyncClient, None]:
    client = AsyncClient(project="uumi-test")
    try:
        yield client
    finally:
        # AsyncClient.close() does not close the gRPC transport used by Firestore.
        await client._firestore_api.transport.close()
