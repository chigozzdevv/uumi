from contracts import BrowserSession, RotationRun
from core.storage import FirestoreCatalog
from core.storage.paths import FirestorePaths
from google.cloud.firestore_v1 import AsyncClient


class FirestoreGatewayRepository:
    def __init__(self, client: AsyncClient) -> None:
        self._catalog = FirestoreCatalog(client)

    async def browser(self, organisation_id: str, session_id: str) -> BrowserSession:
        return await self._catalog.get(
            FirestorePaths.browser(organisation_id, session_id), BrowserSession
        )

    async def run(self, organisation_id: str, run_id: str) -> RotationRun:
        return await self._catalog.get(FirestorePaths.run(organisation_id, run_id), RotationRun)
