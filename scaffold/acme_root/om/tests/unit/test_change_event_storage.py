import pytest

from acme.om.change_events.storage import ChangeEventStorageInterface
from acme.om.change_events.storage.impl.memory import ChangeEventStorageMemoryImpl

from contracts.change_event_storage import ChangeEventStorageContract


class TestChangeEventStorageMemory(ChangeEventStorageContract):
    @pytest.fixture
    def storage(self) -> ChangeEventStorageInterface:
        return ChangeEventStorageMemoryImpl()
