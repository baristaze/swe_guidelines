import pytest
from contracts.tenancy_storage import TenancyStorageContract

from acme.om.idempotency.storage import IdempotencyStorageInterface
from acme.om.idempotency.storage.impl.memory import IdempotencyStorageMemoryImpl
from acme.om.outbox.storage import OutboxStorageInterface
from acme.om.outbox.storage.impl.memory import OutboxStorageMemoryImpl
from acme.om.tenancy.storage import TenancyStorageInterface
from acme.om.tenancy.storage.impl.memory import TenancyStorageMemoryImpl


class TestTenancyStorageMemory(TenancyStorageContract):
    @pytest.fixture
    def outbox(self) -> OutboxStorageInterface:
        return OutboxStorageMemoryImpl()

    @pytest.fixture
    def markers(self) -> IdempotencyStorageInterface:
        return IdempotencyStorageMemoryImpl()

    @pytest.fixture
    def storage(
        self, outbox: OutboxStorageInterface, markers: IdempotencyStorageInterface
    ) -> TenancyStorageInterface:
        # The root wires the same two stores into the tenancy impl: the
        # memory twin of the outbox row landing in the commit, and of the
        # marker being read in the statement.
        assert isinstance(outbox, OutboxStorageMemoryImpl)
        assert isinstance(markers, IdempotencyStorageMemoryImpl)
        return TenancyStorageMemoryImpl(outbox, markers)
