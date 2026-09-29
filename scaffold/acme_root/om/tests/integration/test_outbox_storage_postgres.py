import pytest
from contracts.outbox_storage import OutboxStorageContract

from acme.om.outbox.storage import OutboxStorageInterface
from acme.om.outbox.storage.impl.postgres import OutboxStoragePostgresImpl
from acme.om.storage.impl.pg_base import SessionFactory
from acme.om.storage.roles import DatabaseRole
from acme.om.tenancy.storage import TenancyStorageInterface
from acme.om.tenancy.storage.impl.postgres import TenancyStoragePostgresImpl

pytestmark = pytest.mark.integration


class TestOutboxStoragePostgres(OutboxStorageContract):
    @pytest.fixture
    def outbox(self, pg_sessions: dict[DatabaseRole, SessionFactory]) -> OutboxStorageInterface:
        return OutboxStoragePostgresImpl(pg_sessions)

    @pytest.fixture
    def tenancy(self, pg_sessions: dict[DatabaseRole, SessionFactory]) -> TenancyStorageInterface:
        return TenancyStoragePostgresImpl(pg_sessions)
