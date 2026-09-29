import pytest
from contracts.orchestration_storage import OrchestrationStorageContract

from acme.om.orchestrations.storage import OrchestrationsStorageInterface
from acme.om.orchestrations.storage.impl.memory import OrchestrationsStorageMemoryImpl
from acme.om.outbox.storage.impl.memory import OutboxStorageMemoryImpl


class TestOrchestrationStorageMemory(OrchestrationStorageContract):
    @pytest.fixture
    def storage(self) -> OrchestrationsStorageInterface:
        return OrchestrationsStorageMemoryImpl(OutboxStorageMemoryImpl())
