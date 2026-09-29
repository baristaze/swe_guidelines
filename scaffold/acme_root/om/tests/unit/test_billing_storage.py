import pytest
from contracts.billing_storage import BillingStorageContract

from acme.om.billing.storage import BillingStorageInterface
from acme.om.billing.storage.impl.memory import BillingStorageMemoryImpl
from acme.om.outbox.storage.impl.memory import OutboxStorageMemoryImpl


class TestBillingStorageMemory(BillingStorageContract):
    @pytest.fixture
    def storage(self) -> BillingStorageInterface:
        return BillingStorageMemoryImpl(OutboxStorageMemoryImpl())
