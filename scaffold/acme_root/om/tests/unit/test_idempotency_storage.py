import pytest
from contracts.idempotency_storage import IdempotencyStorageContract

from acme.om.idempotency.storage import IdempotencyStorageInterface
from acme.om.idempotency.storage.impl.memory import IdempotencyStorageMemoryImpl


class TestIdempotencyStorageMemory(IdempotencyStorageContract):
    @pytest.fixture
    def storage(self) -> IdempotencyStorageInterface:
        return IdempotencyStorageMemoryImpl()
