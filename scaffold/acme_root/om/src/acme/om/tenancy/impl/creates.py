"""The creates the tenancy namespace has more than one entry point for: a
person with their personal org, an org with its owner, and a member of an
org. The seeding commands (`bootstrap`, `add-member`) reach them through the
tenant manager's transitions, under a request stage; the operator plane
reaches them through the operator manager, under an operator; a first
sign-in reaches the first one with a person nobody has seen before, an
accepted invitation reaches the third, and a signed-in person reaches the
second one for a team org of their own. Every path builds
the same rows and lands them in the same named atomic write, so a tenant
seeded from the command line, one created over the operator API, and one a
person made are indistinguishable afterwards. Nothing here constructs a stage
or decides who may call: the callers authorize, this module verifies, copies,
and writes.

A person never exists without a place to work: every path that makes an
identity lands its personal org, its user there, and the owner membership in
the same commit as the identity (`personal_rows`). The one exception is the
platform's own identities, which the grant job makes and no person signs in
as."""

import secrets
from collections.abc import Mapping
from datetime import datetime
from typing import Any
from uuid import UUID

from acme.om.base import new_id, utcnow
from acme.om.context import OperatorRole, RequestScope, Role
from acme.om.exceptions import Conflict, MembershipLimitReached, ValidationFailed
from acme.om.outbox import OutboxRelayInterface
from acme.om.outbox.types.row import OutboxRow
from acme.om.tenancy.rules import (
    SLUG_SUFFIX_LENGTH,
    check_email,
    email_digest,
    fold_email,
    is_platform_email,
    operator_permissions_of,
    personal_org_name,
    slug_from_name,
)
from acme.om.tenancy.storage import TenancyStorageInterface
from acme.om.tenancy.types.identity import Identity
from acme.om.tenancy.types.invitation import Invitation
from acme.om.tenancy.types.issued import OrgMembership
from acme.om.tenancy.types.membership import Membership
from acme.om.tenancy.types.org import Org, OrgKind
from acme.om.tenancy.types.user import User

MAX_ORGS_PER_IDENTITY = 100
"""How many orgs one person may be a member of: the default of the managers'
option, which is the bound on every read of the users one identity is."""


def user_payload(user: User) -> Mapping[str, Any]:
    """What an outbox row about a user carries: ids, never who they are, so
    the relay and the stream hold nothing an erasure has to reach."""
    return {"identity_id": str(user.identity_id)}


async def users_of(
    storage: TenancyStorageInterface, identity_id: UUID, most: int
) -> list[tuple[UUID, User]]:
    """Every live user one identity is, across tenants, with the tenant. The
    read asks for one past `most`, so a list cut short is never taken for the
    whole: more than `most` (two adds that raced past the refusal below) is
    `MembershipLimitReached`, never a silent truncation that would hide the
    org a sign-in or an add is about."""
    users = await storage.read_users_by_identity(identity_id, most + 1)
    if len(users) > most:
        raise MembershipLimitReached(f"identity {identity_id} is a member of more than {most} orgs")
    return users


def refuse_one_more(identity_id: UUID, users: list[tuple[UUID, User]], most: int) -> None:
    """A create that adds a user to an identity already at `most` is refused."""
    if len(users) >= most:
        raise MembershipLimitReached(
            f"identity {identity_id} is already a member of {most} orgs, the most one may join"
        )


def widens_operator_role(identity: Identity, granted: OperatorRole) -> bool:
    """Whether granting `granted` gives the identity a permission its entry
    does not hold: a promotion, never a demotion, as the seeding reads it."""
    held = (
        frozenset()
        if identity.operator_role is None
        else operator_permissions_of(identity.operator_role)
    )
    return not operator_permissions_of(granted) <= held


Tenant = tuple[Org, User, Membership]
"""An org with its owner's user and the owner membership: the rows every
create of a tenant lands together."""


async def identity_for(
    storage: TenancyStorageInterface,
    email: str,
    display_name: str,
    operator_role: OperatorRole | None,
    now: datetime,
) -> tuple[Identity, Identity | None, Tenant | None]:
    """The identity as it should read once the create lands, the row to write
    beside the create when it changed, and the personal org to land with it:
    a new identity comes with its personal org, and an existing one promoted
    to the operator role asked for comes alone. A new identity holds no
    credential: the person signs in through the identity provider with the
    address, which links them then. A role is never narrowed. Nothing is
    written here; it all lands in the create, so a create refused meanwhile
    leaves no identity carrying this attempt's role."""
    if is_platform_email(email):
        # The provisioner and the smoke identity are the grant job's to make;
        # no create names one.
        raise ValidationFailed("that address belongs to the platform")
    identity = await storage.read_identity_by_email_digest(email_digest(email))
    if identity is None:
        try:
            check_email(email)
        except ValueError as error:
            raise ValidationFailed(str(error)) from None
        identity = new_identity(email, now)
        if operator_role is not None:
            identity = identity.model_copy(update={"operator_role": operator_role})
        return identity, identity, personal_rows(identity, display_name, now)
    if operator_role is not None and widens_operator_role(identity, operator_role):
        identity = identity.model_copy(
            update={"operator_role": operator_role, "updated_at": now, "updated_by": identity.id}
        )
        return identity, identity, None
    return identity, None, None


def new_identity(
    email: str, now: datetime, *, issuer: str | None = None, subject: str | None = None
) -> Identity:
    """A person nobody has seen before: the provenance names the identity
    itself, since no one else acted. A first sign-in through the identity
    provider names the issuer and the subject; the seeding and the operator
    plane name neither, and the person's first sign-in links them. The
    address is kept folded, as every address is."""
    identity_id = new_id()
    return Identity(
        id=identity_id,
        created_at=now,
        updated_at=now,
        created_by=identity_id,
        updated_by=identity_id,
        email=fold_email(email),
        issuer=issuer,
        subject=subject,
    )


def slug_suffix() -> str:
    """The random tail of a slug nobody typed."""
    alphabet = "abcdefghijklmnopqrstuvwxyz0123456789"
    return "".join(secrets.choice(alphabet) for _ in range(SLUG_SUFFIX_LENGTH))


def personal_rows(
    identity: Identity, display_name: str, now: datetime
) -> tuple[Org, User, Membership]:
    """A person's personal org, their user in it, and the owner membership.
    The org is named after the person, the slug is generated, and the person
    types neither. A person with no name is "Personal" there, and their
    address's local part is the name the org shows for them."""
    name = personal_org_name(display_name)
    shown = display_name.strip() or identity.email.partition("@")[0]
    org, user, membership = owner_rows(
        new_id(), name, slug_from_name(name, slug_suffix()), identity, shown, now
    )
    org = org.model_copy(update={"kind": OrgKind.PERSONAL, "personal_identity_id": identity.id})
    return org, user, membership


async def create_person(
    storage: TenancyStorageInterface,
    identity: Identity,
    display_name: str,
    *,
    new: bool = True,
) -> OrgMembership:
    """A person nobody has seen before, whole: the identity, their personal
    org, their user in it, and the owner membership, in one commit. Every way
    a person comes to exist calls this, a first sign-in through the identity
    provider and the local sign-in among them: it takes the identity as the
    caller built it, the provider's link and all, and asks nothing about an
    org. An email or a personal org taken meanwhile is
    `UniqueKeyTaken`, and nothing lands. `new=False` is the one other case:
    a person who exists and has no personal org yet gets one, and the
    identity is left as it is."""
    org, user, membership = personal_rows(identity, display_name, utcnow())
    await storage.create_org_with_owner(org.id, org, user, membership, identity if new else None)
    return OrgMembership(org=org, user=user, role=membership.role)


async def create_org_with_owner(
    storage: TenancyStorageInterface,
    *,
    org_id: UUID,
    org_name: str,
    slug: str,
    email: str,
    display_name: str,
    max_orgs: int,
    operator_role: OperatorRole | None = None,
) -> tuple[Org, User, Membership]:
    """A team org, its owner's user, and the owner membership, in one commit,
    with the owner's identity created, with its personal org, or promoted
    beside them. A taken slug is a `Conflict`. The org and its rows are the
    owner's own: the owner is the first actor of the tenant, so the provenance
    names the owner's user."""
    if await storage.read_org_by_slug(slug) is not None:
        raise Conflict(f"org slug {slug!r} is taken")
    now = utcnow()
    identity, to_write, personal = await identity_for(
        storage, email, display_name, operator_role, now
    )
    refuse_one_more(identity.id, await users_of(storage, identity.id, max_orgs), max_orgs)
    org, user, membership = owner_rows(org_id, org_name, slug, identity, display_name, now)
    # One commit: a slug taken meanwhile leaves no org without its owner,
    # and no identity without its orgs.
    await storage.create_org_with_owner(org.id, org, user, membership, to_write, personal)
    return org, user, membership


def owner_rows(
    org_id: UUID, org_name: str, slug: str, identity: Identity, display_name: str, now: datetime
) -> tuple[Org, User, Membership]:
    """The org, its owner's user, and the owner membership, as every create of
    a tenant builds them. The org and its rows are the owner's own: the owner
    is the first actor of the tenant, so the provenance names the owner's user."""
    user_id = new_id()
    org = Org(
        id=org_id,
        name=org_name,
        created_at=now,
        updated_at=now,
        created_by=user_id,
        updated_by=user_id,
        slug=slug,
    )
    user = User(
        id=user_id,
        created_at=now,
        updated_at=now,
        created_by=user_id,
        updated_by=user_id,
        identity_id=identity.id,
        email=identity.email,
        display_name=display_name,
    )
    membership = Membership(
        id=new_id(),
        created_at=now,
        updated_at=now,
        created_by=user_id,
        updated_by=user_id,
        user_id=user_id,
        role=Role.OWNER,
    )
    return org, user, membership


async def add_member_to(
    storage: TenancyStorageInterface,
    relay: OutboxRelayInterface,
    *,
    org_id: UUID,
    user_id: UUID,
    email: str,
    display_name: str,
    role: Role,
    actor_id: UUID,
    request: RequestScope,
    max_orgs: int,
    invitation: Invitation | None = None,
) -> tuple[User, bool]:
    """A person in an org: the identity is created if the email is new, with
    its personal org, then the user and the membership land with the row that
    announces them, in one commit, and the row is relayed. `invitation`, when
    given, is the tenant's invitation the membership accepts, as it reads once
    accepted, and lands in the same commit. A person who is already a live
    member is returned as they are, with False, and nothing is written.
    `actor_id` is who the rows record as their maker: the org's creator on the
    seeding path, the operator's identity on the operator plane, which has no
    user in the tenant, and the inviter on an accepted invitation. The service
    role is refused by name: it is the role a sweep's context carries, never a
    membership. A person already a member of `max_orgs` orgs is refused with
    `MembershipLimitReached`."""
    if role is Role.SERVICE:
        raise ValidationFailed("service is not a membership role")
    now = utcnow()
    identity, to_write, personal = await identity_for(storage, email, display_name, None, now)
    users = await users_of(storage, identity.id, max_orgs)
    for member_org_id, existing in users:
        if member_org_id == org_id and existing.deleted_at is None:
            return existing, False
    refuse_one_more(identity.id, users, max_orgs)
    user = User(
        id=user_id,
        created_at=now,
        updated_at=now,
        created_by=actor_id,
        updated_by=actor_id,
        identity_id=identity.id,
        email=identity.email,
        display_name=display_name,
    )
    membership = Membership(
        id=new_id(),
        created_at=now,
        updated_at=now,
        created_by=actor_id,
        updated_by=actor_id,
        user_id=user_id,
        role=role,
    )
    # One commit: the user, the membership, and the outbox row land together,
    # so a concurrent add of the same person leaves no membership without
    # its user and no user without a membership.
    row = OutboxRow(
        id=new_id(),
        created_at=now,
        org_id=org_id,
        kind="tenancy.user.created",
        target_id=user.id,
        payload=user_payload(user),
        actor_id=actor_id,
        request_id=request.request_id,
        traceparent=request.traceparent,
        app=request.app.type.value,
    )
    rows = (row,)
    await storage.create_member(
        org_id, user, membership, rows, to_write, personal, invitation=invitation
    )
    await relay.relay_all(org_id, rows)
    return user, True
