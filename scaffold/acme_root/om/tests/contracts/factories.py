from datetime import timedelta
from uuid import UUID

from acme.om.base import new_id, utcnow
from acme.om.opcontext import CredentialKind, OperatorRole, Role
from acme.om.tenancy.types.api_key import ApiKey
from acme.om.tenancy.types.identity import Identity
from acme.om.tenancy.types.invitation import Invitation
from acme.om.tenancy.types.membership import Membership
from acme.om.tenancy.types.org import Org, OrgKind
from acme.om.tenancy.types.session import Session
from acme.om.tenancy.types.socket_ticket import SocketTicket
from acme.om.tenancy.types.user import User


def make_org(name: str = "Ajax") -> Org:
    now = utcnow()
    org_id = new_id()
    return Org(
        id=org_id,
        name=name,
        created_at=now,
        updated_at=now,
        created_by=org_id,
        updated_by=org_id,
        slug=f"{name.lower()}-{org_id.hex[-12:]}",  # the random tail; the head is the millisecond
    )


def make_personal_org(identity_id: UUID, name: str = "Dee") -> Org:
    """A person's personal org: the kind, and the person it belongs to."""
    return make_org(name).model_copy(
        update={"kind": OrgKind.PERSONAL, "personal_identity_id": identity_id}
    )


def make_identity(
    email: str | None = None, *, operator_role: OperatorRole | None = None
) -> Identity:
    now = utcnow()
    identity_id = new_id()
    return Identity(
        id=identity_id,
        created_at=now,
        updated_at=now,
        created_by=identity_id,
        updated_by=identity_id,
        email=email or f"{identity_id.hex[-12:]}@example.test",
        operator_role=operator_role,
    )


def make_user(identity_id: UUID, email: str = "someone@example.test") -> User:
    now = utcnow()
    user_id = new_id()
    return User(
        id=user_id,
        created_at=now,
        updated_at=now,
        created_by=user_id,
        updated_by=user_id,
        identity_id=identity_id,
        email=email,
        display_name="Someone",
    )


def make_membership(user_id: UUID, role: Role = Role.MEMBER) -> Membership:
    now = utcnow()
    return Membership(
        id=new_id(),
        created_at=now,
        updated_at=now,
        created_by=user_id,
        updated_by=user_id,
        user_id=user_id,
        role=role,
    )


def make_session(
    identity_id: UUID, user_id: UUID, token_hash: str, ttl: timedelta = timedelta(hours=1)
) -> Session:
    now = utcnow()
    return Session(
        id=new_id(),
        created_at=now,
        updated_at=now,
        created_by=user_id,
        updated_by=user_id,
        identity_id=identity_id,
        user_id=user_id,
        token_hash=token_hash,
        credential_kind=CredentialKind.SESSION_TOKEN,
        expires_at=now + ttl,
    )


def make_sign_in(identity_id: UUID, token_hash: str) -> Session:
    """A sign-in: the credential with no tenant, kept in the system scope."""
    now = utcnow()
    return Session(
        id=new_id(),
        created_at=now,
        updated_at=now,
        created_by=identity_id,
        updated_by=identity_id,
        identity_id=identity_id,
        token_hash=token_hash,
        credential_kind=CredentialKind.LOGIN,
        expires_at=now + timedelta(minutes=10),
    )


def make_operator_token(
    identity_id: UUID,
    token_hash: str,
    ttl: timedelta = timedelta(hours=1),
    role: OperatorRole = OperatorRole.READ,
) -> Session:
    """An operator token: a session of the system scope with one permission."""
    now = utcnow()
    return Session(
        id=new_id(),
        created_at=now,
        updated_at=now,
        created_by=identity_id,
        updated_by=identity_id,
        identity_id=identity_id,
        token_hash=token_hash,
        credential_kind=CredentialKind.OPERATOR_TOKEN,
        expires_at=now + ttl,
        operator_role=role,
    )


def make_api_key(user_id: UUID, key_hash: str) -> ApiKey:
    now = utcnow()
    return ApiKey(
        id=new_id(),
        name="ci",
        created_at=now,
        updated_at=now,
        created_by=user_id,
        updated_by=user_id,
        user_id=user_id,
        key_hash=key_hash,
        role=Role.MEMBER,
        expires_at=now,
    )


def make_socket_ticket(
    user_id: UUID, ticket_hash: str, ttl: timedelta = timedelta(minutes=1)
) -> SocketTicket:
    now = utcnow()
    return SocketTicket(
        id=new_id(),
        created_at=now,
        user_id=user_id,
        ticket_hash=ticket_hash,
        credential_kind=CredentialKind.SESSION_TOKEN,
        credential_id=new_id(),
        expires_at=now + ttl,
    )


def make_invitation(
    email: str = "invited@example.test",
    role: Role = Role.MEMBER,
    *,
    expires_in: timedelta = timedelta(days=7),
    provider_invitation_id: str | None = None,
) -> Invitation:
    now = utcnow()
    invitation_id = new_id()
    inviter = new_id()
    return Invitation(
        id=invitation_id,
        created_at=now,
        updated_at=now,
        created_by=inviter,
        updated_by=inviter,
        email=email,
        role=role,
        provider_invitation_id=provider_invitation_id or f"invitation_{invitation_id.hex}",
        expires_at=now + expires_in,
    )
