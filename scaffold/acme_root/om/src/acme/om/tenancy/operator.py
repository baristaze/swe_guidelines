"""The operator plane of the tenancy swimlane: what a platform operator may
do across every tenant. Every operation takes `OperatorContext` and nothing
else, but the sweep's tally of the platform's size, which takes no context
at all; the tenant manager takes `TenantContext` and nothing else, so the type
system keeps the two planes apart. A read requires `OperatorPermission.READ`
and a write `OperatorPermission.WRITE`, which the allowlist entry grants.

A read of a tenant's rows names the tenant, reads them through that
namespace's storage under the named tenant, and is logged with the tenant
and the operator, so support access has a trail. The creates are the same
creates the seeding commands run: one implementation, two entry points."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING
from uuid import UUID

from acme.om.events.types.event import Event
from acme.om.idempotency.types.attempt import Attempt
from acme.om.tenancy.types.identity import Identity
from acme.om.tenancy.types.issued import IssuedOperatorToken, IssuedTotpSecret
from acme.om.tenancy.types.org import Org
from acme.om.tenancy.types.page import OperatorTokenPage, OrgPage, UserPage
from acme.om.tenancy.types.session import Session
from acme.om.tenancy.types.size import PlatformSize
from acme.om.tenancy.types.user import User

if TYPE_CHECKING:
    from datetime import timedelta

    from acme.om.context import OperatorContext, OperatorRole, Role


class TenancyOperatorManagerInterface(ABC):
    # The operator's own credentials.

    @abstractmethod
    async def enrol_totp(self, admin: OperatorContext) -> IssuedTotpSecret:
        """Mints the operator's TOTP secret, replacing one that was minted and
        never confirmed, stores it sealed under the TOTP key, and answers it
        once as an `otpauth://` URI. Requires `OperatorPermission.ENROL`, what
        an operator holds until a secret is confirmed; once one is, Conflict."""
        ...

    @abstractmethod
    async def confirm_totp(self, admin: OperatorContext, totp_code: str) -> Identity:
        """The first code confirms the secret, and from then on the operator
        gate admits the identity only on a sign-in that verified a code. The
        sign-in that confirmed it carried none, so the next request signs in
        again with one. A code that does not match is ValidationFailed."""
        ...

    @abstractmethod
    async def issue_operator_token(
        self,
        admin: OperatorContext,
        operator_role: OperatorRole,
        expires_in: timedelta | None = None,
    ) -> IssuedOperatorToken:
        """An operator token for the operator's own identity: one permission,
        never wider than the operator's entry (`write` implies `read`),
        expiring within the hour (3600 seconds when `expires_in` is None).
        Refused (NotAuthorized) unless the operator stage came from a sign-in
        that verified a second factor, so a token never mints a token. The
        sign-in is exchanged once: it ends in the write that lands the token,
        and a second mint with it is CredentialExpired, so a person signs in
        again for each token. Shown once, stored as its digest; its `id`
        names it in the list and the revoke."""
        ...

    @abstractmethod
    async def get_operator_tokens(
        self, admin: OperatorContext, after: UUID | None, limit: int
    ) -> OperatorTokenPage:
        """The operator's own live tokens, newest first, a page at a time:
        `after` is the id the previous page ended on. A token of kind
        `operator_token` is a row, never the secret. Requires
        `OperatorPermission.READ`, which every token carries."""
        ...

    @abstractmethod
    async def revoke_operator_token(self, admin: OperatorContext, token_id: UUID) -> Session:
        """Ends one of the operator's own tokens at once: its `revoked_at` is
        stamped, `updated_by` names the operator, and its next request is
        refused. Idempotent: a token ended already is answered as stored.
        Another identity's token, or no token, is NotFound; ending another
        operator's credentials is the grant job's disable. Requires
        `OperatorPermission.READ`, so a token revokes its siblings and
        itself."""
        ...

    # Across every tenant.

    @abstractmethod
    async def get_orgs(self, admin: OperatorContext, after: UUID | None, limit: int) -> OrgPage:
        """Every org, deleted ones included, by id, a page at a time: `after` is
        the id the previous page ended on, and `has_more` says another follows."""
        ...

    @abstractmethod
    async def size(self, admin: OperatorContext) -> PlatformSize:
        """How big the platform is: live tenants and users, and the events of
        the twenty-four hours before `counted_at`, as the sweep last
        counted them (`tally_size`). It reads the one tally row and counts
        nothing. NotFound until the first count."""
        ...

    @abstractmethod
    async def tally_size(self) -> PlatformSize:
        """Platform-internal: the sweep's count of the platform's size across
        every tenant, the live orgs and users and the events of the day before
        now, written as the one tally row `size` reads. A count
        older than the row's is not written. Takes no context: it counts for
        no tenant and no principal."""
        ...

    @abstractmethod
    async def create_org(
        self,
        admin: OperatorContext,
        name: str,
        slug: str,
        owner_email: str,
        owner_name: str,
        attempt: Attempt | None = None,
    ) -> Org:
        """A team org with its owner, the way `bootstrap` seeds one: the
        owner's identity is created with its personal org when the email is
        new, and the owner signs in through the identity provider with it. A
        taken slug is `Conflict`. `attempt`, when given, is the
        attempt a retried request runs under: the org is created on its id,
        and an org already written under that id is the rerun of this create
        and is returned as stored."""
        ...

    # One named tenant.

    @abstractmethod
    async def get_org(self, admin: OperatorContext, org_id: UUID) -> Org:
        """The org, deleted or not; `NotFound` when no org has the id."""
        ...

    @abstractmethod
    async def get_members(
        self, admin: OperatorContext, org_id: UUID, after: UUID | None, limit: int
    ) -> UserPage:
        """The tenant's live members, by id, a page at a time, as the tenant's
        own `get_users` pages them."""
        ...

    @abstractmethod
    async def add_member(
        self,
        admin: OperatorContext,
        org_id: UUID,
        email: str,
        display_name: str,
        role: Role,
        attempt: Attempt | None = None,
    ) -> User:
        """A person in the org, the way `add-member` seeds one: the identity is
        created with its personal org if the email is new, and a person who is already a member is
        returned as they are. The owner role and the service role are refused
        by name: the one owner is the one `create_org` minted. The rows record
        the operator's identity as their maker, since an operator has no user
        in the tenant. `attempt` as on `create_org`, for the user's id. An org
        deleted, or closed and waiting for the queue to delete it, is
        NotFound."""
        ...

    @abstractmethod
    async def get_events(
        self, admin: OperatorContext, org_id: UUID, after_seq: int, limit: int
    ) -> list[Event]:
        """The tenant's events after `after_seq`, oldest first, as the tenant's
        own replay reads them."""
        ...

    @abstractmethod
    async def delete_org(self, admin: OperatorContext, org_id: UUID) -> Org:
        """Deletes a team org the way its owner does (ADR 0042), with the
        operator's identity as the actor of every row. One commit closes it
        (`TenancyStorageInterface.write_closed_org`): every member's user and
        membership end, every session and api key is revoked, each announced,
        so every socket of the tenant closes, every pending invitation is
        revoked, and the org lets go of its organization at the identity
        provider. The same commit asks for `DELETE_ORG`, which ends the
        providers (the identity provider's organization) and then deletes the
        org;
        the sweep purges it once the retention has passed.

        Answers the closed org, still live (`deleted_at` unset) until the
        worker deletes it. An org closed already is answered as it stands,
        and nothing is asked for twice; a deleted one is NotFound. A personal
        org is refused (PersonalOrgFixed): it goes only with its person's
        account (ADR 0041)."""
        ...
