import pytest
from contracts.change_event_storage import ChangeEventStorageContract

from acme.om.change_events.storage import ChangeEventStorageInterface
from acme.om.change_events.storage.impl.memory import ChangeEventStorageMemoryImpl


class TestChangeEventStorageMemory(ChangeEventStorageContract):
    @pytest.fixture
    def storage(self) -> ChangeEventStorageInterface:
        return ChangeEventStorageMemoryImpl()
