import pytest
from contracts.work_storage import WorkStorageContract

from acme.om.storage.impl.pg_base import SessionFactory
from acme.om.storage.roles import DatabaseRole
from acme.om.work.storage import WorkStorageInterface
from acme.om.work.storage.impl.postgres import WorkStoragePostgresImpl

pytestmark = pytest.mark.integration


class TestWorkStoragePostgres(WorkStorageContract):
    @pytest.fixture
    def storage(self, pg_sessions: dict[DatabaseRole, SessionFactory]) -> WorkStorageInterface:
        return WorkStoragePostgresImpl(pg_sessions)
