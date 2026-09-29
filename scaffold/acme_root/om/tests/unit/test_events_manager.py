from datetime import timedelta

import pytest
from contracts.event_storage import make_event

from acme.om.base import new_id, utcnow
from acme.om.events.impl.manager import EventsManagerImpl, EventsOptions
from acme.om.events.storage.impl.memory import EventStorageMemoryImpl
from acme.om.exceptions import NotAuthorized, StreamTruncated
from acme.om.opcontext import (
    AppContext,
    AppType,
    CredentialKind,
    OpContext,
    RequestContext,
    Role,
    build_context,
)
from acme.om.tenancy import TenancyManagerInterface
from acme.om.tenancy.rules import permissions_of

APP = AppContext(type=AppType.PORTAL, version="portal@test")


def context(role: Role = Role.MEMBER) -> OpContext:
    return build_context(
        RequestContext(request_id=new_id(), app=APP),
        user_id=new_id(),
        org_id=new_id(),
        role=role,
        permissions=permissions_of(role),
        credential_kind=CredentialKind.SESSION_TOKEN,
    )


class Retention(TenancyManagerInterface):
    """The one question the events sweep asks of tenancy, answered as the
    case sets it; any other method fails loudly as unimplemented."""

    def __init__(self) -> None:
        self.expired = False

    async def tenant_expired(self, ctx: OpContext) -> bool:
        return self.expired


Retention.__abstractmethods__ = frozenset()


@pytest.fixture
def retention() -> Retention:
    return Retention()  # pyright: ignore[reportAbstractUsage] (a partial double)


@pytest.fixture
def manager(retention: Retention) -> EventsManagerImpl:
    return EventsManagerImpl(EventStorageMemoryImpl(), retention, EventsOptions(max_limit=2))


async def test_append_sequences_per_tenant_and_reads_back_by_seq(
    manager: EventsManagerImpl,
) -> None:
    ann, bob = context(), context()
    first = await manager.append_event(ann, make_event(ann.org_id))
    second = await manager.append_event(ann, make_event(ann.org_id, "tenancy.user.updated"))
    elsewhere = await manager.append_event(bob, make_event(bob.org_id))
    assert (first.seq, second.seq, elsewhere.seq) == (1, 2, 1)
    assert first.kind == "tenancy.user.created" and first.payload == {"display_name": "t"}
    assert await manager.get_events(ann, after_seq=1, limit=10) == [second]
    assert await manager.get_events(bob, after_seq=0, limit=10) == [elsewhere]


async def test_get_events_clamps_the_limit_and_tolerates_a_negative_cursor(
    manager: EventsManagerImpl,
) -> None:
    ctx = context()
    for _ in range(3):
        await manager.append_event(ctx, make_event(ctx.org_id))
    assert [e.seq for e in await manager.get_events(ctx, after_seq=-5, limit=1000)] == [1, 2]


async def test_the_stream_is_read_with_read_and_is_append_only(manager: EventsManagerImpl) -> None:
    # An append-only entity: no update, no delete, and the read needs READ.
    member = context(Role.MEMBER)
    viewer = member.model_copy(
        update={
            "security": member.security.model_copy(
                update={"role": Role.VIEWER, "permissions": permissions_of(Role.VIEWER)}
            )
        }
    )
    await manager.append_event(member, make_event(member.org_id))
    assert [e.seq for e in await manager.get_events(viewer, after_seq=0, limit=10)] == [1]
    assert not hasattr(manager, "update_event") and not hasattr(manager, "delete_event")
    no_read = viewer.model_copy(
        update={"security": viewer.security.model_copy(update={"permissions": ()})}
    )
    with pytest.raises(NotAuthorized):
        await manager.get_events(no_read, after_seq=0, limit=10)


async def test_an_append_is_a_write_and_carries_the_contexts_provenance(
    manager: EventsManagerImpl,
) -> None:
    # A viewer reads the stream and appends nothing to it.
    viewer = context(Role.VIEWER)
    with pytest.raises(NotAuthorized):
        await manager.append_event(viewer, make_event(viewer.org_id))
    assert await manager.get_head(viewer) == 0
    # A caller-built event names whatever tenant and whoever it likes; the row
    # records the context.
    member = context(Role.MEMBER)
    foreign = make_event(new_id(), "work.item.failed")
    appended = await manager.append_event(member, foreign)
    assert (appended.org_id, appended.actor_id, appended.request_id, appended.app) == (
        member.org_id,
        member.user_id,
        member.request_id,
        "portal",
    )
    assert (appended.id, appended.kind, appended.target_id, appended.payload) == (
        foreign.id,
        foreign.kind,
        foreign.target_id,
        foreign.payload,
    )
    assert appended.org_id != foreign.org_id and appended.actor_id != foreign.actor_id
    assert appended.request_id != foreign.request_id
    assert await manager.get_events(member, after_seq=0, limit=10) == [appended]


async def test_the_head_is_the_last_seq_of_the_callers_tenant(manager: EventsManagerImpl) -> None:
    ctx, other = context(), context()
    assert await manager.get_head(ctx) == 0
    await manager.append_event(ctx, make_event(ctx.org_id))
    await manager.append_event(ctx, make_event(ctx.org_id))
    await manager.append_event(other, make_event(other.org_id))
    assert await manager.get_head(ctx) == 2
    assert await manager.get_head(other) == 1


async def test_the_sweep_drops_a_stream_only_once_its_tenant_has_expired(
    manager: EventsManagerImpl, retention: Retention
) -> None:
    ann = context(Role.OWNER)
    await manager.append_event(ann, make_event(ann.org_id))
    await manager.append_event(ann, make_event(ann.org_id, "tenancy.user.updated"))
    assert await manager.purge_tenant(ann) == 0
    assert len(await manager.get_events(ann, 0, 2)) == 2
    retention.expired = True
    assert await manager.purge_tenant(ann) == 2
    assert await manager.get_events(ann, 0, 2) == []
    assert await manager.get_head(ann) == 0


def keeping(retention: Retention, days: int | None, batch: int = 1000) -> EventsManagerImpl:
    kept = None if days is None else timedelta(days=days)
    options = EventsOptions(retention=kept, purge_batch=batch)
    return EventsManagerImpl(EventStorageMemoryImpl(), retention, options)


async def append_aged(manager: EventsManagerImpl, ctx: OpContext, *days_ago: int) -> None:
    for days in days_ago:
        aged = make_event(ctx.org_id, produced_at=utcnow() - timedelta(days=days))
        await manager.append_event(ctx, aged)


async def test_with_no_retention_the_sweep_keeps_every_event(retention: Retention) -> None:
    """No retention keeps every event: no floor moves."""
    manager = keeping(retention, None)
    ctx = context(Role.OWNER)
    await append_aged(manager, ctx, 400, 200)
    assert await manager.purge_across_tenants() == 0
    assert [e.seq for e in await manager.get_events(ctx, 0, 10)] == [1, 2]


async def test_the_sweep_trims_one_batch_of_what_is_past_the_retention(
    retention: Retention,
) -> None:
    manager = keeping(retention, 90, batch=2)
    ctx, other = context(Role.OWNER), context(Role.OWNER)
    await append_aged(manager, ctx, 100, 100, 100, 1)
    await append_aged(manager, other, 1)
    assert await manager.purge_tenant(ctx) == 0, "a living tenant keeps its stream"
    assert await manager.purge_across_tenants() == 2, "one batch a call"
    assert await manager.purge_across_tenants() == 1
    assert await manager.purge_across_tenants() == 0, "the young events stay"
    assert [e.seq for e in await manager.get_events(ctx, 3, 10)] == [4]
    assert [e.seq for e in await manager.get_events(other, 0, 10)] == [1]
    # A tenant past its own retention loses the rest, floor and all.
    retention.expired = True
    assert await manager.purge_tenant(ctx) == 1
    assert await manager.get_head(ctx) == 0
    assert await manager.get_events(ctx, 0, 10) == []


async def test_a_read_below_the_floor_is_refused_with_the_head(retention: Retention) -> None:
    manager = keeping(retention, 90)
    ctx = context(Role.OWNER)
    await append_aged(manager, ctx, 100, 100, 1, 1)
    assert await manager.purge_across_tenants() == 2
    for below in (0, 1, -5):
        with pytest.raises(StreamTruncated) as refused:
            await manager.get_events(ctx, below, 10)
        assert (refused.value.floor, refused.value.head) == (2, 4)
        assert (refused.value.http_status, refused.value.code) == (410, "stream_truncated")
    # At the floor and above it, the stream reads as it always did.
    assert [e.seq for e in await manager.get_events(ctx, 2, 10)] == [3, 4]
    assert [e.seq for e in await manager.get_events(ctx, 3, 10)] == [4]
    assert await manager.get_events(ctx, 4, 10) == []
    # The floor is the tenant's own: another tenant reads from 0.
    other = context()
    await manager.append_event(other, make_event(other.org_id))
    assert [e.seq for e in await manager.get_events(other, 0, 10)] == [1]
