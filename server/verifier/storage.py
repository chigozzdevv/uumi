from contracts import Connection, ProbeDefinition, VerificationReport
from core.errors import ResourceConflictError
from core.storage import FirestoreCatalog
from core.storage.paths import FirestorePaths
from google.cloud.firestore_v1 import AsyncClient


class FirestoreVerificationRepository:
    def __init__(self, client: AsyncClient) -> None:
        self._catalog = FirestoreCatalog(client)

    async def save_probe(self, definition: ProbeDefinition) -> ProbeDefinition:
        path = FirestorePaths.probe(definition.organisation_id, definition.id)
        try:
            await self._catalog.create(path, definition)
        except ResourceConflictError as conflict:
            existing = await self._catalog.get(path, ProbeDefinition)
            if existing != definition:
                raise ResourceConflictError(
                    f"probe {definition.id} already has a different definition"
                ) from conflict
            return existing
        return definition

    async def connection(self, organisation_id: str, connection_id: str) -> Connection:
        return await self._catalog.get(
            FirestorePaths.connection(organisation_id, connection_id), Connection
        )

    async def save_report(self, report: VerificationReport) -> VerificationReport:
        path = FirestorePaths.report(report.organisation_id, report.id)
        try:
            await self._catalog.create(path, report)
        except ResourceConflictError as conflict:
            existing = await self._catalog.get(path, VerificationReport)
            if existing != report:
                raise ResourceConflictError(
                    f"verification report {report.id} already has a different result"
                ) from conflict
            return existing
        return report
