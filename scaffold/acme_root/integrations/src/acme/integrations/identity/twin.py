"""The identity provider's twin: deterministic, in-process, and for a local
environment only (the configured root refuses it anywhere else). It speaks
the interface's shapes and keeps its own users, organizations, codes,
device sign-ins, and invitations in memory.

A test or a local process drives the person's side through the twin's own
methods: `issue_code` stands for a person finishing the hosted sign-in and
coming back with a code, `confirm_device` for a person confirming a device
sign-in, `accept_invitation` for a person following an invitation's link,
and `verify_domain` for an organization's admin proving a domain. Every id
it mints says it is the twin's (`twin_`), so a row that holds one names its
provenance."""

import base64
import hashlib
import itertools
from datetime import UTC, datetime, timedelta
from typing import Literal
from urllib.parse import urlencode

from acme.integrations.exceptions import (
    DeviceDenied,
    DeviceExpired,
    DevicePending,
    ProviderConflict,
    ProviderRefused,
    ProviderUnavailable,
)
from acme.integrations.identity import (
    DeviceAuthorization,
    IdentityProviderInterface,
    InvitationState,
    PortalIntent,
    ProvidedInvitation,
    ProvidedOrganization,
    ProvidedSignIn,
    ProvidedUser,
)

TWIN_ISSUER = "twin://identity"
TWIN_AUTHORIZE = "https://identity.twin.invalid/authorize"
TWIN_PORTAL = "https://identity.twin.invalid/portal"
TWIN_DEVICE = "https://identity.twin.invalid/device"
TWIN_LOGOUT = "https://identity.twin.invalid/logout"


def s256(verifier: str) -> str:
    """The PKCE S256 challenge of a verifier."""
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


class IdentityProviderTwinImpl(IdentityProviderInterface):
    def __init__(self, clock: type[datetime] = datetime) -> None:
        self._counter = itertools.count(1)
        self._clock = clock
        self.users: dict[str, ProvidedUser] = {}
        self.organizations: dict[str, ProvidedOrganization] = {}
        self.invitations: dict[str, ProvidedInvitation] = {}
        self.sent: list[ProvidedInvitation] = []
        """Every invitation email the twin would have sent, resends included."""
        self._codes: dict[str, ProvidedSignIn] = {}
        self._challenges: dict[str, str] = {}
        self._devices: dict[str, ProvidedSignIn | Literal["pending", "denied", "expired"]] = {}
        self.deleted: list[str] = []
        """Every user the twin deleted, in order."""
        self.deleted_organizations: list[str] = []
        """Every organization the twin deleted, in order."""
        self.unavailable_for = 0
        """How many of the next deletions answer as a provider that is down,
        for a test of the retry."""

    def _id(self, kind: str) -> str:
        return f"twin_{kind}_{next(self._counter):06d}"

    def _now(self) -> datetime:
        return self._clock.now(UTC)

    # The person's side, driven by a test or a local process.

    def user(
        self,
        email: str,
        *,
        first_name: str | None = None,
        last_name: str | None = None,
        email_verified: bool = True,
    ) -> ProvidedUser:
        """The twin's user for the address, made the first time it is named."""
        for existing in self.users.values():
            if existing.email == email:
                return existing
        made = ProvidedUser(
            id=self._id("user"),
            email=email,
            email_verified=email_verified,
            first_name=first_name,
            last_name=last_name,
        )
        self.users[made.id] = made
        return made

    def issue_code(
        self,
        email: str,
        *,
        first_name: str | None = None,
        last_name: str | None = None,
        email_verified: bool = True,
        organization_id: str | None = None,
        via_sso: bool = False,
        code_challenge: str | None = None,
    ) -> str:
        """A person finished the hosted sign-in: the code the browser brings
        back, and the twin's session in that browser, which the sign-in names.
        With `code_challenge`, the exchange must present its verifier."""
        person = self.user(
            email, first_name=first_name, last_name=last_name, email_verified=email_verified
        )
        code = self._id("code")
        self._codes[code] = ProvidedSignIn(
            user=person,
            organization_id=organization_id,
            via_sso=via_sso,
            session_id=self._id("session"),
        )
        if code_challenge is not None:
            self._challenges[code] = code_challenge
        return code

    def confirm_device(self, user_code: str, email: str, *, deny: bool = False) -> None:
        """A person confirmed (or declined) the device sign-in shown as `user_code`."""
        for device_code in self._devices:
            if device_code.endswith(user_code):
                self._devices[device_code] = (
                    "denied" if deny else ProvidedSignIn(user=self.user(email))
                )
                return
        raise KeyError(user_code)

    def expire_device(self, user_code: str) -> None:
        for device_code in self._devices:
            if device_code.endswith(user_code):
                self._devices[device_code] = "expired"
                return
        raise KeyError(user_code)

    def accept_invitation(self, invitation_id: str, email: str | None = None) -> str:
        """A person followed the invitation's link and signed in: the invitation
        is accepted by them and the code the browser brings back names its
        organization."""
        invitation = self.invitations[invitation_id]
        person = self.user(email or invitation.email)
        self.invitations[invitation_id] = invitation.model_copy(
            update={"state": InvitationState.ACCEPTED, "accepted_user_id": person.id}
        )
        code = self._id("code")
        self._codes[code] = ProvidedSignIn(
            user=person, organization_id=invitation.organization_id, session_id=self._id("session")
        )
        return code

    def verify_domain(self, organization_id: str, domain: str) -> None:
        org = self.organizations[organization_id]
        self.organizations[organization_id] = org.model_copy(
            update={"verified_domains": (*org.verified_domains, domain.lower())}
        )

    # The interface.

    @property
    def issuer(self) -> str:
        return TWIN_ISSUER

    @property
    def configured(self) -> bool:
        return True

    def authorization_url(
        self,
        *,
        redirect_uri: str,
        state: str,
        code_challenge: str,
        invitation_token: str | None = None,
        screen_hint: Literal["sign-in", "sign-up"] | None = None,
    ) -> str:
        query: dict[str, str] = {
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        if invitation_token is not None:
            query["invitation_token"] = invitation_token
        if screen_hint is not None:
            query["screen_hint"] = screen_hint
        return f"{TWIN_AUTHORIZE}?{urlencode(query)}"

    def logout_url(self, *, session_id: str, return_to: str | None) -> str:
        query = {"session_id": session_id}
        if return_to is not None:
            query["return_to"] = return_to
        return f"{TWIN_LOGOUT}?{urlencode(query)}"

    async def authenticate_code(
        self,
        code: str,
        *,
        code_verifier: str | None,
        invitation_token: str | None = None,
        deadline: datetime | None = None,
    ) -> ProvidedSignIn:
        signed_in = self._codes.pop(code, None)
        if signed_in is None:
            raise ProviderRefused("the code is spent, expired, or was never issued")
        challenge = self._challenges.pop(code, None)
        if challenge is not None and (code_verifier is None or s256(code_verifier) != challenge):
            raise ProviderRefused("the verifier does not match the sign-in's challenge")
        return signed_in

    async def start_device(self, *, deadline: datetime | None = None) -> DeviceAuthorization:
        user_code = f"{next(self._counter):04d}-TWIN"
        device_code = f"{self._id('device')}:{user_code}"
        self._devices[device_code] = "pending"
        return DeviceAuthorization(
            device_code=device_code,
            user_code=user_code,
            verification_uri=TWIN_DEVICE,
            verification_uri_complete=f"{TWIN_DEVICE}?user_code={user_code}",
            expires_in=300,
            interval=5,
        )

    async def authenticate_device(
        self, device_code: str, *, deadline: datetime | None = None
    ) -> ProvidedSignIn:
        state = self._devices.get(device_code)
        if state is None:
            raise ProviderRefused("unknown device code")
        if state == "pending":
            raise DevicePending("the person has not confirmed the sign-in yet")
        if state == "denied":
            raise DeviceDenied("the person declined the sign-in")
        if state == "expired":
            raise DeviceExpired("the device code expired")
        del self._devices[device_code]
        return state

    async def ensure_organization(
        self, *, external_id: str, name: str, deadline: datetime | None = None
    ) -> ProvidedOrganization:
        for org in self.organizations.values():
            if org.external_id == external_id:
                return org
        made = ProvidedOrganization(id=self._id("org"), name=name, external_id=external_id)
        self.organizations[made.id] = made
        return made

    async def get_organization(
        self, organization_id: str, *, deadline: datetime | None = None
    ) -> ProvidedOrganization:
        org = self.organizations.get(organization_id)
        if org is None:
            raise ProviderRefused(f"no organization {organization_id}")
        return org

    async def send_invitation(
        self,
        *,
        email: str,
        organization_id: str,
        expires_in_days: int,
        deadline: datetime | None = None,
    ) -> ProvidedInvitation:
        if organization_id not in self.organizations:
            raise ProviderRefused(f"no organization {organization_id}")
        if await self.find_pending_invitation(email=email, organization_id=organization_id):
            raise ProviderConflict("an invitation for this address is pending")
        invitation = ProvidedInvitation(
            id=self._id("invitation"),
            email=email,
            state=InvitationState.PENDING,
            expires_at=self._now() + timedelta(days=expires_in_days),
            organization_id=organization_id,
        )
        self.invitations[invitation.id] = invitation
        self.sent.append(invitation)
        return invitation

    async def find_pending_invitation(
        self, *, email: str, organization_id: str, deadline: datetime | None = None
    ) -> ProvidedInvitation | None:
        for invitation in self.invitations.values():
            if (
                invitation.email == email
                and invitation.organization_id == organization_id
                and invitation.state is InvitationState.PENDING
            ):
                return invitation
        return None

    async def resend_invitation(
        self, invitation_id: str, *, deadline: datetime | None = None
    ) -> ProvidedInvitation:
        invitation = self.invitations.get(invitation_id)
        if invitation is None or invitation.state is InvitationState.ACCEPTED:
            raise ProviderRefused("the invitation cannot be sent again")
        resent = invitation.model_copy(
            update={
                "state": InvitationState.PENDING,
                "expires_at": self._now() + timedelta(days=7),
            }
        )
        self.invitations[invitation_id] = resent
        self.sent.append(resent)
        return resent

    async def revoke_invitation(
        self, invitation_id: str, *, deadline: datetime | None = None
    ) -> ProvidedInvitation:
        invitation = self.invitations.get(invitation_id)
        if invitation is None or invitation.state is not InvitationState.PENDING:
            raise ProviderRefused("only a pending invitation is revoked")
        revoked = invitation.model_copy(update={"state": InvitationState.REVOKED})
        self.invitations[invitation_id] = revoked
        return revoked

    async def accepted_invitation(
        self,
        *,
        organization_id: str,
        user_id: str,
        email: str,
        deadline: datetime | None = None,
    ) -> ProvidedInvitation | None:
        # The record of who accepted decides, whatever address it was sent to.
        for invitation in self.invitations.values():
            if (
                invitation.organization_id == organization_id
                and invitation.accepted_user_id == user_id
                and invitation.state is InvitationState.ACCEPTED
            ):
                return invitation
        return None

    async def portal_link(
        self,
        *,
        organization_id: str,
        intent: PortalIntent,
        return_url: str,
        deadline: datetime | None = None,
    ) -> str:
        if organization_id not in self.organizations:
            raise ProviderRefused(f"no organization {organization_id}")
        query = urlencode(
            {"organization": organization_id, "intent": intent, "return_url": return_url}
        )
        return f"{TWIN_PORTAL}?{query}"

    async def delete_user(self, user_id: str) -> None:
        if self.unavailable_for > 0:
            self.unavailable_for -= 1
            raise ProviderUnavailable("deleting the user: the twin is down")
        if self.users.pop(user_id, None) is not None:
            self.deleted.append(user_id)

    async def delete_organization(self, organization_id: str) -> None:
        if self.unavailable_for > 0:
            self.unavailable_for -= 1
            raise ProviderUnavailable("deleting the organization: the twin is down")
        if self.organizations.pop(organization_id, None) is not None:
            self.deleted_organizations.append(organization_id)
            for invitation_id, invitation in list(self.invitations.items()):
                if invitation.organization_id == organization_id:
                    del self.invitations[invitation_id]

    def describe(self) -> str:
        return "identity provider: the twin (in-process, local only)"

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None
