"""The sign-in duty of the tenancy manager: a person proves who they are, a
sign-in becomes a session in one org, and a credential ends."""

from abc import ABC, abstractmethod
from uuid import UUID

from acme.integrations.identity import DeviceAuthorization
from acme.om.context import IdentityContext, RequestContext
from acme.om.tenancy.types.issued import IssuedLogin, IssuedSession, SignedOut, SignInStart
from acme.om.tenancy.types.page import OrgMembershipPage


class TenancySignInManagerInterface(ABC):
    """A delegate of `TenancyManagerInterface`, reached as `tenancy.sign_in`.
    Every operation is platform-internal and runs before a tenant is chosen:
    it takes the request stage, or the identity stage the manager's
    `authenticate_login` produces. None constructs a stage."""

    @abstractmethod
    async def sign_in_url(
        self,
        rctx: RequestContext,
        redirect_uri: str,
        state: str,
        *,
        invitation_token: str | None = None,
        sign_up: bool = False,
    ) -> SignInStart:
        """Platform-internal: where a browser goes to sign in at the identity
        provider, and the PKCE verifier the caller keeps and hands back with
        the code. It comes back to `redirect_uri` with a code and `state` as
        it was handed; the state is the caller's, which binds the round trip
        to the tab that started it. A redirect this environment does not name as
        its own is refused (ValidationFailed): a deployed environment never
        sends a code anywhere else. Unavailable when no provider is
        configured."""
        ...

    @abstractmethod
    async def sign_in_with_code(
        self,
        rctx: RequestContext,
        code: str,
        invitation_token: str | None = None,
        *,
        code_verifier: str | None = None,
    ) -> IssuedLogin:
        """Platform-internal: the identity provider's sign-in, finished. The code
        the browser brought back is exchanged with the provider, server-side,
        with the verifier the sign-in started with, for the person it vouches
        for: an issuer, a subject, and a verified
        email. The identity is found by the issuer and the subject; else by
        the email, and linked to the subject from then on (a person the
        seeding, the operator plane, or an older release made); else made,
        with their personal org, in one commit: a first sign-in is a sign-up.
        A sign-in that accepted an invitation lands the membership it names,
        and one through an org's single sign-on lands a membership when the
        person's address is in a domain the org verified (`sso_joins`). The
        answer is a login and the places, as every sign-in's.

        SignInRefused for a code the provider will not exchange;
        EmailNotVerified when the provider has not verified the address;
        Unavailable when the provider cannot be reached or is not
        configured."""
        ...

    @abstractmethod
    async def start_device_sign_in(self, rctx: RequestContext) -> DeviceAuthorization:
        """Platform-internal: a sign-in for a device with no browser of its own,
        the command line. The person confirms the code the answer names at the
        provider's address, in any browser; the device keeps the device code
        and finishes with it."""
        ...

    @abstractmethod
    async def finish_device_sign_in(self, rctx: RequestContext, device_code: str) -> IssuedLogin:
        """Platform-internal: asks whether the person confirmed the device
        sign-in. When they did, it is finished as `sign_in_with_code` finishes
        a browser's. SignInPending (or SignInSlowDown) while they have not;
        SignInRefused when they declined or the code expired."""
        ...

    @abstractmethod
    async def dev_sign_in(
        self, rctx: RequestContext, email: str, display_name: str = ""
    ) -> IssuedLogin:
        """Platform-internal, local and test only: a sign-in by address alone,
        for the seed, the demo recorders, the traffic generator, and the tests,
        which need people without a browser round trip. The identity is found
        by the email or made with its personal org, as a first sign-in makes
        one. NotFound unless the process was built with it on, which a deployed
        environment refuses at boot."""
        ...

    @abstractmethod
    async def verify_second_factor(self, ictx: IdentityContext, totp_code: str) -> IssuedLogin:
        """Platform-internal: a sign-in's second factor. The login presented is
        answered with a new one that records the verified code, which the
        operator gate asks for, and ends in the write that lands it, so a
        person holds one sign-in at a time; a wrong code ends nothing. The
        code is checked against the identity's enrolled secret and refused
        when it was used already. A run of wrong codes for the email makes
        the next one wait (SignInDelayed). Only a login credential is taken
        (InvalidCredential otherwise)."""
        ...

    @abstractmethod
    async def exchange_login(self, ictx: IdentityContext, org_id: UUID) -> IssuedSession:
        """Platform-internal: exchanges the verified identity for a tenant-scoped
        session token; NotAuthorized when the identity is not a member of
        `org_id`, and the credential presented still stands. A sign-in is
        exchanged once: it ends in the same write that lands the session, so
        one sign-in makes one session. A second exchange of it, a retry after
        a lost answer among them, is refused with CredentialExpired, and the
        person signs in again; of two exchanges at once, one lands. An
        identity proven by a session is a switch: that session
        ends in the same write that lands the new one, announced as any
        revocation is, so its socket closes and a tab never holds two live
        sessions. A session another write already ended is refused with
        CredentialExpired and nothing lands. An operator token never enters a
        tenant (InvalidCredential)."""
        ...

    @abstractmethod
    async def get_identity_memberships(
        self, ictx: IdentityContext, after: UUID | None, limit: int
    ) -> OrgMembershipPage:
        """Platform-internal: no tenant is chosen, so the list is the identity's.
        The places the verified identity holds, each its org, its user, and its
        role, the same choice a sign-in answers with; by user id, a page at a
        time as `TenancyMembersManagerInterface.get_users` pages. Deleted orgs
        and ended memberships are not listed. An operator token lists nothing (InvalidCredential)."""
        ...

    @abstractmethod
    async def logout(self, ictx: IdentityContext, return_to: str | None = None) -> SignedOut:
        """Platform-internal: ending its own sign-in is an operation of the
        identity stage. Ends the credential the stage came from, whichever
        it is: a session, announced so the socket it opened closes; a sign-in,
        with or without its second factor; an operator token, which is the
        operator's sign-out. It answers where the browser goes to end the
        identity provider's session behind it, when the sign-in left one
        there. `return_to` is where the provider sends the browser after: one
        of `sign_out_return_uris`, else refused; None leaves it to the
        provider's default. An api key has no sign-out: it is never an
        identity stage, and its holder revokes it."""
        ...
