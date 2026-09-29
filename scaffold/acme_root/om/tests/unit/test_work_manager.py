import asyncio
from datetime import timedelta
from pathlib import Path
from uuid import UUID

import pytest
from contracts.work_storage import make_item

from acme.infra.impl.local import InfraLocalImpl
from acme.infra.observability import OUTCOMES
from acme.infra.topics import EntityChangedPayload, TopicPayload, Topics, WorkAvailablePayload
from acme.om.base import EMPTY_UUID, new_id, utcnow
from acme.om.exceptions import (
    LeaseLost,
    NotAuthorized,
    NotFound,
    ValidationFailed,
    WorkNotFailed,
)
from acme.om.opcontext import (
    AppContext,
    AppType,
    CredentialKind,
    OpContext,
    OperatorContext,
    OperatorRole,
    RequestContext,
    Role,
)
from acme.om.root import Managers, build_managers
from acme.om.storage.impl.memory import StorageMemoryImpl
from acme.om.tenancy.impl.manager import TenancyOptions
from acme.om.tenancy.rules import operator_permissions_of
from acme.om.work.impl.manager import DEAD_LETTER_KIND, WorkOptions
from acme.om.work.impl.operator import REQUEUED_KIND
from acme.om.work.types.work_item import WorkItem, WorkKind, WorkStatus

LEASE = timedelta(seconds=30)
APP = AppContext(type=AppType.PORTAL, version="portal@test")
WORKER = AppContext(type=AppType.WORKER, version="worker@test")


def request(app: AppContext = WORKER) -> RequestContext:
    """The request stage the worker mints per claim and per sweep pass."""
    return RequestContext(request_id=new_id(), app=app)


@pytest.fixture
def infra(tmp_path: Path) -> InfraLocalImpl:
    return InfraLocalImpl(tmp_path)


@pytest.fixture
def storage() -> StorageMemoryImpl:
    return StorageMemoryImpl()


@pytest.fixture
def managers(infra: InfraLocalImpl, storage: StorageMemoryImpl) -> Managers:
    return build_managers(storage, infra, TenancyOptions(dev_sign_in=True))


@pytest.fixture
async def ctx(managers: Managers) -> OpContext:
    tenancy = managers.tenancy
    _, org = await tenancy.bootstrap(request(APP), "Ajax", "ajax", "ann@example.test", "Ann")
    login = await tenancy.dev_sign_in(request(APP), "ann@example.test")
    identity = await tenancy.authenticate_login(request(APP), login.token)
    issued = await tenancy.exchange_login(identity, org.id)
    return await tenancy.authenticate(request(APP), issued.token)


async def test_enqueue_publishes_and_claim_returns_the_enqueuers_context(
    managers: Managers, infra: InfraLocalImpl, ctx: OpContext
) -> None:
    seen: list[TopicPayload] = []

    async def record(payload: TopicPayload) -> None:
        seen.append(payload)

    infra.get_topics().subscribe(Topics.WORK_AVAILABLE, "test", record)
    item = make_item().model_copy(update={"created_by": ctx.user_id})
    await managers.work.enqueue(ctx, item)
    assert isinstance(seen[0], WorkAvailablePayload)
    assert seen[0].lane == "default" and seen[0].kind == "NOOP"
    assert seen[0].idempotency_key == item.idempotency_key

    claim_request = request()
    claimed = await managers.work.claim(claim_request, "default", [WorkKind.NOOP], "w1", LEASE)
    assert claimed is not None
    work_ctx, claimed_item = claimed
    assert work_ctx.org_id == ctx.org_id
    assert work_ctx.user_id == ctx.user_id
    assert work_ctx.security.role is Role.SERVICE
    assert work_ctx.security.credential_kind is CredentialKind.INTERNAL
    # The work context refines the request stage the worker minted for the claim.
    assert work_ctx.app.type is AppType.WORKER and work_ctx.request_id == claim_request.request_id
    assert claimed_item.claimed_by == "w1"

    done = await managers.work.complete(work_ctx, claimed_item)
    assert done.status is WorkStatus.DONE and done.claimed_by is None
    assert await managers.work.claim(request(), "default", [WorkKind.NOOP], "w1", LEASE) is None


async def test_the_run_names_the_request_that_caused_it_and_keeps_its_own(
    managers: Managers, ctx: OpContext
) -> None:
    """The stage a claim runs under is a new request that names the causing one.
    The enqueue leaves the item's cause and trace context as constructed: they
    are the item's, not the enqueue's."""
    causing = new_id()
    traceparent = f"00-{'a' * 32}-{'b' * 16}-01"
    item = make_item().model_copy(
        update={
            "created_by": ctx.user_id,
            "request_id": causing,
            "traceparent": traceparent,
        }
    )
    enqueued = await managers.work.enqueue(ctx, item)
    assert (enqueued.request_id, enqueued.traceparent) == (causing, traceparent)

    claim_request = request()
    claimed = await managers.work.claim(claim_request, "default", [WorkKind.NOOP], "w1", LEASE)
    assert claimed is not None
    work_ctx, claimed_item = claimed
    assert work_ctx.request_id == claim_request.request_id, "the run has a request of its own"
    assert work_ctx.caused_by_request_id == causing, "and it names the one that caused it"
    assert claimed_item.traceparent == traceparent, "what the run links its span to"
    await managers.work.complete(work_ctx, claimed_item)


async def test_an_item_that_names_no_causing_request_leaves_the_field_empty(
    managers: Managers, ctx: OpContext
) -> None:
    """A required reference nobody owns is EMPTY_UUID on the row; the stage
    carries no cause at all, the way a request that arrived at the edge does."""
    item = make_item().model_copy(update={"created_by": ctx.user_id, "request_id": EMPTY_UUID})
    assert item.request_id == EMPTY_UUID and item.traceparent is None
    await managers.work.enqueue(ctx, item)
    claimed = await managers.work.claim(request(), "default", [WorkKind.NOOP], "w1", LEASE)
    assert claimed is not None
    assert claimed[0].caused_by_request_id is None


async def test_defer_and_release_hand_back_without_spending_an_attempt(
    managers: Managers, ctx: OpContext
) -> None:
    await managers.work.enqueue(ctx, make_item().model_copy(update={"created_by": ctx.user_id}))
    claimed = await managers.work.claim(request(), "default", [WorkKind.NOOP], "w1", LEASE)
    assert claimed is not None
    deferred = await managers.work.defer(claimed[0], claimed[1], timedelta(hours=1))
    assert deferred.status is WorkStatus.QUEUED and deferred.attempts == 0
    assert deferred.available_at > utcnow() + timedelta(minutes=59)
    assert await managers.work.claim(request(), "default", [WorkKind.NOOP], "w1", LEASE) is None

    await managers.work.enqueue(ctx, make_item().model_copy(update={"created_by": ctx.user_id}))
    reclaimed = await managers.work.claim(request(), "default", [WorkKind.NOOP], "w1", LEASE)
    assert reclaimed is not None and reclaimed[1].attempts == 1
    released = await managers.work.release(reclaimed[0], reclaimed[1])
    assert released.attempts == 0 and released.status is WorkStatus.QUEUED
    again = await managers.work.claim(request(), "default", [WorkKind.NOOP], "w2", LEASE)
    assert again is not None and again[1].attempts == 1


async def test_fail_requeues_with_a_growing_delay_then_fails(
    managers: Managers, ctx: OpContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    item = make_item().model_copy(update={"created_by": ctx.user_id, "max_attempts": 2})
    await managers.work.enqueue(ctx, item)
    first = await managers.work.claim(request(), "default", [WorkKind.NOOP], "w1", LEASE)
    assert first is not None
    requeued = await managers.work.fail(first[0], first[1], "boom")
    assert requeued.status is WorkStatus.QUEUED and requeued.last_error == "boom"
    assert requeued.available_at > utcnow() + timedelta(seconds=20)
    assert await managers.work.claim(request(), "default", [WorkKind.NOOP], "w1", LEASE) is None

    # Without a delay the requeued item is claimable at once, and the second
    # failure spends the last attempt.
    monkeypatch.setattr(managers.work, "_options", WorkOptions(base_retry_delay=timedelta(0)))
    other = make_item().model_copy(update={"created_by": ctx.user_id, "max_attempts": 2})
    await managers.work.enqueue(ctx, other)
    first = await managers.work.claim(request(), "default", [WorkKind.NOOP], "w1", LEASE)
    assert first is not None and first[1].id == other.id
    assert (await managers.work.fail(first[0], first[1], "boom")).status is WorkStatus.QUEUED
    second = await managers.work.claim(request(), "default", [WorkKind.NOOP], "w1", LEASE)
    assert second is not None and second[1].id == other.id and second[1].attempts == 2
    failed = await managers.work.fail(second[0], second[1], "boom again")
    assert failed.status is WorkStatus.FAILED
    assert await managers.work.claim(request(), "default", [WorkKind.NOOP], "w1", LEASE) is None


async def test_extend_lease_and_lease_loss(managers: Managers, ctx: OpContext) -> None:
    await managers.work.enqueue(ctx, make_item().model_copy(update={"created_by": ctx.user_id}))
    claimed = await managers.work.claim(request(), "default", [WorkKind.NOOP], "w1", LEASE)
    assert claimed is not None
    extended = await managers.work.extend_lease(claimed[0], claimed[1], timedelta(minutes=5))
    assert extended.lease_expires_at is not None
    assert extended.lease_expires_at > utcnow() + timedelta(minutes=4)
    # The token is the fence, not the worker's name: a copy under another
    # token is refused, and so is one that carries no token at all.
    stolen = claimed[1].model_copy(update={"claim_token": new_id()})
    with pytest.raises(LeaseLost):
        await managers.work.extend_lease(claimed[0], stolen, LEASE)
    with pytest.raises(LeaseLost):
        await managers.work.extend_lease(
            claimed[0], claimed[1].model_copy(update={"claim_token": None}), LEASE
        )


async def test_the_same_worker_re_claiming_after_a_requeue_refuses_its_stale_copy(
    managers: Managers, ctx: OpContext
) -> None:
    # A worker whose lease expired, whose item the sweep requeued, and which
    # claims the same item again: the stale copy's completion carries the old
    # token and is refused; the new claim is settled by its own token.
    await managers.work.enqueue(ctx, make_item().model_copy(update={"created_by": ctx.user_id}))
    first = await managers.work.claim(
        request(), "default", [WorkKind.NOOP], "w1", timedelta(seconds=-1)
    )
    assert first is not None
    stale_ctx, stale = first
    assert await managers.work.requeue_stale(request(), limit=100) == 1
    second = await managers.work.claim(request(), "default", [WorkKind.NOOP], "w1", LEASE)
    assert second is not None
    fresh_ctx, fresh = second
    assert fresh.claimed_by == stale.claimed_by == "w1"
    assert fresh.claim_token != stale.claim_token
    for stale_write in (
        managers.work.complete(stale_ctx, stale),
        managers.work.fail(stale_ctx, stale, "boom"),
        managers.work.extend_lease(stale_ctx, stale, LEASE),
        managers.work.release(stale_ctx, stale),
    ):
        with pytest.raises(LeaseLost):
            await stale_write
    assert (await managers.work.extend_lease(fresh_ctx, fresh, LEASE)).status is WorkStatus.CLAIMED
    done = await managers.work.complete(fresh_ctx, fresh)
    assert done.status is WorkStatus.DONE and done.claim_token is None


async def test_transitions_refuse_a_lost_lease_and_a_missing_item(
    managers: Managers, storage: StorageMemoryImpl, ctx: OpContext
) -> None:
    await managers.work.enqueue(ctx, make_item().model_copy(update={"created_by": ctx.user_id}))
    claimed = await managers.work.claim(
        request(), "default", [WorkKind.NOOP], "w1", timedelta(seconds=-1)
    )
    assert claimed is not None
    work_ctx, held = claimed
    assert await managers.work.requeue_stale(request(), limit=100) == 1

    with pytest.raises(LeaseLost):
        await managers.work.complete(work_ctx, held)
    with pytest.raises(LeaseLost):
        await managers.work.fail(work_ctx, held, "boom")
    with pytest.raises(LeaseLost):
        await managers.work.defer(work_ctx, held, timedelta(minutes=1))
    with pytest.raises(LeaseLost):
        await managers.work.release(work_ctx, held)
    stored = await storage.get_work_storage().read_item(ctx.org_id, held.id)
    assert stored is not None
    assert stored.status is WorkStatus.QUEUED and stored.last_error == "lease expired"

    taken = await managers.work.claim(request(), "default", [WorkKind.NOOP], "w2", LEASE)
    assert taken is not None and taken[1].claimed_by == "w2"
    with pytest.raises(LeaseLost):
        await managers.work.complete(work_ctx, held)
    with pytest.raises(NotFound):
        await managers.work.complete(work_ctx, held.model_copy(update={"id": new_id()}))
    done = await managers.work.complete(taken[0], taken[1])
    assert done.status is WorkStatus.DONE


async def second_tenant(managers: Managers) -> OpContext:
    """Bob's context in a second team org, Beta."""
    tenancy = managers.tenancy
    _, beta = await tenancy.bootstrap(request(APP), "Beta", "beta", "bob@example.test", "Bob")
    login = await tenancy.dev_sign_in(request(APP), "bob@example.test")
    identity = await tenancy.authenticate_login(request(APP), login.token)
    issued = await tenancy.exchange_login(identity, beta.id)
    return await tenancy.authenticate(request(APP), issued.token)


async def test_requeue_stale_runs_once_across_tenants_and_dead_letters_in_each(
    managers: Managers, infra: InfraLocalImpl, ctx: OpContext
) -> None:
    # One call reaches every tenant's expired leases, a batch at a time. An
    # item whose attempts are spent is a dead letter, and its audit event
    # lands in its own tenant's stream under the service role, as `fail`
    # writes one; the other tenant's stream is untouched.
    seen: list[TopicPayload] = []

    async def record(payload: TopicPayload) -> None:
        seen.append(payload)

    infra.get_topics().subscribe(Topics.ENTITY_CHANGED, "test", record)
    bob = await second_tenant(managers)
    retried = make_item().model_copy(update={"created_by": ctx.user_id})
    exhausted = make_item().model_copy(update={"created_by": ctx.user_id, "max_attempts": 1})
    bobs = make_item().model_copy(update={"created_by": bob.user_id})
    await managers.work.enqueue(ctx, retried)
    await managers.work.enqueue(ctx, exhausted)
    await managers.work.enqueue(bob, bobs)
    expired = timedelta(seconds=-1)
    for _ in range(3):
        claimed = await managers.work.claim(request(), "default", [WorkKind.NOOP], "w1", expired)
        assert claimed is not None

    # The batch bounds one call; the next call takes the rest.
    assert await managers.work.requeue_stale(request(), limit=2) == 2
    assert await managers.work.requeue_stale(request(), limit=2) == 1
    assert await managers.work.requeue_stale(request(), limit=2) == 0

    events = await managers.events.get_events(ctx, after_seq=0, limit=10)
    assert [(e.kind, e.target_id, e.payload["last_error"]) for e in events] == [
        (DEAD_LETTER_KIND, exhausted.id, "lease expired")
    ]
    assert events[0].actor_id == EMPTY_UUID, "the sweep's service context, the system user"
    assert [(p.org_id, p.kind) for p in seen if isinstance(p, EntityChangedPayload)] == [
        (ctx.org_id, DEAD_LETTER_KIND)
    ]
    assert await managers.events.get_events(bob, after_seq=0, limit=10) == []
    ids = set()
    for _ in range(2):
        again = await managers.work.claim(request(), "default", [WorkKind.NOOP], "w2", LEASE)
        assert again is not None and again[1].attempts == 2
        ids.add(again[1].id)
    assert ids == {retried.id, bobs.id}
    assert await managers.work.claim(request(), "default", [WorkKind.NOOP], "w2", LEASE) is None


async def test_a_dead_letter_in_a_gone_tenant_is_counted_without_an_event(
    managers: Managers, storage: StorageMemoryImpl, ctx: OpContext
) -> None:
    # A tenant that is gone has no stream to write into, as the claim's
    # orphan says: the requeue fails the item and counts it, and the other
    # tenants' items still move.
    bob = await second_tenant(managers)
    doomed = make_item().model_copy(update={"created_by": ctx.user_id, "max_attempts": 1})
    bobs = make_item().model_copy(update={"created_by": bob.user_id})
    await managers.work.enqueue(ctx, doomed)
    await managers.work.enqueue(bob, bobs)
    expired = timedelta(seconds=-1)
    for _ in range(2):
        claimed = await managers.work.claim(request(), "default", [WorkKind.NOOP], "w1", expired)
        assert claimed is not None
    org = await storage.get_tenancy_storage().read_org(ctx.org_id)
    assert org is not None
    await storage.get_tenancy_storage().write_org(
        ctx.org_id, org.model_copy(update={"deleted_at": utcnow(), "deleted_by": ctx.user_id})
    )

    counted = OUTCOMES.labels(subsystem="work", outcome="dead_letter")._value.get()
    assert await managers.work.requeue_stale(request(), limit=100) == 2
    assert OUTCOMES.labels(subsystem="work", outcome="dead_letter")._value.get() == counted + 1
    failed = await storage.get_work_storage().read_item(ctx.org_id, doomed.id)
    assert failed is not None and failed.status is WorkStatus.FAILED
    assert await storage.get_event_storage().read_after(ctx.org_id, 0, 10) == []
    moved = await storage.get_work_storage().read_item(bob.org_id, bobs.id)
    assert moved is not None and moved.status is WorkStatus.QUEUED


async def test_enqueue_stamps_the_actor_and_clears_the_claim_whatever_the_caller_sent(
    managers: Managers, ctx: OpContext
) -> None:
    sent = make_item().model_copy(
        update={
            "created_by": new_id(),
            "updated_by": new_id(),
            "status": WorkStatus.CLAIMED,
            "claimed_by": "somebody",
            "lease_expires_at": utcnow() + timedelta(hours=1),
            "attempts": 7,
            "last_error": "not mine to say",
        }
    )
    before = utcnow()
    queued = await managers.work.enqueue(ctx, sent)
    assert queued.created_by == ctx.user_id and queued.updated_by == ctx.user_id
    assert queued.created_at >= before and queued.updated_at == queued.created_at
    assert queued.status is WorkStatus.QUEUED and queued.attempts == 0
    assert queued.claimed_by is None and queued.lease_expires_at is None
    assert queued.last_error is None
    assert queued.id == sent.id and queued.idempotency_key == sent.idempotency_key
    # The claim runs under the enqueuer, not under whoever the payload named.
    claimed = await managers.work.claim(request(), "default", [WorkKind.NOOP], "w1", LEASE)
    assert claimed is not None and claimed[0].user_id == ctx.user_id


async def test_a_retried_enqueue_returns_the_row_as_stored_and_keeps_the_claim(
    managers: Managers, infra: InfraLocalImpl, ctx: OpContext
) -> None:
    seen: list[TopicPayload] = []

    async def record(payload: TopicPayload) -> None:
        seen.append(payload)

    infra.get_topics().subscribe(Topics.WORK_AVAILABLE, "test", record)
    item = make_item().model_copy(update={"created_by": ctx.user_id})
    await managers.work.enqueue(ctx, item)
    claimed = await managers.work.claim(request(), "default", [WorkKind.NOOP], "w1", LEASE)
    assert claimed is not None
    work_ctx, held = claimed
    # The producer retries: the row is the claimed one, the claim intact, and
    # the queue is not woken a second time.
    again = await managers.work.enqueue(ctx, item)
    assert again == held and again.status is WorkStatus.CLAIMED and again.claimed_by == "w1"
    assert len([p for p in seen if isinstance(p, WorkAvailablePayload)]) == 1
    done = await managers.work.complete(work_ctx, held)
    assert done.status is WorkStatus.DONE


async def test_a_reused_idempotency_key_returns_the_row_it_named(
    managers: Managers, infra: InfraLocalImpl, ctx: OpContext
) -> None:
    """A reused key is a retry, not a conflict: the insert reports it, the
    manager reads the row back, and the queue is not woken a second time. It is
    what makes an enqueue that runs twice under one key leave one item."""
    seen: list[TopicPayload] = []

    async def record(payload: TopicPayload) -> None:
        seen.append(payload)

    infra.get_topics().subscribe(Topics.WORK_AVAILABLE, "test", record)
    item = make_item().model_copy(update={"created_by": ctx.user_id})
    queued = await managers.work.enqueue(ctx, item)
    duplicate = make_item().model_copy(
        update={"created_by": ctx.user_id, "idempotency_key": item.idempotency_key}
    )
    assert await managers.work.enqueue(ctx, duplicate) == queued
    assert len([p for p in seen if isinstance(p, WorkAvailablePayload)]) == 1


async def test_enqueue_refuses_a_payload_outside_the_kinds_shape(
    managers: Managers, ctx: OpContext
) -> None:
    # WORK_PAYLOADS fixes the shape per kind; NOOP carries nothing.
    item = make_item().model_copy(update={"created_by": ctx.user_id, "payload": {"extra": 1}})
    with pytest.raises(ValidationFailed):
        await managers.work.enqueue(ctx, item)


async def test_a_failed_item_is_a_dead_letter_with_an_audit_event(
    managers: Managers, infra: InfraLocalImpl, ctx: OpContext
) -> None:
    seen: list[TopicPayload] = []

    async def record(payload: TopicPayload) -> None:
        seen.append(payload)

    infra.get_topics().subscribe(Topics.ENTITY_CHANGED, "test", record)
    item = make_item().model_copy(update={"created_by": ctx.user_id, "max_attempts": 1})
    await managers.work.enqueue(ctx, item)
    claimed = await managers.work.claim(request(), "default", [WorkKind.NOOP], "w1", LEASE)
    assert claimed is not None
    work_ctx, claimed_item = claimed
    failed = await managers.work.fail(work_ctx, claimed_item, "boom")
    assert failed.status is WorkStatus.FAILED and failed.updated_by == EMPTY_UUID
    events = await managers.events.get_events(ctx, after_seq=0, limit=10)
    assert [(e.kind, e.target_id, e.payload["last_error"]) for e in events] == [
        (DEAD_LETTER_KIND, item.id, "boom")
    ]
    assert [(p.kind, p.seq) for p in seen if isinstance(p, EntityChangedPayload)] == [
        (DEAD_LETTER_KIND, 1)
    ]


async def test_a_failure_for_good_is_a_dead_letter_with_attempts_left(
    managers: Managers, ctx: OpContext
) -> None:
    """A refusal is failed at once: the attempt the claim spent stays spent,
    the rest go unused, and the dead letter is the one an exhausted item
    leaves."""
    item = make_item().model_copy(update={"created_by": ctx.user_id, "max_attempts": 3})
    await managers.work.enqueue(ctx, item)
    claimed = await managers.work.claim(request(), "default", [WorkKind.NOOP], "w1", LEASE)
    assert claimed is not None
    failed = await managers.work.fail_for_good(claimed[0], claimed[1], "refused: no")
    assert failed.status is WorkStatus.FAILED and failed.claim_token is None
    assert (failed.attempts, failed.last_error) == (1, "refused: no")
    events = await managers.events.get_events(ctx, after_seq=0, limit=10)
    assert [(e.kind, e.target_id, e.payload["attempts"]) for e in events] == [
        (DEAD_LETTER_KIND, item.id, 1)
    ]
    assert await managers.work.claim(request(), "default", [WorkKind.NOOP], "w1", LEASE) is None


def operator(role: OperatorRole = OperatorRole.WRITE) -> OperatorContext:
    """A test double of the operator stage; admission is the tenancy
    manager's, and this suite is about the queue."""
    return OperatorContext(
        request_id=new_id(),
        app=AppContext(type=AppType.CLI, version="ops@test"),
        identity_id=new_id(),
        email="root@example.test",
        credential_kind=CredentialKind.LOGIN,
        credential_id=new_id(),
        permissions=operator_permissions_of(role),
    )


async def failed_item(managers: Managers, ctx: OpContext) -> tuple[UUID, WorkItem]:
    item = make_item().model_copy(update={"created_by": ctx.user_id, "max_attempts": 3})
    await managers.work.enqueue(ctx, item)
    claimed = await managers.work.claim(request(), "default", [WorkKind.NOOP], "w1", LEASE)
    assert claimed is not None
    failed = await managers.work.fail_for_good(claimed[0], claimed[1], "refused: no")
    return item.id, failed


async def test_an_operator_requeues_a_failed_item_as_a_fresh_one(
    managers: Managers, infra: InfraLocalImpl, ctx: OpContext
) -> None:
    woken: list[TopicPayload] = []

    async def record(payload: TopicPayload) -> None:
        woken.append(payload)

    item_id, failed = await failed_item(managers, ctx)
    infra.get_topics().subscribe(Topics.WORK_AVAILABLE, "test", record)
    admin = operator()
    requeued = await managers.work_operator.requeue(admin, ctx.org_id, item_id)
    assert requeued.status is WorkStatus.QUEUED
    assert (requeued.attempts, requeued.last_error, requeued.claim_token) == (0, None, None)
    assert requeued.available_at <= utcnow()
    assert requeued.updated_by == admin.identity_id, "the operator is the actor"
    assert [p.kind for p in woken if isinstance(p, WorkAvailablePayload)] == ["NOOP"]

    # The diary names the requeue, who made it, and what the item was.
    events = await managers.events.get_events(ctx, after_seq=0, limit=10)
    assert [e.kind for e in events] == [DEAD_LETTER_KIND, REQUEUED_KIND]
    audit = events[-1]
    assert (audit.target_id, audit.actor_id, audit.request_id) == (
        item_id,
        admin.identity_id,
        admin.request_id,
    )
    assert audit.payload["last_error"] == failed.last_error
    assert audit.payload["attempts"] == failed.attempts

    # It runs again, with every attempt it had.
    again = await managers.work.claim(request(), "default", [WorkKind.NOOP], "w1", LEASE)
    assert again is not None and again[1].id == item_id and again[1].attempts == 1


async def test_a_requeue_of_an_item_that_is_not_failed_is_refused(
    managers: Managers, ctx: OpContext
) -> None:
    item_id, _ = await failed_item(managers, ctx)
    await managers.work_operator.requeue(operator(), ctx.org_id, item_id)
    with pytest.raises(WorkNotFailed):  # queued now
        await managers.work_operator.requeue(operator(), ctx.org_id, item_id)
    claimed = await managers.work.claim(request(), "default", [WorkKind.NOOP], "w1", LEASE)
    assert claimed is not None
    with pytest.raises(WorkNotFailed):  # running
        await managers.work_operator.requeue(operator(), ctx.org_id, item_id)
    await managers.work.complete(claimed[0], claimed[1])
    with pytest.raises(WorkNotFailed):  # done
        await managers.work_operator.requeue(operator(), ctx.org_id, item_id)
    events = await managers.events.get_events(ctx, after_seq=0, limit=10)
    assert [e.kind for e in events].count(REQUEUED_KIND) == 1


async def test_a_requeue_takes_the_write_permission_and_a_real_item(
    managers: Managers, ctx: OpContext
) -> None:
    item_id, _ = await failed_item(managers, ctx)
    with pytest.raises(NotAuthorized):
        await managers.work_operator.requeue(operator(OperatorRole.READ), ctx.org_id, item_id)
    with pytest.raises(NotFound):
        await managers.work_operator.requeue(operator(), ctx.org_id, new_id())
    with pytest.raises(NotFound):  # another org's id finds nothing
        await managers.work_operator.requeue(operator(), new_id(), item_id)
    stored = await managers.work_operator.requeue(operator(), ctx.org_id, item_id)
    assert stored.status is WorkStatus.QUEUED


async def test_a_retry_that_still_has_attempts_is_not_a_dead_letter(
    managers: Managers, ctx: OpContext
) -> None:
    item = make_item().model_copy(update={"created_by": ctx.user_id, "max_attempts": 3})
    await managers.work.enqueue(ctx, item)
    claimed = await managers.work.claim(request(), "default", [WorkKind.NOOP], "w1", LEASE)
    assert claimed is not None
    work_ctx, claimed_item = claimed
    requeued = await managers.work.fail(work_ctx, claimed_item, "again")
    assert requeued.status is WorkStatus.QUEUED
    assert await managers.events.get_events(ctx, after_seq=0, limit=10) == []


async def test_purge_items_takes_done_and_failed_items_past_the_retention(
    managers: Managers, storage: StorageMemoryImpl, ctx: OpContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    for _ in range(2):
        await managers.work.enqueue(ctx, make_item().model_copy(update={"created_by": ctx.user_id}))
    exhausted = make_item().model_copy(update={"created_by": ctx.user_id, "max_attempts": 1})
    await managers.work.enqueue(ctx, exhausted)
    first = await managers.work.claim(request(), "default", [WorkKind.NOOP], "w1", LEASE)
    second = await managers.work.claim(request(), "default", [WorkKind.NOOP], "w1", LEASE)
    third = await managers.work.claim(request(), "default", [WorkKind.NOOP], "w1", LEASE)
    assert first is not None and second is not None and third is not None
    done = await managers.work.complete(first[0], first[1])
    failed = await managers.work.fail(third[0], third[1], "boom")
    assert failed.status is WorkStatus.FAILED
    # Under the default retention nothing is old enough; with none, the done
    # and the failed items go and the one still claimed stays.
    assert await managers.work.purge_items() == 0
    monkeypatch.setattr(managers.work, "_options", WorkOptions(retention=timedelta(0)))
    await asyncio.sleep(0.001)
    assert await managers.work.purge_items() == 2
    storage_ = storage.get_work_storage()
    assert await storage_.read_item(ctx.org_id, done.id) is None
    assert await storage_.read_item(ctx.org_id, failed.id) is None
    held = await storage_.read_item(ctx.org_id, second[1].id)
    assert held is not None and held.status is WorkStatus.CLAIMED


async def test_a_claim_in_a_deleted_org_fails_the_item_and_moves_on(
    managers: Managers, storage: StorageMemoryImpl, ctx: OpContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The claim is written before the tenant is checked. A deleted tenant used
    # to leave the row claimed for good: no context to settle it under, and no
    # sweep visited the tenant. The item is failed in the same call, with the
    # reason, and the claim goes on to the next tenant's work.
    tenancy = managers.tenancy
    orphan = make_item().model_copy(update={"created_by": ctx.user_id})
    await managers.work.enqueue(ctx, orphan)
    _, beta = await tenancy.bootstrap(request(APP), "Beta", "beta", "bob@example.test", "Bob")
    bob = await tenancy.authenticate(
        request(APP),
        (
            await tenancy.exchange_login(
                await tenancy.authenticate_login(
                    request(APP),
                    (await tenancy.dev_sign_in(request(APP), "bob@example.test")).token,
                ),
                beta.id,
            )
        ).token,
    )
    bobs = make_item().model_copy(update={"created_by": bob.user_id})
    await managers.work.enqueue(bob, bobs)
    await tenancy.bootstrap(
        request(APP),
        "Ops",
        "ops",
        "root@example.test",
        "Root",
        operator_role=OperatorRole.WRITE,
    )
    # Admitted on the operator token the grant job mints: the claim is the
    # subject here, not how an operator signs in.
    token = await tenancy.grant_operator_token(request(APP), "root@example.test")
    admin = await tenancy.admit_operator(
        await tenancy.authenticate_login(request(APP), token.token)
    )
    # The operator's deletion, carried to its end as the worker ends it.
    await managers.tenancy_operator.delete_org(admin, ctx.org_id)
    await tenancy.delete_closed_org(
        await tenancy.service_context(request(APP), ctx.org_id, admin.identity_id)
    )

    claimed = await managers.work.claim(request(), "default", [WorkKind.NOOP], "w1", LEASE)
    assert claimed is not None and claimed[1].id == bobs.id
    stored = await storage.get_work_storage().read_item(ctx.org_id, orphan.id)
    assert stored is not None
    assert stored.status is WorkStatus.FAILED and stored.claimed_by is None
    assert stored.claim_token is None and stored.last_error == "the org is gone"
    assert stored.updated_by == EMPTY_UUID
    assert await managers.work.claim(request(), "default", [WorkKind.NOOP], "w1", LEASE) is None
    # The sweep's purge reaches the deleted tenant's dead letter past the retention.
    assert await managers.work.purge_items() == 0
    monkeypatch.setattr(managers.work, "_options", WorkOptions(retention=timedelta(0)))
    await asyncio.sleep(0.001)
    assert await managers.work.purge_items() == 1


async def test_every_write_after_the_enqueue_is_the_platforms(
    managers: Managers, storage: StorageMemoryImpl, ctx: OpContext
) -> None:
    """`created_by` is the person who asked for the work and `updated_by` is the
    machinery that ran it: the claim, the renewal, the hand-back, the requeue,
    the failure, and the completion all sign EMPTY_UUID. One lane per item, so
    each claim takes the item its part of the test is about."""
    items = {
        lane: make_item(lane=lane).model_copy(update={"created_by": ctx.user_id})
        for lane in ("held", "stale", "failing")
    }
    for item in items.values():
        await managers.work.enqueue(ctx, item)

    claimed = await managers.work.claim(request(), "held", [WorkKind.NOOP], "w1", LEASE)
    assert claimed is not None
    work_ctx, held = claimed
    assert work_ctx.user_id == ctx.user_id, "the work runs under the enqueuer"
    assert held.created_by == ctx.user_id and held.updated_by == EMPTY_UUID
    renewed = await managers.work.extend_lease(work_ctx, held, LEASE)
    assert renewed.updated_by == EMPTY_UUID
    handed_back = await managers.work.release(work_ctx, renewed)
    assert handed_back.updated_by == EMPTY_UUID
    retaken = await managers.work.claim(request(), "held", [WorkKind.NOOP], "w1", LEASE)
    assert retaken is not None
    done = await managers.work.complete(retaken[0], retaken[1])
    assert done.status is WorkStatus.DONE and done.updated_by == EMPTY_UUID

    expired = timedelta(seconds=-1)
    assert await managers.work.claim(request(), "stale", [WorkKind.NOOP], "w1", expired) is not None
    assert await managers.work.requeue_stale(request(), limit=100) == 1
    requeued = await storage.get_work_storage().read_item(ctx.org_id, items["stale"].id)
    assert requeued is not None and requeued.updated_by == EMPTY_UUID

    failing = await managers.work.claim(request(), "failing", [WorkKind.NOOP], "w1", LEASE)
    assert failing is not None
    failed = await managers.work.fail(failing[0], failing[1], "boom")
    assert failed.updated_by == EMPTY_UUID and failed.created_by == ctx.user_id


async def test_a_transition_writes_the_stored_row_and_not_the_workers_copy(
    managers: Managers, storage: StorageMemoryImpl, ctx: OpContext
) -> None:
    """The copy starts from the stored row: what a worker sends back cannot
    rewrite who asked for the work, when it was asked for, or what it is. The
    note a hand-back carries is the one field the caller supplies."""
    item = make_item().model_copy(update={"created_by": ctx.user_id})
    await managers.work.enqueue(ctx, item)
    claimed = await managers.work.claim(request(), "default", [WorkKind.NOOP], "w1", LEASE)
    assert claimed is not None
    work_ctx, held = claimed
    forged = held.model_copy(
        update={
            "created_by": new_id(),
            "created_at": utcnow() - timedelta(days=9),
            "target_id": new_id(),
            "max_attempts": 99,
            "last_error": "returned: worker stopping",
        }
    )
    released = await managers.work.release(work_ctx, forged)
    assert released.created_by == ctx.user_id and released.created_at == held.created_at
    assert released.target_id == item.target_id and released.max_attempts == item.max_attempts
    assert released.last_error == "returned: worker stopping"
    stored = await storage.get_work_storage().read_item(ctx.org_id, item.id)
    assert stored == released


async def test_the_gauges_read_the_backlog_and_the_dead_letters_of_late(
    managers: Managers, ctx: OpContext
) -> None:
    """The age of the item ready longest, zero when nothing waits, and the
    items failed within a window, which an operator's requeue takes back out."""
    assert await managers.work.oldest_ready_age() == timedelta(0)
    assert await managers.work.failed_within(timedelta(minutes=15)) == 0
    waiting = make_item(available_in=-timedelta(minutes=12))
    await managers.work.enqueue(ctx, waiting.model_copy(update={"created_by": ctx.user_id}))
    parked = make_item(available_in=timedelta(hours=1))
    await managers.work.enqueue(ctx, parked.model_copy(update={"created_by": ctx.user_id}))
    age = await managers.work.oldest_ready_age()
    assert timedelta(minutes=12) <= age < timedelta(minutes=13)
    claimed = await managers.work.claim(request(), "default", [WorkKind.NOOP], "w1", LEASE)
    assert claimed is not None
    assert await managers.work.oldest_ready_age() == timedelta(0)
    failed = await managers.work.fail_for_good(claimed[0], claimed[1], "refused: no")
    assert await managers.work.failed_within(timedelta(minutes=15)) == 1
    assert await managers.work.failed_within(timedelta(0)) == 0
    await managers.work_operator.requeue(operator(), ctx.org_id, failed.id)
    assert await managers.work.failed_within(timedelta(minutes=15)) == 0
