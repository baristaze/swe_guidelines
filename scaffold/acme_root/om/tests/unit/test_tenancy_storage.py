import pytest

from acme.om.idempotency.storage import IdempotencyStorageInterface
from acme.om.idempotency.storage.impl.memory import IdempotencyStorageMemoryImpl
from acme.om.outbox.storage import OutboxStorageInterface
from acme.om.outbox.storage.impl.memory import OutboxStorageMemoryImpl
from acme.om.tenancy.storage import TenancyStorageInterface
from acme.om.tenancy.storage.impl.memory import TenancyStorageMemoryImpl

from contracts.tenancy_credentials_storage import TenancyCredentialsStorageContract
from contracts.tenancy_members_storage import TenancyMembersStorageContract
from contracts.tenancy_org_storage import TenancyOrgStorageContract
from contracts.tenancy_sign_in_storage import TenancySignInStorageContract
from contracts.tenancy_storage import TenancyStorageContract


class TestTenancyStorageMemory(
    TenancyCredentialsStorageContract,
    TenancyMembersStorageContract,
    TenancyOrgStorageContract,
    TenancySignInStorageContract,
    TenancyStorageContract,
):
    @pytest.fixture
    def outbox(self) -> OutboxStorageInterface:
        return OutboxStorageMemoryImpl()

    @pytest.fixture
    def markers(self) -> IdempotencyStorageInterface:
        return IdempotencyStorageMemoryImpl()

    @pytest.fixture
    def storage(
        self, outbox: OutboxStorageInterface, markers: IdempotencyStorageInterface
    ) -> TenancyStorageInterface:
        # The root wires the same two stores into the tenancy impl: the
        # memory twin of the outbox row landing in the commit, and of the
        # marker being read in the statement.
        assert isinstance(outbox, OutboxStorageMemoryImpl)
        assert isinstance(markers, IdempotencyStorageMemoryImpl)
        return TenancyStorageMemoryImpl(outbox, markers)
