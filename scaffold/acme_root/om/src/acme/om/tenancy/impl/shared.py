"""What more than one duty of the tenancy manager calls: the credentials
they mint and end, the checks a credential passes, and the reads of a
person and their places. The manager, its delegates, and the operator plane
each reach these, so each is written once, as `creates.py` holds the
creates. Nothing here constructs a stage or decides who may call: the
callers authorize."""

import logging
import secrets
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from acme.integrations.exceptions import ProviderUnavailable
from acme.integrations.identity import IdentityProviderInterface
from acme.om.base import new_id, utcnow
from acme.om.context import CredentialKind, OperatorRole, TenantScope
from acme.om.exceptions import (
    Conflict,
    CredentialExpired,
    InvalidCredential,
    MembershipLimitReached,
    NotAuthorized,
    NotFound,
    UniqueKeyTaken,
    ValidationFailed,
)
from acme.om.tenancy.impl.creates import users_of
from acme.om.tenancy.rules import PREFIX_FOR_KIND, hash_token
from acme.om.tenancy.storage import TenancyStorageInterface
from acme.om.tenancy.types.issued import IssuedOperatorToken, OrgMembership
from acme.om.tenancy.types.membership import Membership
from acme.om.tenancy.types.org import Org
from acme.om.tenancy.types.session import Session
from acme.om.tenancy.types.user import User

log = logging.getLogger(__name__)

SIGN_IN_USED = "this sign-in was used already; sign in again"
"""The refusal of a sign-in presented after its exchange: a sign-in makes one
session, so a retry, a second choice, or a replay starts a new sign-in."""
REVOKED_MESSAGE: Mapping[CredentialKind, str] = {
    CredentialKind.LOGIN: "this sign-in has ended (used or signed out); sign in again",
    CredentialKind.OPERATOR_TOKEN: "operator token revoked",
}
"""The refusal of an ended credential, by kind; a session's is "session revoked"."""


def mint_token(kind: CredentialKind) -> str:
    return PREFIX_FOR_KIND[kind] + secrets.token_urlsafe(32)


def new_operator_token(
    identity_id: UUID,
    operator_role: OperatorRole,
    expires_in: timedelta | None,
    most: timedelta,
) -> tuple[IssuedOperatorToken, Session]:
    """An operator token for one identity and the row that keeps it: one
    permission, an hour at most, a session of kind `operator_token` under the
    system scope, kept as its digest. The two entry points, a signed-in
    operator's mint and the grant job's, authorize before they call this, and
    each lands the row its own way: the mint ends the sign-in in the same
    write, the grant job has none to end."""
    ttl = most if expires_in is None else expires_in
    if not timedelta(0) < ttl <= most:
        raise ValidationFailed(
            f"an operator token lives between one second and {int(most.total_seconds())} seconds"
        )
    now = utcnow()
    token = mint_token(CredentialKind.OPERATOR_TOKEN)
    session = Session(
        id=new_id(),
        created_at=now,
        updated_at=now,
        created_by=identity_id,
        updated_by=identity_id,
        identity_id=identity_id,
        token_hash=hash_token(token),
        credential_kind=CredentialKind.OPERATOR_TOKEN,
        expires_at=now + ttl,
        operator_role=operator_role,
    )
    issued = IssuedOperatorToken(
        id=session.id, token=token, expires_at=session.expires_at, operator_role=operator_role
    )
    return issued, session


def ended_by(credential: Session, by: UUID, at: datetime) -> Session:
    """A credential as it is once `by` ended it at `at`: the identity that
    ended a sign-in or an operator token, the user that ended a session."""
    return credential.model_copy(update={"revoked_at": at, "updated_at": at, "updated_by": by})


async def exchange_sign_in(
    storage: TenancyStorageInterface, org_id: UUID, session: Session, ended: Session
) -> None:
    """Lands `session` in `org_id` and the sign-in it came from, ended, in one
    write, so a sign-in is exchanged once: for a tenant session, for the
    sign-in its second factor verified, or for an operator token. A second
    exchange of it, a retry after a lost answer among them, is refused, and
    the person signs in again."""
    try:
        await storage.exchange_sign_in(org_id, session, ended)
    except NotFound:
        raise InvalidCredential("the sign-in behind the exchange is gone") from None
    except UniqueKeyTaken:
        raise  # a key of the new row, not the sign-in presented
    except Conflict:
        raise CredentialExpired(SIGN_IN_USED) from None


def clamp(limit: int, most: int) -> int:
    return max(1, min(limit, most))


def session_payload(session: Session) -> Mapping[str, Any]:
    # Ids only: the row names the session by its target, and never carries
    # the hash; the event is a record, not a credential.
    return {"user_id": str(session.user_id)}


async def create_session(storage: TenancyStorageInterface, org_id: UUID, session: Session) -> None:
    """Lands a new credential's row through the insert that reports. Its id
    is minted with its secret, so an id already written is never a retry
    of this call: the row there keeps another digest, and the secret is
    refused rather than handed out as one that opens nothing."""
    if not await storage.create_session(org_id, session):
        raise Conflict(f"session {session.id} is already written")


def check_session(session: Session, kind: CredentialKind, idle_ttl: timedelta) -> None:
    if session.credential_kind is not kind:
        raise InvalidCredential("credential kind does not match its prefix")
    if session.revoked_at is not None:
        # A sign-in ends at its exchange or its sign-out, an operator
        # token at its revoke or its sign-out.
        raise CredentialExpired(REVOKED_MESSAGE.get(kind, "session revoked"))
    now = utcnow()
    if session.expires_at <= now:
        raise CredentialExpired("session expired")
    # The idle lifetime beside the absolute one. A session no request has
    # touched yet (one an older release wrote) starts its idle clock at
    # its first use here, not at its creation.
    seen = session.last_seen_at
    if seen is not None and seen + idle_ttl <= now:
        raise CredentialExpired("session idle")


async def live_user(storage: TenancyStorageInterface, ctx: TenantScope, user_id: UUID) -> User:
    """Existence and tenancy, or NotFound."""
    user = await storage.read_user(ctx.org_id, user_id)
    if user is None or user.deleted_at is not None:
        raise NotFound(f"user {user_id} not found")
    return user


async def principal_in(
    storage: TenancyStorageInterface, org_id: UUID, identity_id: UUID, most: int
) -> tuple[Org, User, Membership]:
    """The principal a verified identity is in `org_id`, or NotAuthorized: the
    sign-in is good, the tenant is not theirs, so a client keeps its login
    and picks another tenant. A gone org, user, or membership is refused the
    same way; InvalidCredential is for a credential that fails, and a login
    that names a tenant it cannot enter has not failed."""
    users = await users_of(storage, identity_id, most)
    user = next((user for user_org, user in users if user_org == org_id), None)
    if user is None:
        raise NotAuthorized("this identity is not a member of that org")
    org = await storage.read_org(org_id)
    if org is None or org.deleted_at is not None:
        raise NotAuthorized("that org is gone")
    membership = await storage.read_membership_for_user(org_id, user.id)
    if membership is None:
        raise NotAuthorized("this identity is no longer a member of that org")
    return org, user, membership


async def memberships_of(
    storage: TenancyStorageInterface, identity_id: UUID, most: int
) -> tuple[OrgMembership, ...]:
    """The places a person holds, each the org, the user, and the role, in
    one read whatever their number. The read asks for one past the most a
    person may hold, so a list cut short is never taken for the whole:
    more than that (two adds that raced past the refusal) is
    `MembershipLimitReached`, as `users_of` answers."""
    found = await storage.read_memberships_by_identity(identity_id, most + 1)
    if len(found) > most:
        raise MembershipLimitReached(f"identity {identity_id} is a member of more than {most} orgs")
    return tuple(found)


def provider_logout_url(
    provider: IdentityProviderInterface, ended: Session, return_to: str | None
) -> str | None:
    """Where the browser goes to end the provider's session behind the one
    that ended here. Acme's session is over whatever this answers: a
    provider this process cannot reach leaves the provider's session to
    its own lifetime, and says so."""
    if ended.provider_session_id is None:
        return None
    try:
        return provider.logout_url(session_id=ended.provider_session_id, return_to=return_to)
    except ProviderUnavailable as error:
        log.warning("the provider's session outlives the sign-out: %s", error.message)
        return None
