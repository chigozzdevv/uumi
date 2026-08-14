import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from contracts import (
    Notification,
    NotificationDelivery,
    NotificationEndpoint,
    NotificationState,
)
from google.cloud.firestore_v1 import AsyncClient
from google.cloud.firestore_v1.async_transaction import AsyncTransaction, async_transactional
from google.cloud.firestore_v1.base_document import DocumentSnapshot
from google.cloud.firestore_v1.base_query import FieldFilter

from core.errors import ResourceConflictError, ResourceNotFoundError, StorageIntegrityError
from core.storage.codec import encode
from core.storage.paths import FirestorePaths


@dataclass(frozen=True, slots=True)
class NotificationClaim:
    path: str
    delivery: NotificationDelivery
    notification: Notification
    endpoint: NotificationEndpoint


class FirestoreNotificationRepository:
    def __init__(self, client: AsyncClient) -> None:
        self._client = client

    async def emit(self, notification: Notification) -> tuple[Notification, bool]:
        notification_ref = self._client.document(
            FirestorePaths.notification(notification.organisation_id, notification.id)
        )
        endpoints = await self.list_endpoints(notification.organisation_id)
        selected = tuple(
            endpoint
            for endpoint in endpoints
            if endpoint.enabled and notification.kind in endpoint.event_kinds
        )

        @async_transactional
        async def apply(transaction: AsyncTransaction) -> tuple[Notification, bool]:
            existing = await notification_ref.get(transaction=transaction)
            if existing.exists:
                current = Notification.model_validate(_data(existing))
                replay = notification.model_copy(
                    update={
                        "created_at": current.created_at,
                        "read_at": current.read_at,
                        "revision": current.revision,
                    }
                )
                if current != replay:
                    raise ResourceConflictError(
                        f"notification {notification.id} was replayed with changes"
                    )
                return current, False
            current_endpoints: list[NotificationEndpoint] = []
            for endpoint in selected:
                endpoint_ref = self._client.document(
                    FirestorePaths.notification_endpoint(notification.organisation_id, endpoint.id)
                )
                endpoint_snapshot = await endpoint_ref.get(transaction=transaction)
                if not endpoint_snapshot.exists:
                    raise StorageIntegrityError("notification endpoint disappeared during emission")
                current_endpoint = NotificationEndpoint.model_validate(_data(endpoint_snapshot))
                if current_endpoint != endpoint:
                    raise ResourceConflictError(
                        "notification endpoint changed during event emission; retry safely"
                    )
                current_endpoints.append(current_endpoint)
            transaction.create(notification_ref, encode(notification))
            for endpoint in current_endpoints:
                delivery = _delivery(notification, endpoint)
                delivery_ref = self._client.document(
                    FirestorePaths.notification_delivery(
                        notification.organisation_id,
                        notification.id,
                        delivery.id,
                    )
                )
                transaction.create(delivery_ref, encode(delivery))
            return notification, True

        return await apply(self._client.transaction(max_attempts=5))

    async def list_notifications(
        self, organisation_id: str, limit: int
    ) -> tuple[Notification, ...]:
        root = FirestorePaths.notification_collection(organisation_id)
        values: list[Notification] = []
        query = (
            self._client.collection(root)
            .order_by("created_at", direction="DESCENDING")
            .limit(min(max(limit, 1), 200))
        )
        async for snapshot in query.stream():
            values.append(Notification.model_validate(_data(snapshot)))
        return tuple(values)

    async def mark_read(
        self,
        organisation_id: str,
        notification_id: str,
        expected_revision: int,
        read_at: datetime,
    ) -> Notification:
        reference = self._client.document(
            FirestorePaths.notification(organisation_id, notification_id)
        )

        @async_transactional
        async def apply(transaction: AsyncTransaction) -> Notification:
            snapshot = await reference.get(transaction=transaction)
            if not snapshot.exists:
                raise ResourceNotFoundError(f"notification {notification_id} was not found")
            current = Notification.model_validate(_data(snapshot))
            if current.read_at is not None:
                return current
            if current.revision != expected_revision:
                raise ResourceConflictError(
                    f"notification expected revision {expected_revision}, found {current.revision}"
                )
            changed = current.model_copy(
                update={"read_at": read_at, "revision": current.revision + 1}
            )
            transaction.set(reference, encode(changed))
            return changed

        return await apply(self._client.transaction(max_attempts=5))

    async def register_endpoint(self, endpoint: NotificationEndpoint) -> NotificationEndpoint:
        reference = self._client.document(
            FirestorePaths.notification_endpoint(endpoint.organisation_id, endpoint.id)
        )

        @async_transactional
        async def apply(transaction: AsyncTransaction) -> NotificationEndpoint:
            snapshot = await reference.get(transaction=transaction)
            if snapshot.exists:
                current = NotificationEndpoint.model_validate(_data(snapshot))
                replay = endpoint.model_copy(
                    update={
                        "created_at": current.created_at,
                        "updated_at": current.updated_at,
                        "revision": current.revision,
                    }
                )
                if current == replay:
                    return current
                raise ResourceConflictError(f"notification endpoint {endpoint.id} already exists")
            transaction.create(reference, encode(endpoint))
            return endpoint

        return await apply(self._client.transaction(max_attempts=5))

    async def list_endpoints(self, organisation_id: str) -> tuple[NotificationEndpoint, ...]:
        root = FirestorePaths.notification_endpoint_collection(organisation_id)
        values: list[NotificationEndpoint] = []
        async for snapshot in self._client.collection(root).limit(100).stream():
            values.append(NotificationEndpoint.model_validate(_data(snapshot)))
        return tuple(values)

    async def set_endpoint_enabled(
        self,
        organisation_id: str,
        endpoint_id: str,
        expected_revision: int,
        enabled: bool,
        updated_at: datetime,
    ) -> NotificationEndpoint:
        reference = self._client.document(
            FirestorePaths.notification_endpoint(organisation_id, endpoint_id)
        )

        @async_transactional
        async def apply(transaction: AsyncTransaction) -> NotificationEndpoint:
            snapshot = await reference.get(transaction=transaction)
            if not snapshot.exists:
                raise ResourceNotFoundError(f"notification endpoint {endpoint_id} was not found")
            current = NotificationEndpoint.model_validate(_data(snapshot))
            if current.revision != expected_revision:
                raise ResourceConflictError(
                    f"notification endpoint expected revision {expected_revision}, "
                    f"found {current.revision}"
                )
            if current.enabled is enabled:
                return current
            changed = current.model_copy(
                update={
                    "enabled": enabled,
                    "updated_at": updated_at,
                    "revision": current.revision + 1,
                }
            )
            transaction.set(reference, encode(changed))
            return changed

        return await apply(self._client.transaction(max_attempts=5))

    async def claim(
        self,
        owner_id: str,
        now: datetime,
        lease_duration: timedelta,
        limit: int,
    ) -> tuple[NotificationClaim, ...]:
        query = (
            self._client.collection_group("notification-deliveries")
            .where(
                filter=FieldFilter(
                    "state",
                    "in",
                    [NotificationState.PENDING.value, NotificationState.SENDING.value],
                )
            )
            .where(filter=FieldFilter("available_at", "<=", now))
            .order_by("available_at")
            .limit(min(max(limit * 4, 40), 400))
        )
        claims: list[NotificationClaim] = []
        async for snapshot in query.stream():
            claim = await self._claim_one(snapshot.reference.path, owner_id, now, lease_duration)
            if claim is not None:
                claims.append(claim)
            if len(claims) == limit:
                break
        return tuple(claims)

    async def mark_sent(
        self,
        claim: NotificationClaim,
        owner_id: str,
        receipt: str,
        sent_at: datetime,
    ) -> None:
        await self._finish(
            claim,
            owner_id,
            NotificationState.SENT,
            sent_at,
            receipt,
            None,
            sent_at,
        )

    async def mark_failed(
        self,
        claim: NotificationClaim,
        owner_id: str,
        error: str,
        available_at: datetime,
        terminal: bool,
    ) -> None:
        await self._finish(
            claim,
            owner_id,
            NotificationState.FAILED if terminal else NotificationState.PENDING,
            None,
            None,
            error,
            available_at,
        )

    async def _claim_one(
        self,
        path: str,
        owner_id: str,
        now: datetime,
        lease_duration: timedelta,
    ) -> NotificationClaim | None:
        reference = self._client.document(path)

        @async_transactional
        async def apply(transaction: AsyncTransaction) -> NotificationClaim | None:
            snapshot = await reference.get(transaction=transaction)
            current = NotificationDelivery.model_validate(_data(snapshot))
            pending = current.state is NotificationState.PENDING and current.available_at <= now
            abandoned = (
                current.state is NotificationState.SENDING
                and current.lease_expires_at is not None
                and current.lease_expires_at <= now
            )
            if not pending and not abandoned:
                return None
            notification_ref = self._client.document(
                FirestorePaths.notification(current.organisation_id, current.notification_id)
            )
            endpoint_ref = self._client.document(
                FirestorePaths.notification_endpoint(current.organisation_id, current.endpoint_id)
            )
            notification_snapshot = await notification_ref.get(transaction=transaction)
            endpoint_snapshot = await endpoint_ref.get(transaction=transaction)
            if not notification_snapshot.exists or not endpoint_snapshot.exists:
                raise StorageIntegrityError("notification delivery lost its source records")
            notification = Notification.model_validate(_data(notification_snapshot))
            endpoint = NotificationEndpoint.model_validate(_data(endpoint_snapshot))
            claimed = current.model_copy(
                update={
                    "state": NotificationState.SENDING,
                    "attempts": current.attempts + 1,
                    "lease_owner": owner_id,
                    "lease_expires_at": now + lease_duration,
                }
            )
            transaction.set(reference, encode(claimed))
            return NotificationClaim(path, claimed, notification, endpoint)

        return await apply(self._client.transaction(max_attempts=5))

    async def _finish(
        self,
        claim: NotificationClaim,
        owner_id: str,
        state: NotificationState,
        sent_at: datetime | None,
        receipt: str | None,
        error: str | None,
        available_at: datetime,
    ) -> None:
        reference = self._client.document(claim.path)

        @async_transactional
        async def apply(transaction: AsyncTransaction) -> None:
            snapshot = await reference.get(transaction=transaction)
            current = NotificationDelivery.model_validate(_data(snapshot))
            if current.state is NotificationState.SENT:
                if current.provider_receipt == receipt:
                    return
                raise ResourceConflictError("notification delivery already has another receipt")
            if current.state is not NotificationState.SENDING or current.lease_owner != owner_id:
                raise ResourceConflictError("notification delivery lease is not owned")
            changed = current.model_copy(
                update={
                    "state": state,
                    "available_at": available_at,
                    "lease_owner": None,
                    "lease_expires_at": None,
                    "sent_at": sent_at,
                    "provider_receipt": receipt,
                    "last_error": error,
                }
            )
            transaction.set(reference, encode(changed))

        await apply(self._client.transaction(max_attempts=5))


def _delivery(notification: Notification, endpoint: NotificationEndpoint) -> NotificationDelivery:
    identity = hashlib.sha256(f"{notification.id}\0{endpoint.id}".encode()).hexdigest()
    return NotificationDelivery(
        id=f"delivery_{identity[:40]}",
        organisation_id=notification.organisation_id,
        notification_id=notification.id,
        endpoint_id=endpoint.id,
        endpoint_revision=endpoint.revision,
        provider=endpoint.provider,
        available_at=notification.created_at,
    )


def _data(snapshot: DocumentSnapshot) -> dict[str, Any]:
    data = snapshot.to_dict()
    if data is None:
        raise StorageIntegrityError(f"notification document {snapshot.id} has no data")
    return data
