from datetime import datetime
from typing import ClassVar
from uuid import UUID

from acme.om.base import EMPTY_UUID, Identifiable, Trackable
from acme.om.opcontext import CredentialKind, OperatorRole


class Session(Identifiable, Trackable):
    """A login credential or an operator token (no tenant, stored under the
    system scope) or a tenant-scoped session token. Only the hash of the
    token is kept."""

    MANAGER_OWNED_FIELDS: ClassVar[tuple[str, ...]] = (
        "token_hash",
        "expires_at",
        "revoked_at",
        "last_seen_at",
        "second_factor_at",
        "operator_role",
        "provider_session_id",
    )
    """Every fact of a credential is the manager's: a caller names a session,
    never writes one."""

    identity_id: UUID
    user_id: UUID = EMPTY_UUID  # EMPTY_UUID while the credential carries no tenant
    token_hash: str
    credential_kind: CredentialKind
    expires_at: datetime
    revoked_at: datetime | None = None
    # When the session was last presented, for the idle lifetime; None for a
    # row no request has touched since it was written.
    last_seen_at: datetime | None = None
    # A sign-in that verified a TOTP code records when; the operator gate
    # admits an enrolled operator only on a sign-in that carries it.
    second_factor_at: datetime | None = None
    # An operator token's one permission; None on every other kind.
    operator_role: OperatorRole | None = None
    # The identity provider's own session in the browser that signed in, when
    # the sign-in left one (AuthKit's, through its hosted page): the sign-out
    # ends it too. Carried from the sign-in to every session exchanged from
    # it. Not a secret, and never on the wire but in the sign-out's answer;
    # None for the device sign-in, the local sign-in, and an operator token.
    provider_session_id: str | None = None
