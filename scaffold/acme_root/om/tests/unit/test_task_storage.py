import pytest
from contracts.task_storage import TaskStorageContract

from acme.om.orchestrations.storage import OrchestrationsStorageInterface
from acme.om.orchestrations.storage.impl.memory import OrchestrationsStorageMemoryImpl
from acme.om.outbox.storage.impl.memory import OutboxStorageMemoryImpl
from acme.om.tasks.storage import TasksStorageInterface
from acme.om.tasks.storage.impl.memory import TasksStorageMemoryImpl


class TestTaskStorageMemory(TaskStorageContract):
    @pytest.fixture
    def outbox(self) -> OutboxStorageMemoryImpl:
        return OutboxStorageMemoryImpl()

    @pytest.fixture
    def records(self, outbox: OutboxStorageMemoryImpl) -> OrchestrationsStorageMemoryImpl:
        return OrchestrationsStorageMemoryImpl(outbox)

    @pytest.fixture
    def storage(
        self, outbox: OutboxStorageMemoryImpl, records: OrchestrationsStorageMemoryImpl
    ) -> TasksStorageInterface:
        # A step lands in the records beside the tasks it changes.
        return TasksStorageMemoryImpl(outbox, records)

    @pytest.fixture
    def orchestrations(
        self, records: OrchestrationsStorageMemoryImpl
    ) -> OrchestrationsStorageInterface:
        return records
