import asyncio
from datetime import timedelta
from uuid import UUID

import pytest
from contracts.idempotency_storage import attempt_minted_at, attempt_of, make_record

from acme.om.base import EMPTY_UUID, new_id, utcnow
from acme.om.exceptions import (
    IdempotencyAttemptLost,
    IdempotencyInProgress,
    IdempotencyKeyReused,
    NotAuthorized,
    NotFound,
)
from acme.om.idempotency.impl.manager import IdempotencyManagerImpl, IdempotencyOptions
from acme.om.idempotency.storage.impl.memory import IdempotencyStorageMemoryImpl
from acme.om.idempotency.types.record import IdempotencyRecord
from acme.om.opcontext import (
    AppContext,
    AppType,
    CredentialKind,
    OpContext,
    OperatorContext,
    OperatorRole,
    RequestContext,
    Role,
    build_context,
)
from acme.om.tenancy.rules import operator_permissions_of, permissions_of

APP = AppContext(type=AppType.API, version="api@test")


def context(role: Role = Role.MEMBER, org_id: UUID | None = None) -> OpContext:
    return build_context(
        RequestContext(request_id=new_id(), app=APP),
        user_id=new_id(),
        org_id=org_id or new_id(),
        role=role,
        permissions=permissions_of(role),
        credential_kind=CredentialKind.SESSION_TOKEN,
    )


def operator(role: OperatorRole = OperatorRole.WRITE) -> OperatorContext:
    """A test double of the operator stage; the transition is the tenancy
    manager's, and this suite is about the marker."""
    return OperatorContext(
        request_id=new_id(),
        app=APP,
        identity_id=new_id(),
        email="root@example.test",
        credential_kind=CredentialKind.LOGIN,
        credential_id=new_id(),
        permissions=operator_permissions_of(role),
    )


@pytest.fixture
def storage() -> IdempotencyStorageMemoryImpl:
    return IdempotencyStorageMemoryImpl()


@pytest.fixture
def manager(storage: IdempotencyStorageMemoryImpl) -> IdempotencyManagerImpl:
    return IdempotencyManagerImpl(storage, IdempotencyOptions())


async def test_begin_once_then_replay_the_stored_outcome(manager: IdempotencyManagerImpl) -> None:
    ctx = context()
    target = new_id()
    begun = await manager.begin(ctx, "k1", "digest-a", target)
    assert begun.pending and begun.target_id == target
    with pytest.raises(IdempotencyInProgress):
        await manager.begin(ctx, "k1", "digest-a", new_id())
    finished = await manager.finish(ctx, "k1", attempt_of(begun), 201, '{"id":"t1"}')
    assert finished.status == 201 and finished.body == '{"id":"t1"}'
    replayed = await manager.begin(ctx, "k1", "digest-a", new_id())
    assert replayed == finished
    assert (await manager.begin(ctx, "k2", "digest-a", new_id())).pending


async def test_every_begin_mints_its_own_attempt(manager: IdempotencyManagerImpl) -> None:
    ctx = context()
    first = await manager.begin(ctx, "k1", "d", new_id())
    second = await manager.begin(ctx, "k2", "d", new_id())
    assert first.attempt_id != second.attempt_id


async def test_a_reused_key_with_another_request_is_refused(
    manager: IdempotencyManagerImpl,
) -> None:
    ctx = context()
    await manager.begin(ctx, "k1", "digest-a", new_id())
    with pytest.raises(IdempotencyKeyReused):
        await manager.begin(ctx, "k1", "digest-b", new_id())


async def test_keys_are_personal_within_the_tenant(manager: IdempotencyManagerImpl) -> None:
    org_id = new_id()
    ann, bob = context(org_id=org_id), context(org_id=org_id)
    elsewhere = context()
    anns = await manager.begin(ann, "k1", "digest-a", new_id())
    assert anns.pending
    assert (await manager.begin(bob, "k1", "digest-a", new_id())).pending
    assert (await manager.begin(elsewhere, "k1", "digest-a", new_id())).pending
    await manager.finish(ann, "k1", attempt_of(anns), 200, "ann")
    with pytest.raises(IdempotencyInProgress):
        await manager.begin(bob, "k1", "digest-a", new_id())


async def test_release_keeps_the_marker_and_the_retry_reruns_on_its_id(
    storage: IdempotencyStorageMemoryImpl, manager: IdempotencyManagerImpl
) -> None:
    # The first attempt failed after its row landed (a 5xx between the commit
    # and the response). The release keeps the record with its digest and its
    # target id and clears only the attempt; the retry re-arms it and reruns
    # on the same id, so the create finds the row instead of making a second.
    ctx = context()
    first = new_id()
    begun = await manager.begin(ctx, "k1", "d", first)
    assert begun.pending
    await manager.release(ctx, "k1", attempt_of(begun))
    released = await storage.read_record(ctx.org_id, ctx.user_id, "k1")
    assert released is not None and released.released and released.target_id == first
    retry = await manager.begin(ctx, "k1", "d", new_id())
    assert retry.pending and retry.target_id == first, "the marker's id, not the retry's"
    assert retry.attempt_id != begun.attempt_id
    assert retry.request_digest == "d"
    finished = await manager.finish(ctx, "k1", attempt_of(retry), 201, "{}")
    assert finished.target_id == first
    assert (await manager.begin(ctx, "k1", "d", new_id())).body == "{}"


async def test_a_released_key_with_another_request_is_still_refused(
    manager: IdempotencyManagerImpl,
) -> None:
    ctx = context()
    begun = await manager.begin(ctx, "k1", "digest-a", new_id())
    await manager.release(ctx, "k1", attempt_of(begun))
    with pytest.raises(IdempotencyKeyReused):
        await manager.begin(ctx, "k1", "digest-b", new_id())


async def test_a_stale_attempt_cannot_release_or_finish_a_rearmed_marker(
    storage: IdempotencyStorageMemoryImpl, manager: IdempotencyManagerImpl
) -> None:
    ctx = context()
    target = new_id()
    failed = await manager.begin(ctx, "k", "d", target)
    stale = attempt_of(failed)
    await manager.release(ctx, "k", stale)
    # Released, the marker is nobody's: the failed attempt is refused twice over.
    with pytest.raises(IdempotencyAttemptLost):
        await manager.release(ctx, "k", stale)
    with pytest.raises(IdempotencyAttemptLost):
        await manager.finish(ctx, "k", stale, 201, "late")
    retry = await manager.begin(ctx, "k", "d", new_id())
    assert retry.pending and retry.target_id == target
    # Re-armed and within its lease: the retry holds it, another retry waits,
    # and the failed attempt still cannot touch it.
    with pytest.raises(IdempotencyInProgress):
        await manager.begin(ctx, "k", "d", new_id())
    with pytest.raises(IdempotencyAttemptLost):
        await manager.release(ctx, "k", stale)
    with pytest.raises(IdempotencyAttemptLost):
        await manager.finish(ctx, "k", stale, 201, "late")
    assert await storage.read_record(ctx.org_id, ctx.user_id, "k") == retry
    assert (await manager.finish(ctx, "k", attempt_of(retry), 201, "retry")).body == "retry"


async def test_a_released_marker_is_rearmed_by_exactly_one_of_two_racing_retries(
    manager: IdempotencyManagerImpl,
) -> None:
    ctx = context()
    target = new_id()
    failed = await manager.begin(ctx, "k", "d", target)
    await manager.release(ctx, "k", attempt_of(failed))
    outcomes = await asyncio.gather(
        manager.begin(ctx, "k", "d", new_id()),
        manager.begin(ctx, "k", "d", new_id()),
        return_exceptions=True,
    )
    armed = [o for o in outcomes if isinstance(o, IdempotencyRecord)]
    waiting = [o for o in outcomes if isinstance(o, IdempotencyInProgress)]
    assert len(armed) == 1 and len(waiting) == 1, outcomes
    assert armed[0].pending and armed[0].target_id == target
    assert armed[0].attempt_id not in (None, failed.attempt_id)


async def test_finish_needs_a_begun_key_and_write_permission(
    manager: IdempotencyManagerImpl,
) -> None:
    with pytest.raises(NotFound):
        await manager.finish(context(), "never", new_id(), 200, "")
    viewer = context(Role.VIEWER)
    with pytest.raises(NotAuthorized):
        await manager.begin(viewer, "k1", "digest-a", new_id())


async def test_a_finished_record_is_not_finished_or_released_again(
    manager: IdempotencyManagerImpl,
) -> None:
    ctx = context()
    begun = await manager.begin(ctx, "k", "d", new_id())
    await manager.finish(ctx, "k", attempt_of(begun), 201, "first")
    with pytest.raises(IdempotencyAttemptLost):
        await manager.finish(ctx, "k", attempt_of(begun), 201, "second")
    with pytest.raises(IdempotencyAttemptLost):
        await manager.release(ctx, "k", attempt_of(begun))
    assert (await manager.begin(ctx, "k", "d", new_id())).body == "first"


async def test_an_abandoned_pending_record_is_taken_over_with_its_target_id() -> None:
    # The marker landed and the outcome never did (a crash in between, before
    # or after the create committed); once the pending lease has passed a retry
    # runs the request again on the id the first attempt minted, so a create
    # that already landed is found and not repeated.
    manager = IdempotencyManagerImpl(
        IdempotencyStorageMemoryImpl(), IdempotencyOptions(pending_ttl=timedelta(0))
    )
    ctx = context()
    first = new_id()
    begun = await manager.begin(ctx, "k", "d", first)
    assert begun.target_id == first
    taken = await manager.begin(ctx, "k", "d", new_id())
    assert taken.pending and taken.target_id == first, "abandoned: run it again, same id"
    assert taken.attempt_id != begun.attempt_id, "the take-over stamps its own attempt"
    await manager.finish(ctx, "k", attempt_of(taken), 201, "{}")
    replayed = await manager.begin(ctx, "k", "d", new_id())
    assert replayed.status == 201, "finished: replay it"


async def test_a_marker_handed_on_runs_its_lease_from_the_attempt(
    storage: IdempotencyStorageMemoryImpl, manager: IdempotencyManagerImpl
) -> None:
    # A marker born an hour ago and handed to its second attempt a moment ago:
    # the lease runs from the attempt, so the marker is held and the next retry
    # waits for it. Measured from the marker, it was stale the instant it
    # changed hands and a third attempt would take it over at once.
    ctx = context()
    handed_on = make_record(ctx.user_id, "k", utcnow() - timedelta(hours=1)).model_copy(
        update={"attempt_id": attempt_minted_at(utcnow())}
    )
    await storage.write_record(ctx.org_id, handed_on)
    with pytest.raises(IdempotencyInProgress):
        await manager.begin(ctx, "k", handed_on.request_digest, new_id())
    assert await storage.read_record(ctx.org_id, ctx.user_id, "k") == handed_on


async def test_a_slow_attempt_that_lost_the_marker_is_refused(
    storage: IdempotencyStorageMemoryImpl, manager: IdempotencyManagerImpl
) -> None:
    # The first attempt is still running when its pending lease passes; the
    # retry takes the marker over and runs the request again on the same id.
    # Whatever the first attempt then does is refused: it can neither record
    # its outcome over the retry's marker nor release the marker the retry
    # holds, and the retry's outcome is the one every later retry replays.
    ctx = context()
    target = new_id()
    # The marker and the attempt that holds it are five minutes old, and that
    # attempt is still running: the lease passed while it ran.
    slow = make_record(ctx.user_id, "k", utcnow() - timedelta(minutes=5)).model_copy(
        update={"target_id": target, "request_digest": "d"}
    )
    await storage.write_record(ctx.org_id, slow)

    retry = await manager.begin(ctx, "k", "d", new_id())
    assert retry.pending and retry.target_id == target
    assert retry.attempt_id != slow.attempt_id

    with pytest.raises(IdempotencyAttemptLost):
        await manager.finish(ctx, "k", attempt_of(slow), 201, "slow")
    with pytest.raises(IdempotencyAttemptLost):
        await manager.release(ctx, "k", attempt_of(slow))
    stored = await storage.read_record(ctx.org_id, ctx.user_id, "k")
    assert stored == retry, "the slow attempt changed nothing"

    finished = await manager.finish(ctx, "k", attempt_of(retry), 201, "retry")
    assert finished.body == "retry"
    assert (await manager.begin(ctx, "k", "d", new_id())).body == "retry"


async def test_a_record_written_by_hand_carries_its_attempt() -> None:
    assert make_record().attempt_id != make_record().attempt_id


async def test_purge_takes_finished_records_past_the_retention_and_abandoned_markers(
    storage: IdempotencyStorageMemoryImpl,
) -> None:
    options = IdempotencyOptions(pending_ttl=timedelta(minutes=2), retention=timedelta(hours=1))
    manager = IdempotencyManagerImpl(storage, options)
    ctx = context()
    now = utcnow()
    # Finished an hour and a bit ago: past the retention. Finished just now: kept.
    stale = make_record(ctx.user_id, "stale", now - timedelta(minutes=61)).model_copy(
        update={"status": 201, "body": "{}"}
    )
    await storage.write_record(ctx.org_id, stale)
    fresh = await manager.begin(ctx, "fresh", "d", new_id())
    await manager.finish(ctx, "fresh", attempt_of(fresh), 201, "{}")
    # Pending for eleven leases: no retry came back. Pending for three: a
    # retry may still take it over, so it stays.
    forgotten = make_record(ctx.user_id, "forgotten", now - timedelta(minutes=22))
    recent = make_record(ctx.user_id, "recent", now - timedelta(minutes=6))
    await storage.write_record(ctx.org_id, forgotten)
    await storage.write_record(ctx.org_id, recent)
    # Released an hour and a bit ago: past the retention, like a finished
    # one. Released within it: a retry may still re-arm it, so it stays.
    dropped = make_record(ctx.user_id, "dropped", now - timedelta(minutes=61)).model_copy(
        update={"attempt_id": None}
    )
    held_open = make_record(ctx.user_id, "held-open", now - timedelta(minutes=22)).model_copy(
        update={"attempt_id": None}
    )
    await storage.write_record(ctx.org_id, dropped)
    await storage.write_record(ctx.org_id, held_open)
    assert await manager.purge_across_tenants() == 3
    assert await storage.read_record(ctx.org_id, ctx.user_id, "stale") is None
    assert await storage.read_record(ctx.org_id, ctx.user_id, "forgotten") is None
    assert await storage.read_record(ctx.org_id, ctx.user_id, "dropped") is None
    assert (await storage.read_record(ctx.org_id, ctx.user_id, "fresh")) is not None
    assert await storage.read_record(ctx.org_id, ctx.user_id, "recent") == recent
    assert await storage.read_record(ctx.org_id, ctx.user_id, "held-open") == held_open
    # A purged key is free again: the next begin runs the request anew.
    assert (await manager.begin(ctx, "stale", "d", new_id())).pending


async def test_the_operator_marker_runs_the_same_moves_in_the_system_scope(
    manager: IdempotencyManagerImpl, storage: IdempotencyStorageMemoryImpl
) -> None:
    """The operator plane's creating requests carry a key like every other:
    the record is the operator's, under the system scope, and it replays,
    refuses a reused key, releases, and finishes as a tenant's does. A read
    operator has no marker to begin, since only a write carries a key."""
    admin = operator()
    target = new_id()
    begun = await manager.begin_for_operator(admin, "k1", "digest-a", target)
    assert begun.pending and begun.target_id == target and begun.user_id == admin.identity_id
    assert await storage.read_record(EMPTY_UUID, admin.identity_id, "k1") == begun
    with pytest.raises(IdempotencyInProgress):
        await manager.begin_for_operator(admin, "k1", "digest-a", new_id())
    with pytest.raises(IdempotencyKeyReused):
        await manager.begin_for_operator(admin, "k1", "digest-b", new_id())
    assert begun.attempt_id is not None
    await manager.release_for_operator(admin, "k1", begun.attempt_id)
    rearmed = await manager.begin_for_operator(admin, "k1", "digest-a", new_id())
    assert rearmed.pending and rearmed.target_id == target
    assert rearmed.attempt_id is not None
    with pytest.raises(IdempotencyAttemptLost):
        await manager.finish_for_operator(admin, "k1", begun.attempt_id, 201, "{}")
    finished = await manager.finish_for_operator(admin, "k1", rearmed.attempt_id, 201, "{}")
    assert (finished.status, finished.body) == (201, "{}")
    assert await manager.begin_for_operator(admin, "k1", "digest-a", new_id()) == finished
    # Personal to the operator, as a tenant's key is to the user.
    assert (await manager.begin_for_operator(operator(), "k1", "digest-a", new_id())).pending
    with pytest.raises(NotFound):
        await manager.finish_for_operator(admin, "k2", new_id(), 201, "{}")
    reader = operator(OperatorRole.READ)
    for move in (
        manager.begin_for_operator(reader, "k3", "digest-a", new_id()),
        manager.finish_for_operator(reader, "k1", rearmed.attempt_id, 201, "{}"),
        manager.release_for_operator(reader, "k1", rearmed.attempt_id),
    ):
        with pytest.raises(NotAuthorized):
            await move
