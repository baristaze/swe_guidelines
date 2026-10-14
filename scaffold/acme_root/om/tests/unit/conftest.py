"""The fixtures the tenancy suites share: the memory storage over one
outbox and one store of markers, the local infra, the TOTP clock, and the
tenancy manager and the operator manager over them. A module that defines
a fixture of the same name keeps its own."""

from pathlib import Path

import pytest

from acme.infra.impl.local import InfraLocalImpl
from acme.om.change_events.storage.impl.memory import ChangeEventStorageMemoryImpl
from acme.om.idempotency.storage.impl.memory import IdempotencyStorageMemoryImpl
from acme.om.outbox.impl.relay import OutboxRelayImpl
from acme.om.outbox.storage.impl.memory import OutboxStorageMemoryImpl
from acme.om.tenancy import TenancyManagerInterface
from acme.om.tenancy.impl.operator import TenancyOperatorManagerImpl, TenancyOperatorOptions
from acme.om.tenancy.storage.impl.memory import TenancyStorageMemoryImpl

from contracts.second_factor import TOTP_KEY, SteppingClock
from contracts.tenancy import make_manager


@pytest.fixture
def infra(tmp_path: Path) -> InfraLocalImpl:
    return InfraLocalImpl(tmp_path)


@pytest.fixture
def outbox() -> OutboxStorageMemoryImpl:
    return OutboxStorageMemoryImpl()


@pytest.fixture
def markers() -> IdempotencyStorageMemoryImpl:
    return IdempotencyStorageMemoryImpl()


@pytest.fixture
def storage(
    outbox: OutboxStorageMemoryImpl, markers: IdempotencyStorageMemoryImpl
) -> TenancyStorageMemoryImpl:
    return TenancyStorageMemoryImpl(outbox, markers)


@pytest.fixture
def clock() -> SteppingClock:
    return SteppingClock()


@pytest.fixture
def manager(
    storage: TenancyStorageMemoryImpl,
    infra: InfraLocalImpl,
    outbox: OutboxStorageMemoryImpl,
    clock: SteppingClock,
) -> TenancyManagerInterface:
    return make_manager(storage, infra, outbox=outbox, clock=clock)


@pytest.fixture
def operator(
    storage: TenancyStorageMemoryImpl,
    infra: InfraLocalImpl,
    outbox: OutboxStorageMemoryImpl,
    clock: SteppingClock,
) -> TenancyOperatorManagerImpl:
    events = ChangeEventStorageMemoryImpl()
    relay = OutboxRelayImpl(outbox, events, infra.get_topics())
    return TenancyOperatorManagerImpl(
        storage, events, relay, TenancyOperatorOptions(totp_encryption_key=TOTP_KEY), clock
    )
