import pytest
from contracts.lease_storage import LeaseStorageContract

from acme.om.leases.storage import LeasesStorageInterface
from acme.om.leases.storage.impl.postgres import LeasesStoragePostgresImpl
from acme.om.orchestrations.storage import OrchestrationsStorageInterface
from acme.om.orchestrations.storage.impl.postgres import OrchestrationsStoragePostgresImpl
from acme.om.storage.impl.pg_base import SessionFactory
from acme.om.storage.roles import DatabaseRole

pytestmark = pytest.mark.integration


class TestLeaseStoragePostgres(LeaseStorageContract):
    @pytest.fixture
    def records(
        self, pg_sessions: dict[DatabaseRole, SessionFactory]
    ) -> OrchestrationsStorageInterface:
        return OrchestrationsStoragePostgresImpl(pg_sessions)

    @pytest.fixture
    def storage(self, pg_sessions: dict[DatabaseRole, SessionFactory]) -> LeasesStorageInterface:
        return LeasesStoragePostgresImpl(pg_sessions)
