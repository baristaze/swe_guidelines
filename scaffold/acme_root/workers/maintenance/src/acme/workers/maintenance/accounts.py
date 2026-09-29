"""The handlers of a deleted account, and of a deleted team org: what each
one commit could not do.

The account's own rows go in the request that deleted it, so the person is
gone the moment it answers and nothing below can bring them back. What is
left is outside the database, or in orgs the person no longer holds a place
in, and the queue carries it to the end.

`DELETE_ACCOUNT` runs in the person's personal org. It deletes the person at
the identity provider, ends the org's subscription and deletes its customer
at the payment processor, removes the org's Slack app, and deletes the org
last, which the sweep then purges whole. Every step is one a rerun finds
done, so a run that stopped halfway is finished by the next. A provider that
cannot be reached, or that refuses the process's own key, parks the item,
spending no attempt: nothing about the call has failed, and the work waits
for the provider, or a person, to fix it. A provider that refuses the call
itself fails the item at once, since asking again gets the same answer, and
leaves a failed item for an operator to read and requeue. The org stays
until the providers are done: deleted first, it could no longer run the
work that names it.

`UNASSIGN_TASKS` runs in each org the person left, under their name: their
open tasks there go unassigned, each by the update a person makes to clear
an assignee. It runs on the service role, whatever role the person held:
the unassignment is what an account's deletion does, not a write the
person asks for (ADR 0041).

`DELETE_ORG` runs in a team org its owner or an operator deleted. Its
members and their credentials went in the request, so nobody is in it. It
deletes the org's organization at the identity provider, ends the
subscription and deletes the customer at the payment processor, removes the
Slack app, and deletes the org last: the sweep purges it after the
retention. Every step, and every wait and failure, is the account's
(ADR 0042)."""

import logging
from collections.abc import Awaitable, Callable
from typing import ClassVar

from acme.integrations.identity import IdentityProviderInterface
from acme.om.billing import BillingManagerInterface
from acme.om.opcontext import OpContext, Permission
from acme.om.slack import SlackManagerInterface
from acme.om.tasks import TasksManagerInterface
from acme.om.tasks.types.filter import OpenTaskCursor, TaskFilter
from acme.om.tasks.types.task import TaskScope
from acme.om.tenancy import TenancyManagerInterface
from acme.om.work.types.handler import WorkHandlerInterface
from acme.om.work.types.work_item import DeleteAccountPayload, DeleteOrgPayload, WorkItem
from acme.workers.maintenance.providers import provider_calls

log = logging.getLogger(__name__)

TASKS_PAGE = 100
"""Open tasks read a page at a time while their assignee is cleared."""


class DeleteAccountHandlerImpl(WorkHandlerInterface):
    REQUIRES: ClassVar[tuple[Permission, ...]] = (Permission.MANAGE_MEMBERS,)
    """The processor's side, the Slack app's removal, and the org's deletion
    all manage members."""

    def __init__(
        self,
        tenancy: TenancyManagerInterface,
        billing: BillingManagerInterface,
        slack: SlackManagerInterface,
        identity: IdentityProviderInterface,
    ) -> None:
        self._tenancy = tenancy
        self._billing = billing
        self._slack = slack
        self._identity = identity

    async def handle(self, ctx: OpContext, item: WorkItem) -> None:
        payload = DeleteAccountPayload.model_validate(item.payload)
        user_id = payload.provider_user_id
        identity_step = None if user_id is None else lambda: self._identity.delete_user(user_id)
        await end_providers(ctx, identity_step, self._billing, self._slack)
        await self._tenancy.delete_personal_org(ctx)
        log.info("the personal org %s of a deleted account is deleted", ctx.org_id)


class DeleteOrgHandlerImpl(WorkHandlerInterface):
    REQUIRES: ClassVar[tuple[Permission, ...]] = (Permission.MANAGE_MEMBERS,)
    """The processor's side, the Slack app's removal, and the org's deletion
    all manage members."""

    def __init__(
        self,
        tenancy: TenancyManagerInterface,
        billing: BillingManagerInterface,
        slack: SlackManagerInterface,
        identity: IdentityProviderInterface,
    ) -> None:
        self._tenancy = tenancy
        self._billing = billing
        self._slack = slack
        self._identity = identity

    async def handle(self, ctx: OpContext, item: WorkItem) -> None:
        payload = DeleteOrgPayload.model_validate(item.payload)
        org_id = payload.provider_org_id
        identity_step = (
            None if org_id is None else lambda: self._identity.delete_organization(org_id)
        )
        await end_providers(ctx, identity_step, self._billing, self._slack)
        await self._tenancy.delete_closed_org(ctx)
        log.info("the closed team org %s is deleted", ctx.org_id)


async def end_providers(
    ctx: OpContext,
    identity_step: Callable[[], Awaitable[None]] | None,
    billing: BillingManagerInterface,
    slack: SlackManagerInterface,
) -> None:
    """The providers' side of a tenant that goes, in order: the identity
    provider's record of it, then the processor's subscription and customer,
    then the Slack app. A provider out of reach parks the item; one that
    refuses the request fails it for good."""
    async with provider_calls():
        if identity_step is not None:
            await identity_step()
        await billing.close_account(ctx)
    # Slack's side is best effort, as every removal of the app is: a Slack
    # that cannot be reached leaves the app in the workspace, and the token
    # goes from Acme either way.
    await slack.uninstall(ctx)


class UnassignTasksHandlerImpl(WorkHandlerInterface):
    REQUIRES: ClassVar[tuple[Permission, ...]] = (Permission.READ, Permission.WRITE)
    """`get_open_tasks` reads; `update_task` writes."""

    def __init__(self, tasks: TasksManagerInterface) -> None:
        self._tasks = tasks

    async def handle(self, ctx: OpContext, item: WorkItem) -> None:
        # The item runs as the person who left, so `mine` is their list: the
        # tasks assigned to them, and the unassigned ones they made.
        mine = TaskFilter(scope=TaskScope.MINE, user_id=item.target_id)
        after: OpenTaskCursor | None = None
        cleared = 0
        while True:
            page = await self._tasks.get_open_tasks(ctx, mine, after, TASKS_PAGE)
            for task in page.items:
                if task.assignee_id != item.target_id:
                    continue
                # A task edited meanwhile is refused as stale, and the item's
                # retry reads it again.
                released = task.model_copy(update={"assignee_id": None})
                await self._tasks.update_task(ctx, released, task.version)
                cleared += 1
            if not page.has_more or not page.items:
                break
            last = page.items[-1]
            after = OpenTaskCursor(rank=last.rank, id=last.id)
        log.info("unassigned %d open tasks of a person who left org %s", cleared, ctx.org_id)
