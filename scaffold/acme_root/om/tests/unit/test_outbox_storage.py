import pytest
from contracts.outbox_storage import OutboxStorageContract

from acme.om.outbox.storage import OutboxStorageInterface
from acme.om.outbox.storage.impl.memory import OutboxStorageMemoryImpl
from acme.om.tenancy.storage import TenancyStorageInterface
from acme.om.tenancy.storage.impl.memory import TenancyStorageMemoryImpl


class TestOutboxStorageMemory(OutboxStorageContract):
    @pytest.fixture
    def outbox(self) -> OutboxStorageInterface:
        return OutboxStorageMemoryImpl()

    @pytest.fixture
    def tenancy(self, outbox: OutboxStorageMemoryImpl) -> TenancyStorageInterface:
        return TenancyStorageMemoryImpl(outbox)
