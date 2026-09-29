"""The realtime service hears every change on the bus and ends the sockets a
revocation names: the one the session or the api key opened, every one of a
user whose membership ended or whose role changed, or every one of a deleted
org, and no other. It re-checks a socket's credential when asked, the plan of
an api key among it, and wakes that recheck when the org's account changes.
It answers a ping with the head it heard, reading it only when that is
unknown or old."""

from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from uuid import UUID

import pytest
from api_support import OWNER, add_member, build_container, on_plan, seed_request

from acme.infra.topics import EntityChangedPayload, Topics
from acme.om.base import new_id, utcnow
from acme.om.billing.types.plan import Plan
from acme.om.events.types.event import Event
from acme.om.opcontext import OpContext, Role
from acme.om.tenancy.rules import hash_token
from acme.om.tenancy.types.socket_ticket import SocketPrincipal
from acme.services.api.container import AppContainer
from acme.services.api.services.impl.realtime import RealtimeServiceImpl
from acme.services.api.services.realtime import (
    CREDENTIAL_REVOKED,
    MEMBERSHIP_ENDED,
    RIGHTS_CHANGED,
)
from acme.services.api.types.tasks import AddTaskRequest


async def test_a_revocation_ends_the_sockets_it_names_and_no_other(tmp_path: Path) -> None:
    container = build_container(tmp_path)
    tenancy = container.managers.tenancy
    _, org = await tenancy.bootstrap(seed_request(), "Ajax", "ajax", OWNER["email"], OWNER["name"])
    await on_plan(container, org.id, Plan.TEAM)
    bob = await add_member(container, org.id, "bob@example.test", Role.MEMBER)

    async def session_of(email: str) -> str:
        login = await tenancy.dev_sign_in(seed_request(), email)
        identity = await tenancy.authenticate_login(seed_request(), login.token)
        return (await tenancy.exchange_login(identity, org.id)).token

    async def socket_principal(credential: str) -> SocketPrincipal:
        """The principal a socket opened on `credential` runs as."""
        ctx = await tenancy.authenticate(seed_request(), credential)
        ticket = await tenancy.issue_ticket(ctx)
        return await tenancy.redeem_ticket(seed_request(), ticket.ticket)

    ann_first = await socket_principal(await session_of(OWNER["email"]))
    ann_second = await socket_principal(await session_of(OWNER["email"]))
    bob_first = await socket_principal(await session_of("bob@example.test"))
    bob_second = await socket_principal(await session_of("bob@example.test"))
    owner = await tenancy.authenticate(seed_request(), await session_of(OWNER["email"]))
    key = await tenancy.create_api_key(owner, "ci", Role.MEMBER)
    from_key = await socket_principal(key.key)

    realtime = container.services.get_realtime_service()
    ended: dict[str, list[str]] = {}
    detach: dict[str, Callable[[], None]] = {}
    for name, principal in {
        "ann_first": ann_first,
        "ann_second": ann_second,
        "bob_first": bob_first,
        "bob_second": bob_second,
        "from_key": from_key,
    }.items():
        ended[name] = []
        detach[name] = realtime.attach(principal, ended[name].append)
    detach["bob_second"]()

    topics = container.infra.get_topics()

    async def announce(kind: str, target_id: UUID, org_id: UUID = org.id) -> None:
        await topics.publish(
            Topics.ENTITY_CHANGED,
            EntityChangedPayload(
                idempotency_key=new_id(),
                produced_at=utcnow(),
                org_id=org_id,
                kind=kind,
                target_id=target_id,
                seq=1,
                actor_id=owner.user_id,
            ),
        )

    # Another tenant's frame with the same ids ends nothing; nor does a change of another kind.
    await announce("tenancy.session.revoked", ann_first.ctx.credential_id, org_id=new_id())
    await announce("tasks.task.updated", ann_first.ctx.credential_id)
    # A membership's change names the membership, never the user.
    await announce("tenancy.membership.updated", bob.id)
    assert all(reasons == [] for reasons in ended.values())

    await announce("tenancy.session.revoked", ann_first.ctx.credential_id)
    assert ended["ann_first"] == [CREDENTIAL_REVOKED]
    assert ended["ann_second"] == [] and ended["from_key"] == []

    await announce("tenancy.api_key.deleted", from_key.ctx.credential_id)
    assert ended["from_key"] == [CREDENTIAL_REVOKED]

    # The member's membership ended: every socket of theirs still attached ends.
    await announce("tenancy.user.deleted", bob.id)
    assert ended["bob_first"] == [MEMBERSHIP_ENDED]
    assert ended["bob_second"] == []  # detached before the frame
    assert ended["ann_second"] == []  # another user

    # The org was deleted: every socket of the tenant still attached ends, and
    # another tenant's deletion ends none of them.
    other_org = new_id()
    await announce("tenancy.org.deleted", other_org, org_id=other_org)
    assert ended["ann_second"] == []
    await announce("tenancy.org.deleted", org.id)
    assert ended["ann_second"] == [MEMBERSHIP_ENDED]
    assert ended["bob_second"] == []  # detached before the frame


async def session_of(container: AppContainer, org_id: UUID, email: str) -> str:
    tenancy = container.managers.tenancy
    login = await tenancy.dev_sign_in(seed_request(), email)
    identity = await tenancy.authenticate_login(seed_request(), login.token)
    return (await tenancy.exchange_login(identity, org_id)).token


async def principal_of(container: AppContainer, credential: str) -> SocketPrincipal:
    """What redeeming a ticket minted on `credential` yields, as the gateway gets it."""
    tenancy = container.managers.tenancy
    ctx = await tenancy.authenticate(seed_request(), credential)
    ticket = await tenancy.issue_ticket(ctx)
    return await tenancy.redeem_ticket(seed_request(), ticket.ticket)


async def test_a_change_of_role_ends_the_members_sockets_to_reconnect(tmp_path: Path) -> None:
    """The bus names the membership whose role changed. Every socket built
    from it ends with the reason that asks the client to reconnect, the
    member's api key's among them, since a key's role is capped by the
    member's; another member's socket stays."""
    container = build_container(tmp_path)
    tenancy = container.managers.tenancy
    _, org = await tenancy.bootstrap(seed_request(), "Ajax", "ajax", OWNER["email"], OWNER["name"])
    await on_plan(container, org.id, Plan.TEAM)
    bob = await add_member(container, org.id, "bob@example.test", Role.ADMIN)
    owner = await tenancy.authenticate(
        seed_request(), await session_of(container, org.id, OWNER["email"])
    )
    bob_session = await session_of(container, org.id, "bob@example.test")
    bob_ctx = await tenancy.authenticate(seed_request(), bob_session)
    bob_key = await tenancy.create_api_key(bob_ctx, "ci", Role.MEMBER)
    realtime = container.services.get_realtime_service()
    ended: dict[str, list[str]] = {"bob": [], "bob_key": [], "ann": []}
    realtime.attach(await principal_of(container, bob_session), ended["bob"].append)
    realtime.attach(await principal_of(container, bob_key.key), ended["bob_key"].append)
    realtime.attach(
        await principal_of(container, await session_of(container, org.id, OWNER["email"])),
        ended["ann"].append,
    )

    await tenancy.update_membership_role(owner, bob.id, Role.MEMBER)

    assert ended == {"bob": [RIGHTS_CHANGED], "bob_key": [RIGHTS_CHANGED], "ann": []}


async def test_the_recheck_answers_what_the_redemption_would(tmp_path: Path) -> None:
    """None while the credential holds as it did; the change of role; the
    refusal once the session is revoked, idle, or the key deleted. Each
    change is made in storage alone, as when the bus lost its message."""
    container = build_container(tmp_path)
    tenancy = container.managers.tenancy
    storage = container.storage.get_tenancy_storage()
    _, org = await tenancy.bootstrap(seed_request(), "Ajax", "ajax", OWNER["email"], OWNER["name"])
    await on_plan(container, org.id, Plan.TEAM)
    bob = await add_member(container, org.id, "bob@example.test", Role.ADMIN)
    realtime = container.services.get_realtime_service()

    token = await session_of(container, org.id, OWNER["email"])
    principal = await principal_of(container, token)
    assert await realtime.recheck(principal) is None

    # Revoked in storage, with no message on any bus.
    found = await storage.read_session_by_digest(hash_token(token))
    assert found is not None
    _, session = found
    await storage.write_session(org.id, session.model_copy(update={"revoked_at": utcnow()}))
    assert await realtime.recheck(principal) == "not_authenticated"

    # A session idle past its lifetime is refused as a request would be.
    idle_token = await session_of(container, org.id, OWNER["email"])
    idle = await principal_of(container, idle_token)
    found = await storage.read_session_by_digest(hash_token(idle_token))
    assert found is not None
    _, session = found
    idle_lifetime = timedelta(seconds=container.settings.session_idle_lifetime_seconds)
    long_ago = utcnow() - idle_lifetime - timedelta(seconds=1)
    await storage.write_session(org.id, session.model_copy(update={"last_seen_at": long_ago}))
    assert await realtime.recheck(idle) == "not_authenticated"

    # The role, changed in storage alone.
    bob_principal = await principal_of(
        container, await session_of(container, org.id, "bob@example.test")
    )
    membership = await storage.read_membership_for_user(org.id, bob.id)
    assert membership is not None
    await storage.write_membership(org.id, membership.model_copy(update={"role": Role.MEMBER}), ())
    assert await realtime.recheck(bob_principal) == RIGHTS_CHANGED

    owner = await tenancy.authenticate(
        seed_request(), await session_of(container, org.id, OWNER["email"])
    )
    key = await tenancy.create_api_key(owner, "ci", Role.MEMBER)
    from_key = await principal_of(container, key.key)
    assert await realtime.recheck(from_key) is None
    await tenancy.revoke_api_key(owner, key.api_key.id)
    assert await realtime.recheck(from_key) == "not_authenticated"


async def put_on(container: AppContainer, org_id: UUID, plan: Plan | None) -> None:
    """Changes the org's plan in storage alone, as a downgrade whose message
    the bus lost: no row, so nothing is announced. None is no grant: Free."""
    billing = container.storage.get_billing_storage()
    account = await billing.read_account(org_id)
    assert account is not None
    await billing.write_account(org_id, account.model_copy(update={"comped_plan": plan}), ())


async def test_the_recheck_refuses_a_key_its_plan_no_longer_allows(tmp_path: Path) -> None:
    """A downgrade to a plan without api keys refuses the key's socket as it
    refuses the key's every request, with the plan's refusal; the session's
    socket in the same org holds. The key is kept, so it holds again on a
    plan with keys."""
    container = build_container(tmp_path)
    tenancy = container.managers.tenancy
    _, org = await tenancy.bootstrap(seed_request(), "Ajax", "ajax", OWNER["email"], OWNER["name"])
    await on_plan(container, org.id, Plan.TEAM)
    token = await session_of(container, org.id, OWNER["email"])
    key = await tenancy.create_api_key(
        await tenancy.authenticate(seed_request(), token), "ci", Role.MEMBER
    )
    from_session = await principal_of(container, token)
    from_key = await principal_of(container, key.key)
    realtime = container.services.get_realtime_service()

    await put_on(container, org.id, None)
    assert await realtime.recheck(from_key) == "plan_limit_reached"
    assert await realtime.recheck(from_session) is None

    await put_on(container, org.id, Plan.PRO)
    assert await realtime.recheck(from_key) is None


async def test_a_change_of_the_account_wakes_the_recheck_of_key_sockets(tmp_path: Path) -> None:
    """The account's change carries no plan, so it closes nothing itself: it
    wakes the recheck of every socket an api key of the org opened, and of
    no session's socket and no other org's."""
    container = build_container(tmp_path)
    tenancy = container.managers.tenancy
    _, org = await tenancy.bootstrap(seed_request(), "Ajax", "ajax", OWNER["email"], OWNER["name"])
    await on_plan(container, org.id, Plan.TEAM)
    token = await session_of(container, org.id, OWNER["email"])
    owner = await tenancy.authenticate(seed_request(), token)
    key = await tenancy.create_api_key(owner, "ci", Role.MEMBER)
    realtime = container.services.get_realtime_service()
    woken: dict[str, int] = {"session": 0, "key": 0}
    ended: list[str] = []

    def wake(name: str) -> Callable[[], None]:
        def recheck_now() -> None:
            woken[name] += 1

        return recheck_now

    realtime.attach(await principal_of(container, token), ended.append, wake("session"))
    realtime.attach(await principal_of(container, key.key), ended.append, wake("key"))

    async def announce(org_id: UUID) -> None:
        await container.infra.get_topics().publish(
            Topics.ENTITY_CHANGED,
            EntityChangedPayload(
                idempotency_key=new_id(),
                produced_at=utcnow(),
                org_id=org_id,
                kind="billing.account.updated",
                target_id=new_id(),
                seq=1,
                actor_id=owner.user_id,
            ),
        )

    await announce(new_id())
    assert woken == {"session": 0, "key": 0}
    await announce(org.id)
    assert woken == {"session": 0, "key": 1}
    assert ended == []


async def test_the_recheck_is_not_a_use_of_the_session(tmp_path: Path) -> None:
    """A recheck asks; it does not count as a use. An open socket never keeps
    an idle session alive, so a tab left open is signed out at the idle
    lifetime exactly as a tab left closed is."""
    container = build_container(tmp_path)
    tenancy = container.managers.tenancy
    storage = container.storage.get_tenancy_storage()
    _, org = await tenancy.bootstrap(seed_request(), "Ajax", "ajax", OWNER["email"], OWNER["name"])
    token = await session_of(container, org.id, OWNER["email"])
    principal = await principal_of(container, token)
    found = await storage.read_session_by_digest(hash_token(token))
    assert found is not None
    _, session = found
    seen = utcnow() - timedelta(hours=1)
    await storage.write_session(org.id, session.model_copy(update={"last_seen_at": seen}))

    assert await container.services.get_realtime_service().recheck(principal) is None

    found = await storage.read_session_by_digest(hash_token(token))
    assert found is not None
    assert found[1].last_seen_at == seen


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Heads:
    """A realtime service of its own over the container's managers and bus,
    on a clock the test moves, with every read of the head counted."""

    def __init__(self, container: AppContainer, max_age: float) -> None:
        self.clock = Clock()
        self.reads = 0
        events = container.managers.events
        read = events.get_head

        async def counted(ctx: OpContext) -> int:
            self.reads += 1
            return await read(ctx)

        self._patch = pytest.MonkeyPatch()
        self._patch.setattr(events, "get_head", counted)
        self.service = RealtimeServiceImpl(
            container.managers.tenancy,
            events,
            container.infra.get_topics(),
            timedelta(seconds=max_age),
            self.clock,
        )

    def close(self) -> None:
        self._patch.undo()


async def owner_socket(container: AppContainer) -> tuple[OpContext, SocketPrincipal]:
    tenancy = container.managers.tenancy
    _, org = await tenancy.bootstrap(seed_request(), "Ajax", "ajax", OWNER["email"], OWNER["name"])
    token = await session_of(container, org.id, OWNER["email"])
    owner = await tenancy.authenticate(seed_request(), token)
    return owner, await principal_of(container, token)


async def add_task(container: AppContainer, owner: OpContext, title: str) -> None:
    """A write the relay announces on the bus: its event gets the next seq."""
    tasks = container.services.get_tasks_service()
    await tasks.create_task(owner, AddTaskRequest(title=title), new_id())


async def append_unheard(container: AppContainer, owner: OpContext) -> None:
    """An event the bus never carried to this process: its subscription was
    down, or the publish was lost. The head moves; nothing is heard."""
    await container.managers.events.append_event(
        owner,
        Event(
            id=new_id(),
            org_id=owner.org_id,
            kind="tasks.task.updated",
            target_id=new_id(),
            produced_at=utcnow(),
            actor_id=owner.user_id,
            request_id=new_id(),
            app="portal",
        ),
    )


async def test_a_process_that_just_started_reads_the_head(tmp_path: Path) -> None:
    """Nothing heard yet for the tenant: the pong reads, and what it read
    answers the next ping."""
    container = build_container(tmp_path)
    owner, principal = await owner_socket(container)
    await add_task(container, owner, "before this process")
    heads = Heads(container, max_age=60)
    try:
        heads.service.attach(principal, lambda reason: None)
        assert await heads.service.pong_head(owner) == 1
        assert heads.reads == 1
        assert await heads.service.pong_head(owner) == 1
        assert heads.reads == 1
    finally:
        heads.close()


async def test_the_pong_answers_from_the_bus_without_a_read(tmp_path: Path) -> None:
    """Each hint carries its seq; the process keeps the highest it heard for
    a tenant it holds a socket for, and a ping answers with it."""
    container = build_container(tmp_path)
    owner, principal = await owner_socket(container)
    heads = Heads(container, max_age=60)
    try:
        heads.service.attach(principal, lambda reason: None)
        assert await heads.service.head(owner) == 0  # the hello's read
        await add_task(container, owner, "one")
        await add_task(container, owner, "two")
        heads.clock.now += 59
        assert await heads.service.pong_head(owner) == 2
        assert heads.reads == 1
    finally:
        heads.close()


async def test_a_hint_never_heard_is_hidden_no_longer_than_the_bound(tmp_path: Path) -> None:
    """The process's subscription dropped, or a publish was lost: the head
    moved and nothing was heard. Within the bound the pong answers the head
    heard, one below the truth; past it, the pong reads and the client sees
    the gap. A later hint shows it at once, since its seq is past the gap."""
    container = build_container(tmp_path)
    owner, principal = await owner_socket(container)
    heads = Heads(container, max_age=60)
    try:
        heads.service.attach(principal, lambda reason: None)
        await add_task(container, owner, "heard")
        assert await heads.service.pong_head(owner) == 1
        assert heads.reads == 0  # heard on the bus

        await append_unheard(container, owner)
        heads.clock.now += 30
        assert await heads.service.pong_head(owner) == 1  # the bound, not yet past
        heads.clock.now += 31
        assert await heads.service.pong_head(owner) == 2  # read: the gap shows
        assert heads.reads == 1

        await append_unheard(container, owner)
        await add_task(container, owner, "heard after a loss")
        assert await heads.service.pong_head(owner) == 4
        assert heads.reads == 1
    finally:
        heads.close()


async def test_the_pong_never_answers_below_what_it_heard(tmp_path: Path) -> None:
    """A hint that arrives late, below the head heard, changes nothing, and
    neither does a read that returns less than a hint heard meanwhile."""
    container = build_container(tmp_path)
    owner, principal = await owner_socket(container)
    heads = Heads(container, max_age=60)
    try:
        heads.service.attach(principal, lambda reason: None)
        await add_task(container, owner, "one")
        await add_task(container, owner, "two")
        await container.infra.get_topics().publish(
            Topics.ENTITY_CHANGED,
            EntityChangedPayload(
                idempotency_key=new_id(),
                produced_at=utcnow(),
                org_id=owner.org_id,
                kind="tasks.task.updated",
                target_id=new_id(),
                seq=1,
                actor_id=owner.user_id,
            ),
        )
        assert await heads.service.pong_head(owner) == 2
        assert heads.reads == 0
    finally:
        heads.close()


async def test_a_bound_of_zero_reads_on_every_ping(tmp_path: Path) -> None:
    container = build_container(tmp_path)
    owner, principal = await owner_socket(container)
    heads = Heads(container, max_age=0)
    try:
        heads.service.attach(principal, lambda reason: None)
        await add_task(container, owner, "one")
        assert await heads.service.pong_head(owner) == 1
        assert await heads.service.pong_head(owner) == 1
        assert heads.reads == 2
    finally:
        heads.close()


async def test_the_head_is_kept_only_while_the_tenant_has_a_socket_here(tmp_path: Path) -> None:
    """A tenant with no socket in this process costs it nothing: its hints
    are not kept, and the head goes with the tenant's last socket. The next
    socket reads afresh."""
    container = build_container(tmp_path)
    owner, principal = await owner_socket(container)
    heads = Heads(container, max_age=60)
    try:
        await add_task(container, owner, "no socket here")
        first = heads.service.attach(principal, lambda reason: None)
        second = heads.service.attach(principal, lambda reason: None)
        assert await heads.service.pong_head(owner) == 1
        assert heads.reads == 1
        first()
        await add_task(container, owner, "one socket left")
        assert await heads.service.pong_head(owner) == 2
        assert heads.reads == 1
        second()
        await add_task(container, owner, "none left")
        heads.service.attach(principal, lambda reason: None)
        assert await heads.service.pong_head(owner) == 3
        assert heads.reads == 2
    finally:
        heads.close()
