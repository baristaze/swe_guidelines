"""Read models that carry a freshly minted secret in the clear, exactly once."""

from datetime import datetime
from uuid import UUID

from acme.om.base import OmBase
from acme.om.context import OperatorRole, Role
from acme.om.tenancy.types.api_key import ApiKey
from acme.om.tenancy.types.auth_session import AuthSession
from acme.om.tenancy.types.org import Org
from acme.om.tenancy.types.user import User


class OrgMembership(OmBase):
    org: Org
    user: User
    role: Role


class IssuedLogin(OmBase):
    token: str
    expires_at: datetime
    memberships: tuple[OrgMembership, ...]


class IssuedAuthSession(OmBase):
    token: str
    expires_at: datetime
    org: Org
    user: User
    role: Role


class IssuedApiKey(OmBase):
    key: str
    api_key: ApiKey


class IssuedTicket(OmBase):
    """A single-use, short-lived socket ticket; redeeming it re-checks the credential behind it."""

    ticket: str
    expires_at: datetime


class IssuedOperatorToken(OmBase):
    """An operator token in the clear, once: one permission, an hour at most.
    `id` names it afterwards, in the list of live tokens and in the revoke of
    one, and is no secret."""

    id: UUID
    token: str
    expires_at: datetime
    operator_role: OperatorRole


class IssuedTotpSecret(OmBase):
    """A freshly minted TOTP secret, once, as the `otpauth://` URI an
    authenticator app reads; only the sealed secret is kept."""

    otpauth_uri: str


class SignInStart(OmBase):
    """Where the browser goes to sign in, and the PKCE verifier the caller
    keeps beside its state and hands back with the code. The provider holds
    only the verifier's digest, so a code is worth nothing without it."""

    authorization_url: str
    code_verifier: str


class SignedOut(OmBase):
    """A credential ended by its own holder, a session, a sign-in, or an
    operator token, and where the browser goes next to end the identity
    provider's session behind it: None when the sign-in left none there
    (the device sign-in, the local sign-in, an operator token)."""

    auth_session: AuthSession
    provider_logout_url: str | None = None


class AccountDeleted(OmBase):
    """A person's account, gone: when, and where the browser goes next to end
    the identity provider's session behind the session that asked, as a
    sign-out answers. None when the sign-in left no session there."""

    deleted_at: datetime
    provider_logout_url: str | None = None


class OrgDeleted(OmBase):
    """A team org its owner deleted: when, and the session the owner lands on
    in their personal org, which replaces the one that asked. None when that
    session could not be made; the owner then signs in again."""

    deleted_at: datetime
    auth_session: IssuedAuthSession | None = None
