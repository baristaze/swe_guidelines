import pytest
from contracts.orchestration_storage import OrchestrationStorageContract

from acme.om.orchestrations.storage import OrchestrationsStorageInterface
from acme.om.orchestrations.storage.impl.postgres import OrchestrationsStoragePostgresImpl
from acme.om.storage.impl.pg_base import SessionFactory
from acme.om.storage.roles import DatabaseRole

pytestmark = pytest.mark.integration


class TestOrchestrationStoragePostgres(OrchestrationStorageContract):
    @pytest.fixture
    def storage(
        self, pg_sessions: dict[DatabaseRole, SessionFactory]
    ) -> OrchestrationsStorageInterface:
        return OrchestrationsStoragePostgresImpl(pg_sessions)
