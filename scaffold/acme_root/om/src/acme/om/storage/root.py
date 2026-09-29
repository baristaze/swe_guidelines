"""Storage implementations are assembled behind a single root."""

from abc import ABC, abstractmethod

from acme.om.events.storage import EventStorageInterface
from acme.om.idempotency.storage import IdempotencyStorageInterface
from acme.om.media.storage import MediaStorageInterface
from acme.om.orchestrations.storage import OrchestrationsStorageInterface
from acme.om.outbox.storage import OutboxStorageInterface
from acme.om.tenancy.storage import TenancyStorageInterface
from acme.om.work.storage import WorkStorageInterface


class StorageInterface(ABC):
    @abstractmethod
    def get_tenancy_storage(self) -> TenancyStorageInterface: ...

    @abstractmethod
    def get_work_storage(self) -> WorkStorageInterface: ...

    @abstractmethod
    def get_media_storage(self) -> MediaStorageInterface: ...

    @abstractmethod
    def get_idempotency_storage(self) -> IdempotencyStorageInterface: ...

    @abstractmethod
    def get_event_storage(self) -> EventStorageInterface: ...

    @abstractmethod
    def get_outbox_storage(self) -> OutboxStorageInterface: ...

    @abstractmethod
    def get_orchestrations_storage(self) -> OrchestrationsStorageInterface: ...

    @abstractmethod
    async def healthcheck(self) -> bool: ...

    @abstractmethod
    async def close(self) -> None: ...
