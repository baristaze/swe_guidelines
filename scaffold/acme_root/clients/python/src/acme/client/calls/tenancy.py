"""The tenancy namespace's calls: the sign-in, the org a caller acts in, its
members and invitations, and the operator plane's reads and writes of orgs."""

from typing import Any, Literal
from uuid import UUID, uuid4

from acme.client.transport import LIMIT_MAX, ApiError, Transport
from acme.client.types.common import Role
from acme.client.types.tenancy import (
    DeviceSignInView,
    InvitationPageView,
    InvitationView,
    IssuedAuthSessionView,
    IssuedLoginView,
    MembershipChoicePageView,
    MembershipChoiceView,
    MeView,
    OperatorTokenPageView,
    OperatorTokenView,
    OperatorView,
    OrgView,
    SignedOutView,
    SignInStartView,
    SsoLinkView,
    SystemSizeView,
    UserPageView,
    UserView,
)


class TenancyCalls(Transport):
    """Sign-in, memberships, members, invitations, and the operator plane.
    On the operator plane the bearer is an operator token, never a tenant
    session; a caller sets `token` to it. The operator's own sign-in, with
    its second factor, mints one token and does nothing else."""

    async def start_sign_in(
        self,
        redirect_uri: str,
        state: str,
        *,
        invitation_token: str | None = None,
        sign_up: bool = False,
    ) -> SignInStartView:
        """Where a browser goes to sign in at the identity provider; it comes
        back to `redirect_uri` with a code and `state`."""
        body: dict[str, object] = {"redirect_uri": redirect_uri, "state": state, "sign_up": sign_up}
        if invitation_token is not None:
            body["invitation_token"] = invitation_token
        answer = await self.request("POST", "/v1/auth/sign-in", json=body, token=None)
        return SignInStartView.model_validate(answer)

    async def finish_sign_in(
        self, code: str, code_verifier: str, invitation_token: str | None = None
    ) -> IssuedLoginView:
        """The code the browser brought back and the verifier `start_sign_in`
        answered with, exchanged by the API; answered as every sign-in is. A
        person nobody knew is signed up by it."""
        body: dict[str, object] = {"code": code, "code_verifier": code_verifier}
        if invitation_token is not None:
            body["invitation_token"] = invitation_token
        answer = await self.request("POST", "/v1/auth/callback", json=body, token=None)
        return IssuedLoginView.model_validate(answer)

    async def start_device_sign_in(self) -> DeviceSignInView:
        """A sign-in for a program with no browser of its own: the person
        confirms the code at the address the answer names, in any browser."""
        answer = await self.request("POST", "/v1/auth/device", token=None)
        return DeviceSignInView.model_validate(answer)

    async def finish_device_sign_in(self, device_code: str) -> IssuedLoginView:
        """Asks once whether the person confirmed the device sign-in. Before
        they do, an `ApiError` with status 400 and code `sign_in_pending` (or
        `sign_in_slow_down`); the caller asks again after the interval."""
        answer = await self.request(
            "POST", "/v1/auth/device/token", json={"device_code": device_code}, token=None
        )
        return IssuedLoginView.model_validate(answer)

    async def dev_sign_in(self, email: str, display_name: str = "") -> IssuedLoginView:
        """Local and test only: a sign-in by address alone, which a deployed
        environment answers with 404. A person nobody knew is made, with
        their personal org."""
        answer = await self.request(
            "POST",
            "/v1/auth/dev-sign-in",
            json={"email": email, "display_name": display_name},
            token=None,
        )
        return IssuedLoginView.model_validate(answer)

    async def verify_second_factor(self, login_token: str, totp_code: str) -> IssuedLoginView:
        """A sign-in's second factor: a new sign-in that records the verified
        code, which the operator plane asks of an enrolled operator."""
        answer = await self.request(
            "POST", "/v1/auth/second-factor", json={"totp_code": totp_code}, token=login_token
        )
        return IssuedLoginView.model_validate(answer)

    async def exchange_auth_session(self, login_token: str, org_id: UUID) -> IssuedAuthSessionView:
        body = await self.request(
            "POST", "/v1/auth/sessions", json={"org_id": str(org_id)}, token=login_token
        )
        return IssuedAuthSessionView.model_validate(body)

    async def create_org(
        self, name: str, slug: str | None = None, *, idempotency_key: str | None = None
    ) -> MembershipChoiceView:
        """A team org the signed-in person makes and owns; the answer is their
        place in it, which `switch` takes. A creating call, so it always
        carries an idempotency key."""
        body: dict[str, object] = {"name": name}
        if slug is not None:
            body["slug"] = slug
        answer = await self.request(
            "POST", "/v1/orgs", json=body, idempotency_key=idempotency_key or str(uuid4())
        )
        return MembershipChoiceView.model_validate(answer)

    async def memberships(
        self, *, cursor: str | None = None, limit: int = LIMIT_MAX
    ) -> MembershipChoicePageView:
        """The signed-in person's places, with the session the client holds."""
        params: dict[str, Any] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        body = await self.request("GET", "/v1/auth/memberships", params=params)
        return MembershipChoicePageView.model_validate(body)

    async def every_membership(self, limit: int = LIMIT_MAX) -> list[MembershipChoiceView]:
        """Every place, page after page, as `every_user` reads the members."""
        found: list[MembershipChoiceView] = []
        cursor: str | None = None
        while True:
            page = await self.memberships(cursor=cursor, limit=limit)
            found += page.items
            if page.next_cursor is None:
                return found
            cursor = page.next_cursor

    async def switch_auth_session(self, org_id: UUID) -> IssuedAuthSessionView:
        """The exchange presented with the session the client holds: the API
        ends that session in the same write, and the client carries the new
        one from here on, so it never holds two."""
        issued = await self.exchange_auth_session(self._require_token(), org_id)
        self.token = issued.token
        return issued

    def _require_token(self) -> str:
        if not self.token:
            raise ApiError(401, "not_authenticated", "no session to switch from", None)
        return self.token

    async def invitations(
        self, *, cursor: str | None = None, limit: int = LIMIT_MAX
    ) -> InvitationPageView:
        params: dict[str, object] = {"limit": limit}
        if cursor is not None:
            params["cursor"] = cursor
        answer = await self.request("GET", "/v1/invitations", params=params)
        return InvitationPageView.model_validate(answer)

    async def invite_member(
        self, email: str, role: Role = Role.member, *, idempotency_key: str | None = None
    ) -> InvitationView:
        """The identity provider sends the person the email with the link; a
        creating call, so it always carries an idempotency key."""
        answer = await self.request(
            "POST",
            "/v1/invitations",
            json={"email": email, "role": role.value},
            idempotency_key=idempotency_key or str(uuid4()),
        )
        return InvitationView.model_validate(answer)

    async def resend_invitation(self, invitation_id: UUID) -> InvitationView:
        answer = await self.request("POST", f"/v1/invitations/{invitation_id}/resend")
        return InvitationView.model_validate(answer)

    async def revoke_invitation(self, invitation_id: UUID) -> InvitationView:
        answer = await self.request("DELETE", f"/v1/invitations/{invitation_id}")
        return InvitationView.model_validate(answer)

    async def sso_link(
        self,
        return_url: str,
        intent: Literal["sso", "domain_verification"] = "sso",
    ) -> SsoLinkView:
        answer = await self.request(
            "POST",
            "/v1/orgs/current/sso-link",
            json={"intent": intent, "return_url": return_url},
        )
        return SsoLinkView.model_validate(answer)

    async def logout(self) -> SignedOutView:
        """Ends the credential this client holds: a session, a sign-in, or an
        operator token. The answer's `provider_logout_url` is a browser's
        next stop; a sign-in without a browser (the device sign-in, the local
        one) has none, and neither has an operator token."""
        return SignedOutView.model_validate(await self.request("POST", "/v1/auth/logout"))

    async def me(self) -> MeView:
        return MeView.model_validate(await self.request("GET", "/v1/me"))

    async def users(self, *, cursor: str | None = None, limit: int = LIMIT_MAX) -> UserPageView:
        params: dict[str, Any] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        body = await self.request("GET", "/v1/users", params=params)
        return UserPageView.model_validate(body)

    async def every_user(self, limit: int = LIMIT_MAX) -> list[UserView]:
        """Every member, page after page until the API says there is no next
        one: a caller that names people needs the whole list, and a fixed
        limit would silently leave the rest unnamed."""
        users: list[UserView] = []
        cursor: str | None = None
        while True:
            page = await self.users(cursor=cursor, limit=limit)
            users += page.items
            cursor = page.next_cursor
            if cursor is None:
                return users

    async def admin_me(self) -> OperatorView:
        """Who the operator plane admitted and what the entry grants; the check a
        skill makes before its first read."""
        return OperatorView.model_validate(await self.request("GET", "/v1/admin/me"))

    async def admin_tokens(
        self, *, cursor: str | None = None, limit: int = LIMIT_MAX
    ) -> OperatorTokenPageView:
        """The operator's own live tokens, newest first: the ids a revoke names."""
        params: dict[str, Any] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        body = await self.request("GET", "/v1/admin/me/tokens", params=params)
        return OperatorTokenPageView.model_validate(body)

    async def admin_revoke_token(self, token_id: UUID) -> OperatorTokenView:
        """Ends one of the operator's own tokens at once; one ended already is
        answered as it is."""
        body = await self.request("DELETE", f"/v1/admin/me/tokens/{token_id}")
        return OperatorTokenView.model_validate(body)

    async def admin_size(self) -> SystemSizeView:
        return SystemSizeView.model_validate(await self.request("GET", "/v1/admin/size"))

    async def admin_create_org(
        self,
        name: str,
        slug: str,
        *,
        owner_email: str,
        owner_name: str,
        idempotency_key: str | None = None,
    ) -> OrgView:
        """An org with its owner, as `bootstrap` seeds one; a creating call, so
        it always carries an idempotency key."""
        body = {
            "name": name,
            "slug": slug,
            "owner_email": owner_email,
            "owner_name": owner_name,
        }
        created = await self.request(
            "POST", "/v1/admin/orgs", json=body, idempotency_key=idempotency_key or str(uuid4())
        )
        return OrgView.model_validate(created)

    async def admin_add_member(
        self,
        org_id: UUID,
        email: str,
        *,
        display_name: str,
        role: Role = Role.member,
        idempotency_key: str | None = None,
    ) -> UserView:
        """A person in the org, as `add-member` seeds one; a creating call, so
        it always carries an idempotency key."""
        body = {
            "email": email,
            "display_name": display_name,
            "role": role.value,
        }
        added = await self.request(
            "POST",
            f"/v1/admin/orgs/{org_id}/members",
            json=body,
            idempotency_key=idempotency_key or str(uuid4()),
        )
        return UserView.model_validate(added)

    async def admin_org(self, org_id: UUID) -> OrgView:
        return OrgView.model_validate(await self.request("GET", f"/v1/admin/orgs/{org_id}"))

    async def admin_members(
        self, org_id: UUID, *, cursor: str | None = None, limit: int = LIMIT_MAX
    ) -> UserPageView:
        params: dict[str, Any] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        body = await self.request("GET", f"/v1/admin/orgs/{org_id}/members", params=params)
        return UserPageView.model_validate(body)
