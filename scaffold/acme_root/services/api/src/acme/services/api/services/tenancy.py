"""The tenancy service: sign in through the identity provider, choose a
tenant, the principal, members and their invitations, single sign-on,
sessions, and api keys. The operations before a principal exists
take the stage the gateway reached (the request, then the identity); each
produces the credential the next request presents."""

from abc import ABC, abstractmethod
from uuid import UUID

from acme.om.context import IdentityContext, RequestContext, TenantContext
from acme.om.idempotency.types.attempt import Attempt
from acme.services.api.types.tenancy import (
    AccountDeletedView,
    AddApiKeyRequest,
    ApiKeyPageView,
    ApiKeyView,
    CreateTeamOrgRequest,
    DeleteAccountRequest,
    DeleteOrgRequest,
    DeviceSignInView,
    DeviceTokenRequest,
    DevSignInRequest,
    ExchangeSessionRequest,
    IdentityView,
    InvitationPageView,
    InvitationView,
    InviteMemberRequest,
    IssuedApiKeyView,
    IssuedLoginView,
    IssuedSessionView,
    LogoutRequest,
    MembershipChoicePageView,
    MembershipChoiceView,
    MembershipPageView,
    MembershipView,
    MeView,
    OrgDeletedView,
    OrgView,
    SecondFactorRequest,
    SessionView,
    SignedOutView,
    SignInCallbackRequest,
    SignInStartRequest,
    SignInStartView,
    SsoLinkRequest,
    SsoLinkView,
    UpdateIdentityRequest,
    UpdateMembershipRequest,
    UpdateMeRequest,
    UserPageView,
    UserView,
)


class TenancyServiceInterface(ABC):
    @abstractmethod
    async def start_sign_in(
        self, rctx: RequestContext, body: SignInStartRequest
    ) -> SignInStartView:
        """Platform-internal: no principal exists yet; the answer is where the
        browser goes."""
        ...

    @abstractmethod
    async def finish_sign_in(
        self, rctx: RequestContext, body: SignInCallbackRequest
    ) -> IssuedLoginView:
        """Platform-internal: no principal exists yet; the code comes back."""
        ...

    @abstractmethod
    async def start_device_sign_in(self, rctx: RequestContext) -> DeviceSignInView: ...

    @abstractmethod
    async def finish_device_sign_in(
        self, rctx: RequestContext, body: DeviceTokenRequest
    ) -> IssuedLoginView: ...

    @abstractmethod
    async def dev_sign_in(self, rctx: RequestContext, body: DevSignInRequest) -> IssuedLoginView:
        """Platform-internal, local and test only."""
        ...

    @abstractmethod
    async def verify_second_factor(
        self, ictx: IdentityContext, body: SecondFactorRequest
    ) -> IssuedLoginView: ...

    @abstractmethod
    async def exchange_session(
        self, ictx: IdentityContext, body: ExchangeSessionRequest
    ) -> IssuedSessionView:
        """Platform-internal: the identity carries no tenant; the body chooses one."""
        ...

    @abstractmethod
    async def get_identity_memberships(
        self, ictx: IdentityContext, cursor: str | None, limit: int
    ) -> MembershipChoicePageView:
        """Platform-internal: the identity's places, before or across tenants;
        `cursor` as on `get_users`."""
        ...

    @abstractmethod
    async def logout(self, ictx: IdentityContext, body: LogoutRequest | None) -> SignedOutView:
        """Platform-internal: ending its own sign-in takes the identity stage.
        Ends the credential presented: a session, a sign-in, or an operator
        token."""
        ...

    @abstractmethod
    async def get_me(self, ctx: TenantContext) -> MeView: ...

    @abstractmethod
    async def delete_account(
        self, ctx: TenantContext, body: DeleteAccountRequest
    ) -> AccountDeletedView: ...

    @abstractmethod
    async def update_me(self, ctx: TenantContext, body: UpdateMeRequest) -> UserView: ...

    @abstractmethod
    async def get_identity(self, ctx: TenantContext) -> IdentityView: ...

    @abstractmethod
    async def update_identity(
        self, ctx: TenantContext, body: UpdateIdentityRequest
    ) -> IdentityView: ...

    @abstractmethod
    async def get_org(self, ctx: TenantContext) -> OrgView: ...

    @abstractmethod
    async def delete_org(self, ctx: TenantContext, body: DeleteOrgRequest) -> OrgDeletedView:
        """The caller's team org, deleted by its owner; the answer carries the
        owner's session in their personal org."""
        ...

    @abstractmethod
    async def create_org(
        self, ctx: TenantContext, body: CreateTeamOrgRequest, attempt: Attempt
    ) -> MembershipChoiceView:
        """A team org the caller owns; the answer is the caller's place in it,
        the choice the exchange takes to switch there."""
        ...

    @abstractmethod
    async def get_users(self, ctx: TenantContext, cursor: str | None, limit: int) -> UserPageView:
        """One page of the tenant's members; `cursor` is the previous page's
        `next_cursor`, opaque and refused when it is not one this list issued."""
        ...

    @abstractmethod
    async def get_memberships(
        self, ctx: TenantContext, cursor: str | None, limit: int
    ) -> MembershipPageView:
        """One page of the tenant's memberships; `cursor` as on `get_users`."""
        ...

    @abstractmethod
    async def update_membership_role(
        self, ctx: TenantContext, user_id: UUID, body: UpdateMembershipRequest
    ) -> MembershipView: ...

    @abstractmethod
    async def remove_member(self, ctx: TenantContext, user_id: UUID) -> UserView: ...

    @abstractmethod
    async def get_invitations(
        self, ctx: TenantContext, cursor: str | None, limit: int
    ) -> InvitationPageView:
        """One page of the org's pending invitations; `cursor` as on `get_users`."""
        ...

    @abstractmethod
    async def invite_member(
        self, ctx: TenantContext, body: InviteMemberRequest, attempt: Attempt
    ) -> InvitationView: ...

    @abstractmethod
    async def resend_invitation(
        self, ctx: TenantContext, invitation_id: UUID
    ) -> InvitationView: ...

    @abstractmethod
    async def revoke_invitation(
        self, ctx: TenantContext, invitation_id: UUID
    ) -> InvitationView: ...

    @abstractmethod
    async def sso_link(self, ctx: TenantContext, body: SsoLinkRequest) -> SsoLinkView: ...

    @abstractmethod
    async def get_sessions(self, ctx: TenantContext, limit: int) -> list[SessionView]: ...

    @abstractmethod
    async def revoke_session(self, ctx: TenantContext, session_id: UUID) -> SessionView: ...

    @abstractmethod
    async def get_api_keys(
        self, ctx: TenantContext, cursor: str | None, limit: int
    ) -> ApiKeyPageView:
        """One page of the api key list; `cursor` as on `get_users`."""
        ...

    @abstractmethod
    async def create_api_key(
        self, ctx: TenantContext, body: AddApiKeyRequest, attempt: Attempt
    ) -> IssuedApiKeyView:
        """`attempt` carries the id the create uses, minted by the gateway
        before the idempotency marker, and the token of the marker holding it,
        which fences the re-mint a rerun makes."""
        ...

    @abstractmethod
    async def revoke_api_key(self, ctx: TenantContext, api_key_id: UUID) -> ApiKeyView: ...
