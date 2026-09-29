import pytest
from contracts.slack_storage import SlackStorageContract

from acme.om.outbox.storage.impl.memory import OutboxStorageMemoryImpl
from acme.om.slack.storage import SlackStorageInterface
from acme.om.slack.storage.impl.memory import SlackStorageMemoryImpl


class TestSlackStorageMemory(SlackStorageContract):
    @pytest.fixture
    def storage(self) -> SlackStorageInterface:
        return SlackStorageMemoryImpl(OutboxStorageMemoryImpl())
