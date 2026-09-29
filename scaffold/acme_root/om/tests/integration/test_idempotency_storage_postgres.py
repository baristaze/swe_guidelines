import pytest
from contracts.idempotency_storage import IdempotencyStorageContract

from acme.om.idempotency.storage import IdempotencyStorageInterface
from acme.om.idempotency.storage.impl.postgres import IdempotencyStoragePostgresImpl
from acme.om.storage.impl.pg_base import SessionFactory
from acme.om.storage.roles import DatabaseRole

pytestmark = pytest.mark.integration


class TestIdempotencyStoragePostgres(IdempotencyStorageContract):
    @pytest.fixture
    def storage(
        self, pg_sessions: dict[DatabaseRole, SessionFactory]
    ) -> IdempotencyStorageInterface:
        return IdempotencyStoragePostgresImpl(pg_sessions)
