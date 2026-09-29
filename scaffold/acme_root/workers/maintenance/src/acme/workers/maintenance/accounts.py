"""The handlers of a deleted account, and of a deleted team org: what one
commit could not do.

The account's own rows go in the request that deleted it, so the person is
gone the moment it answers and nothing below can bring them back. What is
left is outside the database, and the queue carries it to the end.

`DELETE_ACCOUNT` runs in the person's personal org. It deletes the person at
the identity provider, then the org, which the sweep then purges whole.
Each step is one a rerun finds done, so a run that stopped halfway is
finished by the next. A provider that cannot be reached, or that refuses the
process's own key, parks the item, spending no attempt: nothing about the
call has failed, and the work waits for the provider, or a person, to fix
it. A provider that refuses the call itself fails the item at once, since
asking again gets the same answer, and leaves a failed item for an operator
to read and requeue. The org stays until the provider is done: deleted
first, it could no longer run the work that names it.

`DELETE_ORG` runs in a team org its owner or an operator deleted. Its
members and their credentials went in the request, so nobody is in it. It
deletes the org's organization at the identity provider, then the org: the
sweep purges it after the retention. Every step, and every wait and
failure, is the account's (ADR 0042)."""

import logging
from typing import ClassVar

from acme.integrations.identity import IdentityProviderInterface
from acme.om.opcontext import OpContext, Permission
from acme.om.tenancy import TenancyManagerInterface
from acme.om.work.types.handler import WorkHandlerInterface
from acme.om.work.types.work_item import DeleteAccountPayload, DeleteOrgPayload, WorkItem
from acme.workers.maintenance.providers import provider_calls

log = logging.getLogger(__name__)


class DeleteAccountHandlerImpl(WorkHandlerInterface):
    REQUIRES: ClassVar[tuple[Permission, ...]] = (Permission.MANAGE_MEMBERS,)
    """The org's deletion manages members."""

    def __init__(
        self, tenancy: TenancyManagerInterface, identity: IdentityProviderInterface
    ) -> None:
        self._tenancy = tenancy
        self._identity = identity

    async def handle(self, ctx: OpContext, item: WorkItem) -> None:
        user_id = DeleteAccountPayload.model_validate(item.payload).provider_user_id
        if user_id is not None:
            async with provider_calls():
                await self._identity.delete_user(user_id)
        await self._tenancy.delete_personal_org(ctx)
        log.info("the personal org %s of a deleted account is deleted", ctx.org_id)


class DeleteOrgHandlerImpl(WorkHandlerInterface):
    REQUIRES: ClassVar[tuple[Permission, ...]] = (Permission.MANAGE_MEMBERS,)
    """The org's deletion manages members."""

    def __init__(
        self, tenancy: TenancyManagerInterface, identity: IdentityProviderInterface
    ) -> None:
        self._tenancy = tenancy
        self._identity = identity

    async def handle(self, ctx: OpContext, item: WorkItem) -> None:
        org_id = DeleteOrgPayload.model_validate(item.payload).provider_org_id
        if org_id is not None:
            async with provider_calls():
                await self._identity.delete_organization(org_id)
        await self._tenancy.delete_closed_org(ctx)
        log.info("the closed team org %s is deleted", ctx.org_id)
