from contracts import AgentKind, AgentMemory, AgentRegistration, AgentSession, AgentStatus
from core.errors import ResourceNotFoundError, StorageIntegrityError
from core.storage.catalog import FirestoreCatalog
from core.storage.codec import encode
from core.storage.paths import FirestorePaths
from google.cloud.firestore_v1 import AsyncClient
from google.cloud.firestore_v1.async_transaction import AsyncTransaction, async_transactional
from google.cloud.firestore_v1.base_query import FieldFilter


class AgentRepository:
    def __init__(self, client: AsyncClient) -> None:
        self._client = client
        self._catalog = FirestoreCatalog(client)

    async def activate(self, registration: AgentRegistration) -> AgentRegistration:
        root = f"{FirestorePaths.organisation(registration.organisation_id)}/agents"
        target = self._client.document(
            FirestorePaths.agent(registration.organisation_id, registration.id)
        )
        same_kind = self._client.collection(root).where(
            filter=FieldFilter("kind", "==", registration.kind.value)
        )

        @async_transactional
        async def apply(transaction: AsyncTransaction) -> AgentRegistration:
            target_snapshot = await target.get(transaction=transaction)
            stream = await transaction.get(same_kind)
            snapshots = [snapshot async for snapshot in stream]
            active = registration
            if target_snapshot.exists:
                data = target_snapshot.to_dict()
                if data is None:
                    raise StorageIntegrityError(
                        f"resource {target_snapshot.reference.path} has no data"
                    )
                current = AgentRegistration.model_validate(data)
                if (
                    current.deployment != registration.deployment
                    or current.version != registration.version
                    or current.kind is not registration.kind
                ):
                    raise ValueError(
                        "agent registration is immutable; deploy a new registration ID"
                    )
                active = registration.model_copy(update={"registered_at": current.registered_at})

            for snapshot in snapshots:
                data = snapshot.to_dict()
                if data is None:
                    raise StorageIntegrityError(f"resource {snapshot.reference.path} has no data")
                current = AgentRegistration.model_validate(data)
                if current.id != active.id and current.status is AgentStatus.READY:
                    transaction.set(
                        snapshot.reference,
                        encode(current.model_copy(update={"status": AgentStatus.DISABLED})),
                    )

            if target_snapshot.exists:
                transaction.set(target, encode(active))
            else:
                transaction.create(target, encode(active))
            return active

        return await apply(self._client.transaction(max_attempts=5))

    async def get(self, organisation_id: str, agent_id: str) -> AgentRegistration:
        return await self._catalog.get(
            FirestorePaths.agent(organisation_id, agent_id), AgentRegistration
        )

    async def list(self, organisation_id: str) -> tuple[AgentRegistration, ...]:
        root = f"{FirestorePaths.organisation(organisation_id)}/agents"
        return await self._catalog.list(root, AgentRegistration)

    async def save_session(self, session: AgentSession) -> AgentSession:
        path = FirestorePaths.agent_session(session.organisation_id, session.id)
        try:
            current = await self._catalog.get(path, AgentSession)
        except ResourceNotFoundError:
            await self._catalog.create(path, session)
            return session
        if current.remote_session != session.remote_session:
            raise ValueError("agent session ID is already bound to another remote session")
        return current

    async def get_session(self, organisation_id: str, session_id: str) -> AgentSession:
        return await self._catalog.get(
            FirestorePaths.agent_session(organisation_id, session_id), AgentSession
        )

    async def save_memory(self, memory: AgentMemory) -> AgentMemory:
        path = FirestorePaths.agent_memory(memory.organisation_id, memory.id)
        try:
            current = await self._catalog.get(path, AgentMemory)
        except ResourceNotFoundError:
            await self._catalog.create(path, memory)
            return memory
        immutable_binding = (
            "remote_memory",
            "fact",
            "agent",
            "provenance",
            "approved_by",
            "region",
        )
        if any(getattr(current, field) != getattr(memory, field) for field in immutable_binding):
            raise ValueError("agent memory ID is immutable")
        return current

    async def list_memories(
        self, organisation_id: str, agent: AgentKind
    ) -> tuple[AgentMemory, ...]:
        root = f"{FirestorePaths.organisation(organisation_id)}/agent-memory"
        return tuple(
            item for item in await self._catalog.list(root, AgentMemory) if item.agent is agent
        )
