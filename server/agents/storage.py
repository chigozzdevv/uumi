from contracts import AgentKind, AgentMemory, AgentRegistration, AgentSession
from core.errors import ResourceNotFoundError
from core.storage.catalog import FirestoreCatalog
from core.storage.paths import FirestorePaths
from google.cloud.firestore_v1 import AsyncClient


class AgentRepository:
    def __init__(self, client: AsyncClient) -> None:
        self._catalog = FirestoreCatalog(client)

    async def register(self, registration: AgentRegistration) -> AgentRegistration:
        path = FirestorePaths.agent(registration.organisation_id, registration.id)
        try:
            current = await self._catalog.get(path, AgentRegistration)
        except ResourceNotFoundError:
            await self._catalog.create(path, registration)
            return registration
        if current.deployment != registration.deployment or current.version != registration.version:
            raise ValueError("agent registration is immutable; deploy a new registration ID")
        return current

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
        if current.remote_memory != memory.remote_memory or current.fact != memory.fact:
            raise ValueError("agent memory ID is immutable")
        return current

    async def list_memories(
        self, organisation_id: str, agent: AgentKind
    ) -> tuple[AgentMemory, ...]:
        root = f"{FirestorePaths.organisation(organisation_id)}/agent-memory"
        return tuple(
            item for item in await self._catalog.list(root, AgentMemory) if item.agent is agent
        )
