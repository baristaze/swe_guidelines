"""A single-use socket ticket, as a row, and what redeeming it yields. The
ticket stands for the session or api key it was minted under; redeeming it
is one conditional write on this row, so a replay is refused by the
database, not by a cache."""

from datetime import datetime
from uuid import UUID

from acme.om.base import Created, Identifiable, Platform
from acme.om.opcontext import CredentialKind, OpContext


class SocketTicket(Identifiable, Created):
    user_id: UUID
    ticket_hash: str  # only the hash of the ticket is kept
    credential_kind: CredentialKind  # session_token or api_key
    credential_id: UUID
    expires_at: datetime
    redeemed_at: datetime | None = None


class SocketPrincipal(Platform):
    """What a redeemed ticket yields: the context the socket runs as and the
    instant its authority ends, which is the expiry of the session or api
    key behind the ticket. A socket is a request that stays open; it holds
    this context for the life of the connection and closes at `expires_at`
    whatever the client does, so a revocation the bus never delivered is
    still bounded. The socket re-checks the credential on an interval of
    its own as well, so that bound is minutes and not the expiry.

    `credential_kind` is the kind of the credential behind the ticket, a
    session token or an api key; the context's own kind is the ticket's.
    `membership_id` is the membership the context was built from: a change
    to it names the membership, not the user."""

    ctx: OpContext
    expires_at: datetime
    credential_kind: CredentialKind
    membership_id: UUID
