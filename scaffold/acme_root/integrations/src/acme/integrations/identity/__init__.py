"""The identity provider: the hosted service that proves who a person is.
It hands Acme an issuer, a subject, and a verified email; the identity row,
the sessions, and every id stay Acme's own. It also holds the provider's
side of an org (its organization, its single sign-on, its invitations),
which the tenancy manager creates lazily and names by the Acme org's id.

One interface, a real client (`workos.py`), a deterministic twin
(`twin.py`), and the absent provider (`absent.py`) of a process that signs
nobody in, such as the worker.

A call a request makes carries the request's `deadline`, the instant its
time runs out, which every call it makes shares (ADR 0069). The call ends by
then, `ProviderUnavailable`, as when the provider does not answer. The two
deletions are a worker's, bounded by the item's lease, and take none."""

from abc import ABC, abstractmethod
from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class ProvidedUser(_Frozen):
    """The person as the provider knows them. `id` is the subject."""

    id: str
    email: str
    email_verified: bool
    first_name: str | None = None
    last_name: str | None = None

    @property
    def display_name(self) -> str:
        """The name the provider holds, first and last, or empty."""
        return " ".join(part for part in (self.first_name, self.last_name) if part).strip()


class ProvidedSignIn(_Frozen):
    """A completed sign-in: who, and, when the provider signed them in to one
    of its organizations (an invitation accepted, a single sign-on), which.
    `via_sso` is True only when the person's own identity provider, through
    the organization's connection, vouched for them. `session_id` is the
    provider's own session in the browser that signed in, when it keeps one:
    the session its logout ends. It is not a secret, and it stays on the
    server beside the sign-in it came with."""

    user: ProvidedUser
    organization_id: str | None = None
    via_sso: bool = False
    session_id: str | None = None


class DeviceAuthorization(_Frozen):
    """The start of a device sign-in: the code the device polls with, which
    it keeps to itself, and the code and address the person confirms."""

    device_code: str
    user_code: str
    verification_uri: str
    verification_uri_complete: str
    expires_in: int
    interval: int


class ProvidedOrganization(_Frozen):
    id: str
    name: str
    external_id: str | None
    verified_domains: tuple[str, ...] = ()


class InvitationState(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REVOKED = "revoked"
    EXPIRED = "expired"


class ProvidedInvitation(_Frozen):
    id: str
    email: str
    state: InvitationState
    expires_at: datetime
    organization_id: str | None
    accepted_user_id: str | None = None


PortalIntent = Literal["sso", "domain_verification"]
"""What an admin portal link opens on: the single sign-on connection, or the
verification of the organization's domains."""


class IdentityProviderInterface(ABC):
    @property
    @abstractmethod
    def issuer(self) -> str:
        """The issuer every subject this provider returns is unique under."""
        ...

    @property
    @abstractmethod
    def configured(self) -> bool:
        """False when this process cannot sign anyone in through it."""
        ...

    @abstractmethod
    def authorization_url(
        self,
        *,
        redirect_uri: str,
        state: str,
        code_challenge: str,
        invitation_token: str | None = None,
        screen_hint: Literal["sign-in", "sign-up"] | None = None,
    ) -> str:
        """Where the browser goes to sign in; it comes back to `redirect_uri`
        with a code and `state` as it was handed. `code_challenge` is the
        S256 digest of the verifier the exchange presents (PKCE), so a code
        is worth nothing to anyone who does not hold the verifier. No
        network call."""
        ...

    @abstractmethod
    async def authenticate_code(
        self,
        code: str,
        *,
        code_verifier: str | None,
        invitation_token: str | None = None,
        deadline: datetime | None = None,
    ) -> ProvidedSignIn:
        """Exchanges the code the browser brought back, server-side, with the
        verifier the sign-in started with. ProviderRefused for a code that is
        spent, expired, or never issued, or a verifier that does not match."""
        ...

    @abstractmethod
    def logout_url(self, *, session_id: str, return_to: str | None) -> str:
        """Where the browser goes to end the provider's session `session_id`;
        the provider then sends it on to `return_to`, which must be one of the
        application's sign-out URIs, or to its default one when None. No
        network call."""
        ...

    @abstractmethod
    async def start_device(self, *, deadline: datetime | None = None) -> DeviceAuthorization:
        """Starts a sign-in for a device with no browser of its own."""
        ...

    @abstractmethod
    async def authenticate_device(
        self, device_code: str, *, deadline: datetime | None = None
    ) -> ProvidedSignIn:
        """Asks whether the person confirmed the device sign-in: the sign-in
        when they did, DevicePending or DeviceSlowDown while they have not,
        DeviceDenied or DeviceExpired when it will never come."""
        ...

    @abstractmethod
    async def ensure_organization(
        self, *, external_id: str, name: str, deadline: datetime | None = None
    ) -> ProvidedOrganization:
        """The provider's organization for a Acme org: the one that carries
        `external_id` when there is one, else a new one. Rerun, it creates
        nothing."""
        ...

    @abstractmethod
    async def get_organization(
        self, organization_id: str, *, deadline: datetime | None = None
    ) -> ProvidedOrganization:
        """ProviderRefused for an id the provider does not hold."""
        ...

    @abstractmethod
    async def send_invitation(
        self,
        *,
        email: str,
        organization_id: str,
        expires_in_days: int,
        deadline: datetime | None = None,
    ) -> ProvidedInvitation:
        """The provider sends the email with the sign-in link. ProviderConflict
        when an invitation for the address is pending in the organization."""
        ...

    @abstractmethod
    async def find_pending_invitation(
        self, *, email: str, organization_id: str, deadline: datetime | None = None
    ) -> ProvidedInvitation | None:
        """The pending invitation for the address in the organization."""
        ...

    @abstractmethod
    async def resend_invitation(
        self, invitation_id: str, *, deadline: datetime | None = None
    ) -> ProvidedInvitation: ...

    @abstractmethod
    async def revoke_invitation(
        self, invitation_id: str, *, deadline: datetime | None = None
    ) -> ProvidedInvitation: ...

    @abstractmethod
    async def accepted_invitation(
        self,
        *,
        organization_id: str,
        user_id: str,
        email: str,
        deadline: datetime | None = None,
    ) -> ProvidedInvitation | None:
        """The invitation of the organization the person accepted, when one
        is: the provider's record of who accepted it, which a sign-in reads
        rather than the address the invitation was sent to, since an
        invitation to a company's domain may be accepted with another address
        of the same domain. `email` is the person's address: the invitation
        sent to it is looked for first, and the organization's others only
        when none of those is the one."""
        ...

    @abstractmethod
    async def portal_link(
        self,
        *,
        organization_id: str,
        intent: PortalIntent,
        return_url: str,
        deadline: datetime | None = None,
    ) -> str:
        """A short-lived link to the provider's admin portal for the
        organization, where its admin sets up single sign-on themselves."""
        ...

    @abstractmethod
    async def delete_user(self, user_id: str) -> None:
        """Deletes the person the provider knows by `user_id` (the subject),
        for good, so a sign-in through the provider meets nobody it knows.
        A user it no longer holds is deleted already, and that is no error:
        a rerun is one deletion.
        ProviderUnavailable when the provider cannot be reached, answers with
        a server error, or refuses the process's own key (revoked, or
        without the permission), which a person fixes; ProviderRefused when
        it refuses the call itself."""
        ...

    @abstractmethod
    async def delete_organization(self, organization_id: str) -> None:
        """Deletes the provider's organization, for good, with what it holds:
        its connections, its verified domains, and its pending invitations,
        so no sign-in through it reaches the Acme org it stood for. One it
        no longer holds is deleted already, and that is no error: a rerun is
        one deletion. It fails as `delete_user` does: ProviderUnavailable for
        a provider out of reach or a refused key, ProviderRefused for the
        call itself."""
        ...

    @abstractmethod
    def describe(self) -> str: ...

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...
