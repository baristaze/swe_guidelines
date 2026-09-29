"""A deleted account's work in the worker: the person gone at the identity
provider, the personal org's subscription canceled and its customer deleted,
its Slack app removed, the org deleted and then purged whole by the sweep;
the person's open tasks in a team org unassigned; and a provider that is
down, or refusing the process's key, parking the work until it answers;
and a provider that refuses the call itself failing it at once."""

from datetime import timedelta
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest
from slack_support import build, install, make_task, on_team, owner_of
from test_billing_work import checkout, consumer_of, queued
from worker_support import request

from acme.infra.buckets import Buckets
from acme.infra.cache import CacheScope
from acme.integrations.exceptions import (
    PaymentsKeyRefused,
    PaymentsRefused,
    ProviderConflict,
    ProviderRefused,
    ProviderUnavailable,
)
from acme.integrations.identity.twin import IdentityProviderTwinImpl
from acme.integrations.payments.twin import PaymentsTwinImpl
from acme.om.base import new_id, utcnow
from acme.om.billing.types.plan import Plan
from acme.om.media.types.file import File, FilePurpose
from acme.om.opcontext import OpContext, Role
from acme.om.tasks.types.task import TaskStatus
from acme.om.tenancy.impl.manager import TenancyManagerImpl, TenancyOptions
from acme.om.work.types.handler import WorkParked, WorkRefused
from acme.om.work.types.work_item import WorkItem, WorkKind
from acme.workers.maintenance.container import WorkerContainer
from acme.workers.maintenance.main import build_loop

LEASE = timedelta(seconds=30)
ACCOUNT_WORK = [WorkKind.DELETE_ACCOUNT, WorkKind.UNASSIGN_TASKS, WorkKind.SYNC_SEATS]


def identity_of(container: WorkerContainer) -> IdentityProviderTwinImpl:
    return cast(IdentityProviderTwinImpl, container.identity_provider)


async def bob_signs_in(container: WorkerContainer, org_id: UUID) -> tuple[OpContext, OpContext]:
    """Bob, a member of the org, signs in through the provider's twin, which
    then knows him by a subject: his session in the org, and in his personal
    org."""
    tenancy = container.managers.tenancy
    await tenancy.add_member(request(), "ajax", "bob@example.test", "Bob", Role.MEMBER)
    signing = TenancyManagerImpl(
        container.storage.get_tenancy_storage(),
        container.managers.outbox,
        container.infra.get_cache(CacheScope.REALTIME_TICKET),
        TenancyOptions(),
        identity_provider=container.identity_provider,
        entitlements=container.managers.billing,
    )
    places: list[OpContext] = []
    login = await signing.sign_in_with_code(
        request(), identity_of(container).issue_code("bob@example.test")
    )
    home = next(m.org.id for m in login.memberships if m.org.personal)
    for target in (org_id, home):
        code = identity_of(container).issue_code("bob@example.test")
        login = await signing.sign_in_with_code(request(), code)
        identity = await tenancy.authenticate_login(request(), login.token)
        issued = await tenancy.exchange_login(identity, target)
        places.append(await tenancy.authenticate(request(), issued.token))
    return places[0], places[1]


async def attach(container: WorkerContainer, ctx: OpContext, task_id: UUID) -> File:
    data = b"\x89PNG\r\n\x1a\n" + b"0" * 64
    now = utcnow()
    file = await container.managers.tasks.attach_file(
        ctx,
        task_id,
        File(
            id=new_id(),
            name="plan.png",
            created_at=now,
            updated_at=now,
            created_by=ctx.user_id,
            updated_by=ctx.user_id,
            content_type="image/png",
            size_bytes=len(data),
            purpose=FilePurpose.TASK_ATTACHMENT,
        ),
    )
    await container.managers.media.put_content(ctx, file.id, data)
    return await container.managers.media.confirm_file(ctx, file.id)


async def run(container: WorkerContainer) -> list[WorkItem]:
    """Claims every account item and runs it with the worker's own handler,
    settling it as the loop does."""
    handlers = build_loop(container)._handlers
    ran: list[WorkItem] = []
    while True:
        claimed = await container.managers.work.claim(
            request(), "default", ACCOUNT_WORK, "test", LEASE
        )
        if claimed is None:
            return ran
        ctx, item = claimed
        await handlers[item.kind].handle(ctx, item)
        await container.managers.work.complete(ctx, item)
        ran.append(item)


async def test_a_deleted_account_leaves_nothing_of_its_person_behind(tmp_path: Path) -> None:
    container, slack = build(tmp_path)
    tasks, tenancy = container.managers.tasks, container.managers.tenancy
    ann = await owner_of(container, "ajax")
    await on_team(container, ann)
    bob, home = await bob_signs_in(container, ann.org_id)
    subject = (await tenancy.get_identity(bob)).subject
    assert subject is not None and subject in identity_of(container).users

    # In Ajax: a task assigned to Bob, one he made and left open, one of Ann's.
    his = await tasks.create_task(bob, make_task(ann, "Ship it", assignee_id=bob.user_id))
    made = await tasks.create_task(bob, make_task(bob, "Bob's idea"))
    anns = await tasks.create_task(ann, make_task(ann, "Ann's", assignee_id=ann.user_id))
    done = await tasks.create_task(bob, make_task(bob, "Done", assignee_id=bob.user_id))
    done = await tasks.update_task(
        bob, done.model_copy(update={"status": TaskStatus.DONE}), done.version
    )
    # At home: a task with a file, a paid plan, and the Slack app.
    note = await tasks.create_task(home, make_task(home, "Groceries"))
    file = await attach(container, home, note.id)
    buckets = container.infra.get_buckets()
    assert await buckets.exists(home.org_id, Buckets.USER_FILE_UPLOADS, file.key)
    payload = await checkout(container, home, Plan.PRO, 1)
    assert await consumer_of(container).handle(await queued(container, payload)) == "applied"
    account = (await container.managers.billing.get_billing(home)).account
    assert account is not None and account.customer_id and account.subscription_id
    await install(container, slack, home)
    assert await container.managers.slack.get_installation(home) is not None

    await tenancy.delete_account(bob, "bob@example.test")
    ran = await run(container)
    assert {item.kind for item in ran} >= {WorkKind.DELETE_ACCOUNT, WorkKind.UNASSIGN_TASKS}

    # The provider's side is gone.
    assert identity_of(container).deleted == [subject]
    payments = cast(PaymentsTwinImpl, container.payments)
    assert account.customer_id not in payments.customers
    assert payments.subscriptions[account.subscription_id]["status"] == "canceled"
    assert slack.uninstalled, "the app left the workspace"
    # The personal org is deleted, and the sweep purges it whole at its next pass.
    org = await container.storage.get_tenancy_storage().read_org(home.org_id)
    assert org is not None and org.deleted_at is not None
    loop = build_loop(container)
    await loop._sweep_once()
    assert not await buckets.exists(home.org_id, Buckets.USER_FILE_UPLOADS, file.key)
    assert await container.storage.get_tasks_storage().read_task(home.org_id, note.id) is None
    assert await container.storage.get_billing_storage().read_account(home.org_id) is None
    assert await container.managers.slack.get_installation(home) is None
    # The next pass finds nothing left, and the sweep leaves the org out.
    await loop._sweep_once()
    service = await tenancy.service_contexts(request())
    assert home.org_id not in {ctx.org_id for ctx in service}

    # In Ajax his footprint stays, by id: his open task is nobody's now.
    released = await tasks.get_task(ann, his.id)
    assert released.assignee_id is None and released.updated_by == bob.user_id
    assert (await tasks.get_task(ann, made.id)).created_by == bob.user_id
    assert (await tasks.get_task(ann, anns.id)).assignee_id == ann.user_id
    assert (await tasks.get_task(ann, done.id)).assignee_id == bob.user_id, "only open tasks"
    with pytest.raises(Exception):  # noqa: B017 (no user by that id any more)
        await tenancy.get_user(ann, bob.user_id)


async def test_a_provider_that_is_down_parks_the_work_until_it_answers(tmp_path: Path) -> None:
    container, _ = build(tmp_path)
    ann = await owner_of(container, "ajax")
    bob, home = await bob_signs_in(container, ann.org_id)
    await container.managers.tenancy.delete_account(bob, "bob@example.test")
    identity_of(container).unavailable_for = 1
    handlers = build_loop(container)._handlers
    claimed = await container.managers.work.claim(
        request(), "default", [WorkKind.DELETE_ACCOUNT], "test", LEASE
    )
    assert claimed is not None
    ctx, item = claimed
    with pytest.raises(WorkParked):
        await handlers[item.kind].handle(ctx, item)
    # Nothing failed and nothing moved on: the org waits for the provider.
    org = await container.storage.get_tenancy_storage().read_org(home.org_id)
    assert org is not None and org.deleted_at is None
    await handlers[item.kind].handle(ctx, item)
    assert len(identity_of(container).deleted) == 1
    org = await container.storage.get_tenancy_storage().read_org(home.org_id)
    assert org is not None and org.deleted_at is not None
    # A second run finds every step done.
    await handlers[item.kind].handle(ctx, item)
    assert len(identity_of(container).deleted) == 1


async def claimed_deletion(
    tmp_path: Path,
) -> tuple[WorkerContainer, OpContext, OpContext, WorkItem]:
    """Bob deleted his account; the worker holds its DELETE_ACCOUNT item."""
    container, _ = build(tmp_path)
    ann = await owner_of(container, "ajax")
    bob, home = await bob_signs_in(container, ann.org_id)
    await container.managers.tenancy.delete_account(bob, "bob@example.test")
    claimed = await container.managers.work.claim(
        request(), "default", [WorkKind.DELETE_ACCOUNT], "test", LEASE
    )
    assert claimed is not None
    ctx, item = claimed
    return container, home, ctx, item


@pytest.mark.parametrize(
    ("error", "outcome"),
    [
        # What the WorkOS client raises for a 4xx on the request (400, 404
        # past the one that means gone, 422), and for a 5xx or a 401/403 on
        # its own key, which it answers as unavailable.
        (ProviderRefused("deleting the user: bad id"), WorkRefused),
        (ProviderConflict("deleting the user: in the way"), WorkRefused),
        (ProviderUnavailable("deleting the user: WorkOS answered 503"), WorkParked),
        (ProviderUnavailable("deleting the user: WorkOS refused the key (401)"), WorkParked),
    ],
)
async def test_a_refusal_of_the_call_fails_and_one_that_may_pass_parks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    outcome: type[Exception],
) -> None:
    container, home, ctx, item = await claimed_deletion(tmp_path)

    async def answer(user_id: str) -> None:
        raise error

    monkeypatch.setattr(identity_of(container), "delete_user", answer)
    with pytest.raises(outcome) as raised:
        await build_loop(container)._handlers[item.kind].handle(ctx, item)
    assert str(error) in str(raised.value)
    # Either way nothing moved on: the org waits, for the provider or a person.
    org = await container.storage.get_tenancy_storage().read_org(home.org_id)
    assert org is not None and org.deleted_at is None


@pytest.mark.parametrize(
    ("error", "outcome"),
    [
        (PaymentsRefused("delete customer", "invalid_request_error"), WorkRefused),
        (PaymentsKeyRefused("delete customer", "PermissionError"), WorkParked),
    ],
)
async def test_the_processor_refusing_the_call_fails_it_and_its_key_parks_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    outcome: type[Exception],
) -> None:
    container, _ = build(tmp_path)
    ann = await owner_of(container, "ajax")
    bob, home = await bob_signs_in(container, ann.org_id)
    payload = await checkout(container, home, Plan.PRO, 1)
    assert await consumer_of(container).handle(await queued(container, payload)) == "applied"
    await container.managers.tenancy.delete_account(bob, "bob@example.test")
    claimed = await container.managers.work.claim(
        request(), "default", [WorkKind.DELETE_ACCOUNT], "test", LEASE
    )
    assert claimed is not None
    ctx, item = claimed

    async def answer(customer_id: str) -> None:
        raise error

    monkeypatch.setattr(cast(PaymentsTwinImpl, container.payments), "delete_customer", answer)
    with pytest.raises(outcome):
        await build_loop(container)._handlers[item.kind].handle(ctx, item)
    org = await container.storage.get_tenancy_storage().read_org(home.org_id)
    assert org is not None and org.deleted_at is None
