import pytest
from contracts.outbox_storage import OutboxStorageContract

from acme.om.outbox.storage import OutboxStorageInterface
from acme.om.outbox.storage.impl.memory import OutboxStorageMemoryImpl
from acme.om.tasks.storage import TasksStorageInterface
from acme.om.tasks.storage.impl.memory import TasksStorageMemoryImpl


class TestOutboxStorageMemory(OutboxStorageContract):
    @pytest.fixture
    def outbox(self) -> OutboxStorageInterface:
        return OutboxStorageMemoryImpl()

    @pytest.fixture
    def tasks(self, outbox: OutboxStorageMemoryImpl) -> TasksStorageInterface:
        return TasksStorageMemoryImpl(outbox)
