import pytest
from contracts.tenancy_storage import TenancyStorageContract

from acme.om.billing.storage import BillingStorageInterface
from acme.om.billing.storage.impl.postgres import BillingStoragePostgresImpl
from acme.om.idempotency.storage import IdempotencyStorageInterface
from acme.om.idempotency.storage.impl.postgres import IdempotencyStoragePostgresImpl
from acme.om.outbox.storage import OutboxStorageInterface
from acme.om.outbox.storage.impl.postgres import OutboxStoragePostgresImpl
from acme.om.storage.impl.pg_base import SessionFactory
from acme.om.storage.roles import DatabaseRole
from acme.om.tenancy.storage import TenancyStorageInterface
from acme.om.tenancy.storage.impl.postgres import TenancyStoragePostgresImpl

pytestmark = pytest.mark.integration


class TestTenancyStoragePostgres(TenancyStorageContract):
    @pytest.fixture
    def outbox(self, pg_sessions: dict[DatabaseRole, SessionFactory]) -> OutboxStorageInterface:
        return OutboxStoragePostgresImpl(pg_sessions)

    @pytest.fixture
    def markers(
        self, pg_sessions: dict[DatabaseRole, SessionFactory]
    ) -> IdempotencyStorageInterface:
        return IdempotencyStoragePostgresImpl(pg_sessions)

    @pytest.fixture
    def accounts(self, pg_sessions: dict[DatabaseRole, SessionFactory]) -> BillingStorageInterface:
        return BillingStoragePostgresImpl(pg_sessions)

    @pytest.fixture
    def storage(self, pg_sessions: dict[DatabaseRole, SessionFactory]) -> TenancyStorageInterface:
        return TenancyStoragePostgresImpl(pg_sessions)
