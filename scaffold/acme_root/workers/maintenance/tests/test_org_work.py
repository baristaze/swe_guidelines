"""A team org its owner or an operator deleted, in the worker: its organization gone at the
identity provider, its subscription canceled and its customer deleted, its
Slack app removed, and the org deleted as an operator deletes one, which the
sweep purges only after the retention; and a provider that is down parking
the work until it answers."""

from datetime import timedelta
from pathlib import Path
from typing import cast

import pytest
from slack_support import build, install, make_task, on_team
from test_billing_work import checkout, consumer_of, queued
from worker_support import request

from acme.infra.cache import CacheScope
from acme.integrations.exceptions import ProviderRefused, ProviderUnavailable
from acme.integrations.identity.absent import IdentityProviderAbsentImpl
from acme.integrations.identity.twin import IdentityProviderTwinImpl
from acme.integrations.payments.twin import PaymentsTwinImpl
from acme.om.billing.types.plan import Plan
from acme.om.opcontext import OpContext, OperatorRole, Role
from acme.om.tenancy.impl.manager import TenancyManagerImpl, TenancyOptions
from acme.om.work.types.handler import WorkParked, WorkRefused
from acme.om.work.types.work_item import WorkItem, WorkKind
from acme.workers.maintenance.container import WorkerContainer
from acme.workers.maintenance.main import build_loop

LEASE = timedelta(seconds=30)


def identity_of(container: WorkerContainer) -> IdentityProviderTwinImpl:
    return cast(IdentityProviderTwinImpl, container.identity_provider)


async def signed_in_owner(container: WorkerContainer) -> OpContext:
    """Ajax's owner, signed in locally: an org is deleted from a session.
    Ajax is on Team, so it has room to invite someone."""
    tenancy = container.managers.tenancy
    seeded, _ = await tenancy.bootstrap(request(), "Ajax", "ajax", "owner@ajax.test", "Owner")
    await on_team(container, seeded)
    signing = TenancyManagerImpl(
        container.storage.get_tenancy_storage(),
        container.managers.outbox,
        container.infra.get_cache(CacheScope.REALTIME_TICKET),
        TenancyOptions(dev_sign_in=True),
        identity_provider=IdentityProviderAbsentImpl(),
        entitlements=container.managers.billing,
    )
    login = await signing.dev_sign_in(request(), "owner@ajax.test")
    identity = await tenancy.authenticate_login(request(), login.token)
    ajax = next(m.org.id for m in login.memberships if not m.org.personal)
    issued = await tenancy.exchange_login(identity, ajax)
    return await tenancy.authenticate(request(), issued.token)


async def claim(container: WorkerContainer) -> tuple[OpContext, WorkItem]:
    claimed = await container.managers.work.claim(
        request(), "default", [WorkKind.DELETE_ORG], "test", LEASE
    )
    assert claimed is not None
    return claimed


async def test_a_deleted_org_ends_at_its_providers_and_then_itself(tmp_path: Path) -> None:
    container, slack = build(tmp_path)
    tasks, tenancy = container.managers.tasks, container.managers.tenancy
    owner = await signed_in_owner(container)
    task = await tasks.create_task(owner, make_task(owner, "Ship it"))
    await tenancy.invite_member(owner, "bob@example.test", Role.MEMBER)
    provider_org_id = (await tenancy.get_org(owner)).provider_org_id
    assert provider_org_id is not None and provider_org_id in identity_of(container).organizations
    payload = await checkout(container, owner, Plan.PRO, 1)
    assert await consumer_of(container).handle(await queued(container, payload)) == "applied"
    account = (await container.managers.billing.get_billing(owner)).account
    assert account is not None and account.customer_id and account.subscription_id
    await install(container, slack, owner)

    await tenancy.delete_org(owner, "Ajax")
    ctx, item = await claim(container)
    await build_loop(container)._handlers[item.kind].handle(ctx, item)
    await container.managers.work.complete(ctx, item)

    # The providers' side is gone.
    assert identity_of(container).deleted_organizations == [provider_org_id]
    payments = cast(PaymentsTwinImpl, container.payments)
    assert account.customer_id not in payments.customers
    assert payments.subscriptions[account.subscription_id]["status"] == "canceled"
    assert slack.uninstalled, "the app left the workspace"
    # The org is deleted as an operator deletes one, and keeps the retention:
    # the sweep leaves its rows until it has passed.
    org = await container.storage.get_tenancy_storage().read_org(owner.org_id)
    assert org is not None and org.deleted_at is not None and org.name == "Ajax"
    await build_loop(container)._sweep_once()
    kept = await container.storage.get_tasks_storage().read_task(owner.org_id, task.id)
    assert kept is not None, "purged after the retention, not at the next pass"
    # A rerun of the item finds every step done.
    await build_loop(container)._handlers[item.kind].handle(ctx, item)
    assert identity_of(container).deleted_organizations == [provider_org_id]


async def test_a_provider_that_is_down_parks_the_org_until_it_answers(tmp_path: Path) -> None:
    container, _ = build(tmp_path)
    tenancy = container.managers.tenancy
    owner = await signed_in_owner(container)
    await tenancy.invite_member(owner, "bob@example.test", Role.MEMBER)
    await tenancy.delete_org(owner, "Ajax")
    identity_of(container).unavailable_for = 1
    handler = build_loop(container)._handlers[WorkKind.DELETE_ORG]
    ctx, item = await claim(container)
    with pytest.raises(WorkParked):
        await handler.handle(ctx, item)
    # Nothing failed and nothing moved on: the org waits for the provider.
    org = await container.storage.get_tenancy_storage().read_org(owner.org_id)
    assert org is not None and org.deleted_at is None
    await handler.handle(ctx, item)
    assert len(identity_of(container).deleted_organizations) == 1
    org = await container.storage.get_tenancy_storage().read_org(owner.org_id)
    assert org is not None and org.deleted_at is not None


@pytest.mark.parametrize(
    ("error", "outcome"),
    [
        (ProviderRefused("deleting the organization: bad id"), WorkRefused),
        (
            ProviderUnavailable("deleting the organization: WorkOS refused the key (401)"),
            WorkParked,
        ),
    ],
)
async def test_a_refusal_of_the_call_fails_the_org_and_one_that_may_pass_parks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    outcome: type[Exception],
) -> None:
    container, _ = build(tmp_path)
    tenancy = container.managers.tenancy
    owner = await signed_in_owner(container)
    await tenancy.invite_member(owner, "bob@example.test", Role.MEMBER)
    await tenancy.delete_org(owner, "Ajax")

    async def answer(organization_id: str) -> None:
        raise error

    monkeypatch.setattr(identity_of(container), "delete_organization", answer)
    ctx, item = await claim(container)
    with pytest.raises(outcome) as raised:
        await build_loop(container)._handlers[WorkKind.DELETE_ORG].handle(ctx, item)
    assert str(error) in str(raised.value)
    # Either way nothing moved on: the org waits, for the provider or a person.
    org = await container.storage.get_tenancy_storage().read_org(owner.org_id)
    assert org is not None and org.deleted_at is None


async def test_an_org_an_operator_deleted_takes_the_same_work(tmp_path: Path) -> None:
    """The operator's deletion asks for the same `DELETE_ORG`, and the same
    handler ends the providers and deletes the org, under the operator's name."""
    container, slack = build(tmp_path)
    tenancy = container.managers.tenancy
    owner = await signed_in_owner(container)
    await tenancy.invite_member(owner, "bob@example.test", Role.MEMBER)
    provider_org_id = (await tenancy.get_org(owner)).provider_org_id
    payload = await checkout(container, owner, Plan.PRO, 1)
    assert await consumer_of(container).handle(await queued(container, payload)) == "applied"
    account = (await container.managers.billing.get_billing(owner)).account
    assert account is not None and account.customer_id and account.subscription_id
    await install(container, slack, owner)
    await tenancy.bootstrap(
        request(), "Ops", "ops", "root@example.test", "Root", operator_role=OperatorRole.WRITE
    )
    token = await tenancy.grant_operator_token(request(), "root@example.test")
    admin = await tenancy.admit_operator(await tenancy.authenticate_login(request(), token.token))

    await container.managers.tenancy_operator.delete_org(admin, owner.org_id)
    ctx, item = await claim(container)
    assert item.created_by == admin.identity_id
    await build_loop(container)._handlers[item.kind].handle(ctx, item)

    assert identity_of(container).deleted_organizations == [provider_org_id]
    payments = cast(PaymentsTwinImpl, container.payments)
    assert account.customer_id not in payments.customers
    assert payments.subscriptions[account.subscription_id]["status"] == "canceled"
    assert slack.uninstalled, "the app left the workspace"
    org = await container.storage.get_tenancy_storage().read_org(owner.org_id)
    assert org is not None and org.deleted_at is not None
    assert org.deleted_by == admin.identity_id
