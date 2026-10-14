import pytest

from acme.om.idempotency.storage import IdempotencyStorageInterface
from acme.om.idempotency.storage.impl.memory import IdempotencyStorageMemoryImpl

from contracts.idempotency_storage import IdempotencyStorageContract


class TestIdempotencyStorageMemory(IdempotencyStorageContract):
    @pytest.fixture
    def storage(self) -> IdempotencyStorageInterface:
        return IdempotencyStorageMemoryImpl()
