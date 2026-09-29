import pytest
from contracts.task_storage import TaskStorageContract

from acme.om.orchestrations.storage import OrchestrationsStorageInterface
from acme.om.orchestrations.storage.impl.postgres import OrchestrationsStoragePostgresImpl
from acme.om.storage.impl.pg_base import SessionFactory
from acme.om.storage.roles import DatabaseRole
from acme.om.tasks.storage import TasksStorageInterface
from acme.om.tasks.storage.impl.postgres import TasksStoragePostgresImpl

pytestmark = pytest.mark.integration


class TestTaskStoragePostgres(TaskStorageContract):
    @pytest.fixture
    def storage(self, pg_sessions: dict[DatabaseRole, SessionFactory]) -> TasksStorageInterface:
        return TasksStoragePostgresImpl(pg_sessions)

    @pytest.fixture
    def orchestrations(
        self, pg_sessions: dict[DatabaseRole, SessionFactory]
    ) -> OrchestrationsStorageInterface:
        return OrchestrationsStoragePostgresImpl(pg_sessions)
