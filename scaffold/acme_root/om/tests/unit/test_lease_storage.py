import pytest
from contracts.lease_storage import LeaseStorageContract

from acme.om.leases.storage import LeasesStorageInterface
from acme.om.leases.storage.impl.memory import LeasesStorageMemoryImpl
from acme.om.orchestrations.storage import OrchestrationsStorageInterface
from acme.om.orchestrations.storage.impl.memory import OrchestrationsStorageMemoryImpl
from acme.om.outbox.storage.impl.memory import OutboxStorageMemoryImpl


class TestLeaseStorageMemory(LeaseStorageContract):
    @pytest.fixture
    def records(self) -> OrchestrationsStorageMemoryImpl:
        return OrchestrationsStorageMemoryImpl(OutboxStorageMemoryImpl())

    @pytest.fixture
    def storage(self, records: OrchestrationsStorageInterface) -> LeasesStorageInterface:
        assert isinstance(records, OrchestrationsStorageMemoryImpl)
        return LeasesStorageMemoryImpl(OutboxStorageMemoryImpl(), records)
