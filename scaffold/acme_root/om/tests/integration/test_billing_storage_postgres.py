import pytest
from contracts.billing_storage import BillingStorageContract

from acme.om.billing.storage import BillingStorageInterface
from acme.om.billing.storage.impl.postgres import BillingStoragePostgresImpl
from acme.om.storage.impl.pg_base import SessionFactory
from acme.om.storage.roles import DatabaseRole

pytestmark = pytest.mark.integration


class TestBillingStoragePostgres(BillingStorageContract):
    @pytest.fixture
    def storage(self, pg_sessions: dict[DatabaseRole, SessionFactory]) -> BillingStorageInterface:
        return BillingStoragePostgresImpl(pg_sessions)
