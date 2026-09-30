"""The org duty of the tenancy manager: the caller's org and account, a team
org of their own, and the deletion of each."""

from abc import ABC, abstractmethod

from acme.om.context import TenantContext
from acme.om.idempotency.types.attempt import Attempt
from acme.om.tenancy.types.identity import Identity
from acme.om.tenancy.types.issued import AccountDeleted, OrgDeleted, OrgMembership
from acme.om.tenancy.types.org import Org


class TenancyOrgManagerInterface(ABC):
    """A delegate of `TenancyManagerInterface`, reached as `tenancy.org`.
    Every operation takes `TenantContext`."""

    # The principal.

    @abstractmethod
    async def get_org(self, ctx: TenantContext) -> Org: ...

    @abstractmethod
    async def get_me(self, ctx: TenantContext) -> OrgMembership:
        """The caller's org, their user in it, and their membership's role,
        read together in one transaction. The context carries ids, so what
        the caller is shown is loaded here, as fresh as the request."""
        ...

    @abstractmethod
    async def create_org(
        self, ctx: TenantContext, name: str, slug: str | None, attempt: Attempt | None = None
    ) -> OrgMembership:
        """A team org the caller makes and owns: the org, the caller's user in
        it under the display name they carry in this one, and the owner
        membership, in one commit. The slug is generated from the name when
        `slug` is None; a taken one is Conflict. Only a session makes an org
        (NotAuthorized for an api key), since a new tenant is a person's and
        not a program's; a person already in as many orgs as they may join is
        MembershipLimitReached. The caller's session stays in its tenant: the
        switch into the new one is the exchange. `attempt` as on
        `TenancyCredentialsManagerInterface.create_api_key`: the org is created
        on its id, and a rerun finds the org written and answers with the
        caller's place in it."""
        ...

    @abstractmethod
    async def get_identity(self, ctx: TenantContext) -> Identity:
        """The identity behind the caller's user."""
        ...

    @abstractmethod
    async def set_time_zone(self, ctx: TenantContext, time_zone: str) -> Identity:
        """Records where the caller is, as an IANA name, on their identity,
        so it holds in every org they are in. A name that is not one
        (`tenancy.rules.check_time_zone`) is ValidationFailed."""
        ...

    @abstractmethod
    async def delete_account(
        self, ctx: TenantContext, confirm_email: str, return_to: str | None = None
    ) -> AccountDeleted:
        """The caller's whole account, gone for good, from a session only
        (NotAuthorized for an api key, which is a program's). `confirm_email`
        is the account's email as the person typed it (ValidationFailed when
        it is not). Refused while the person is on the operator allowlist
        (OperatorRoleHeld), and while they are the last owner of a team org
        (LastOwner, naming each one).

        One commit erases the person (`TenancyStorageInterface.delete_person`):
        the identity, their user and membership in every org, every
        credential they hold, each live one announced as revoked, and the
        sign-in delay of their address. What they made in a team org stays
        the org's, under an id that no longer names anyone. The same commit
        asks for the rest: in their personal org, the provider's side goes
        and then the org itself (`DELETE_ACCOUNT`). The answer says where the browser goes to
        end the provider's session, as `TenancySignInManagerInterface.logout`
        does with `return_to`."""
        ...

    @abstractmethod
    async def delete_personal_org(self, ctx: TenantContext) -> Org | None:
        """Platform-internal, the last step of `DELETE_ACCOUNT`: deletes the
        caller's tenant when it is a personal org whose person is gone, and
        announces it, so its sockets close. A deleted personal org keeps no
        retention, so the sweep purges every row of it at its next pass
        (`tenancy.rules.past_retention`). None, and nothing written, when it
        is deleted already; PersonalOrgFixed for any other org."""
        ...

    @abstractmethod
    async def delete_org(self, ctx: TenantContext, confirm_name: str) -> OrgDeleted:
        """The caller's team org, deleted by its owner, from a session only
        (NotAuthorized for an api key, which is a program's, and for any role
        but owner). `confirm_name` is the org's name as the owner typed it
        (ValidationFailed when it is not). A personal org is refused
        (PersonalOrgFixed): it goes only with its person's account.

        One commit closes the org (`TenancyStorageInterface.write_closed_org`): every
        member's user and membership end, every session and api key in it is
        revoked, each announced, so every socket closes, every pending
        invitation is revoked, and the org lets go of its organization at the
        identity provider. The same commit asks for the rest (`DELETE_ORG`):
        the provider's organization goes, then the org is deleted, and the
        sweep purges it after the retention. An operator's deletion
        (`TenancyOperatorManagerInterface.delete_org`) takes the same path.
        The answer carries a session in the owner's personal org, which the
        tab takes up, as a switch does."""
        ...

    @abstractmethod
    async def delete_closed_org(self, ctx: TenantContext) -> Org | None:
        """Platform-internal, the last step of `DELETE_ORG`, on the service
        role only (NotAuthorized otherwise): soft-deletes the caller's closed
        team org, whoever closed it, and announces it, so its sockets close.
        The sweep purges it once the retention has passed. None, and nothing
        written, when it is deleted already; PersonalOrgFixed for a personal
        org."""
        ...
