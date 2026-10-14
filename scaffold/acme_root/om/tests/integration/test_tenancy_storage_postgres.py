import pytest

from acme.om.idempotency.storage import IdempotencyStorageInterface
from acme.om.idempotency.storage.impl.postgres import IdempotencyStoragePostgresImpl
from acme.om.outbox.storage import OutboxStorageInterface
from acme.om.outbox.storage.impl.postgres import OutboxStoragePostgresImpl
from acme.om.storage.impl.pg_base import SessionFactory
from acme.om.storage.roles import DatabaseRole
from acme.om.tenancy.storage import TenancyStorageInterface
from acme.om.tenancy.storage.impl.postgres import TenancyStoragePostgresImpl

from contracts.tenancy_credentials_storage import TenancyCredentialsStorageContract
from contracts.tenancy_members_storage import TenancyMembersStorageContract
from contracts.tenancy_org_storage import TenancyOrgStorageContract
from contracts.tenancy_sign_in_storage import TenancySignInStorageContract
from contracts.tenancy_storage import TenancyStorageContract

pytestmark = pytest.mark.integration


class TestTenancyStoragePostgres(
    TenancyCredentialsStorageContract,
    TenancyMembersStorageContract,
    TenancyOrgStorageContract,
    TenancySignInStorageContract,
    TenancyStorageContract,
):
    @pytest.fixture
    def outbox(self, pg_sessions: dict[DatabaseRole, SessionFactory]) -> OutboxStorageInterface:
        return OutboxStoragePostgresImpl(pg_sessions)

    @pytest.fixture
    def markers(
        self, pg_sessions: dict[DatabaseRole, SessionFactory]
    ) -> IdempotencyStorageInterface:
        return IdempotencyStoragePostgresImpl(pg_sessions)

    @pytest.fixture
    def storage(self, pg_sessions: dict[DatabaseRole, SessionFactory]) -> TenancyStorageInterface:
        return TenancyStoragePostgresImpl(pg_sessions)
