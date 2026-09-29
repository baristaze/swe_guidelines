import pytest
from contracts.work_storage import WorkStorageContract

from acme.om.work.storage import WorkStorageInterface
from acme.om.work.storage.impl.memory import WorkStorageMemoryImpl


class TestWorkStorageMemory(WorkStorageContract):
    @pytest.fixture
    def storage(self) -> WorkStorageInterface:
        return WorkStorageMemoryImpl()
