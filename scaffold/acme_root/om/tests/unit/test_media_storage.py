import pytest
from contracts.media_storage import MediaStorageContract

from acme.om.media.storage import MediaStorageInterface
from acme.om.media.storage.impl.memory import MediaStorageMemoryImpl
from acme.om.outbox.storage.impl.memory import OutboxStorageMemoryImpl


class TestMediaStorageMemory(MediaStorageContract):
    @pytest.fixture
    def storage(self) -> MediaStorageInterface:
        return MediaStorageMemoryImpl(OutboxStorageMemoryImpl())
