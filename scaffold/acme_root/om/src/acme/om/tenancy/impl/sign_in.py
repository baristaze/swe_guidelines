import logging
import secrets
from collections.abc import Callable
from datetime import datetime, timedelta
from uuid import UUID

from acme.integrations.exceptions import (
    DevicePending,
    DeviceSlowDown,
    ProviderRefused,
    ProviderUnavailable,
)
from acme.integrations.identity import (
    DeviceAuthorization,
    IdentityProviderInterface,
    ProvidedOrganization,
    ProvidedSignIn,
)
from acme.om.base import EMPTY_UUID, new_id, utcnow
from acme.om.context import CredentialKind, IdentityContext, RequestContext, Role
from acme.om.exceptions import (
    Conflict,
    CredentialExpired,
    EmailNotVerified,
    InvalidCredential,
    MembershipLimitReached,
    NotFound,
    SignInDelayed,
    SignInPending,
    SignInRefused,
    SignInSlowDown,
    Unavailable,
    UniqueKeyTaken,
    ValidationFailed,
)
from acme.om.outbox import OutboxRelayInterface
from acme.om.outbox.types.row import OutboxRow
from acme.om.tenancy.impl.creates import add_member_to, create_person, new_identity
from acme.om.tenancy.impl.manager import TenancyOptions
from acme.om.tenancy.impl.shared import (
    check_session,
    clamp,
    create_session,
    ended_by,
    exchange_sign_in,
    memberships_of,
    mint_token,
    principal_in,
    provider_logout_url,
    session_payload,
)
from acme.om.tenancy.impl.totp import TotpSealer
from acme.om.tenancy.rules import (
    check_email,
    email_digest,
    hash_token,
    is_platform_email,
    matching_totp_step,
    pkce_challenge,
    sign_in_delay,
    sso_joins,
)
from acme.om.tenancy.sign_in import TenancySignInManagerInterface
from acme.om.tenancy.storage import TenancyStorageInterface
from acme.om.tenancy.types.identity import Identity
from acme.om.tenancy.types.invitation import Invitation, InvitationState
from acme.om.tenancy.types.issued import (
    IssuedLogin,
    IssuedSession,
    OrgMembership,
    SignedOut,
    SignInStart,
)
from acme.om.tenancy.types.org import Org, OrgKind
from acme.om.tenancy.types.page import OrgMembershipPage
from acme.om.tenancy.types.session import Session
from acme.om.tenancy.types.user import User

log = logging.getLogger(__name__)


class TenancySignInManagerImpl(TenancySignInManagerInterface):
    def __init__(
        self,
        storage: TenancyStorageInterface,
        relay: OutboxRelayInterface,
        options: TenancyOptions,
        clock: Callable[[], datetime] = utcnow,
        *,
        identity_provider: IdentityProviderInterface,
    ) -> None:
        self._storage = storage
        self._relay = relay
        self._options = options
        self._provider = identity_provider
        self._totp = TotpSealer(options.totp_encryption_key)
        # The TOTP time step is read from this clock, so a test can step it.
        self._clock = clock

    async def sign_in_url(
        self,
        rctx: RequestContext,
        redirect_uri: str,
        state: str,
        *,
        invitation_token: str | None = None,
        sign_up: bool = False,
    ) -> SignInStart:
        if redirect_uri not in self._options.sign_in_redirect_uris:
            raise ValidationFailed("that is not this environment's sign-in callback")
        if not state:
            raise ValidationFailed("a sign-in carries the state that binds it to its tab")
        verifier = secrets.token_urlsafe(48)
        try:
            url = self._provider.authorization_url(
                redirect_uri=redirect_uri,
                state=state,
                code_challenge=pkce_challenge(verifier),
                invitation_token=invitation_token,
                screen_hint="sign-up" if sign_up else None,
            )
        except ProviderUnavailable as error:
            raise Unavailable(f"sign-in is not available: {error.message}") from None
        return SignInStart(authorization_url=url, code_verifier=verifier)

    async def sign_in_with_code(
        self,
        rctx: RequestContext,
        code: str,
        invitation_token: str | None = None,
        *,
        code_verifier: str | None = None,
    ) -> IssuedLogin:
        try:
            signed_in = await self._provider.authenticate_code(
                code,
                code_verifier=code_verifier,
                invitation_token=invitation_token,
                deadline=rctx.deadline,
            )
        except ProviderUnavailable as error:
            raise Unavailable(f"sign-in is not available: {error.message}") from None
        except ProviderRefused as error:
            raise SignInRefused(f"the sign-in was refused: {error.message}") from None
        return await self._signed_in(rctx, signed_in)

    async def start_device_sign_in(self, rctx: RequestContext) -> DeviceAuthorization:
        try:
            return await self._provider.start_device(deadline=rctx.deadline)
        except ProviderUnavailable as error:
            raise Unavailable(f"sign-in is not available: {error.message}") from None
        except ProviderRefused as error:
            raise SignInRefused(f"the sign-in was refused: {error.message}") from None

    async def finish_device_sign_in(self, rctx: RequestContext, device_code: str) -> IssuedLogin:
        try:
            signed_in = await self._provider.authenticate_device(
                device_code, deadline=rctx.deadline
            )
        except DeviceSlowDown:
            raise SignInSlowDown("asked too often; wait longer before asking again") from None
        except DevicePending:
            raise SignInPending("the sign-in is not confirmed yet") from None
        except ProviderUnavailable as error:
            raise Unavailable(f"sign-in is not available: {error.message}") from None
        except ProviderRefused as error:
            raise SignInRefused(f"the sign-in was refused: {error.message}") from None
        return await self._signed_in(rctx, signed_in)

    async def dev_sign_in(
        self, rctx: RequestContext, email: str, display_name: str = ""
    ) -> IssuedLogin:
        if not self._options.dev_sign_in:
            raise NotFound("Not Found")
        if is_platform_email(email):
            raise ValidationFailed("that address belongs to the platform")
        try:
            check_email(email)
        except ValueError as error:
            raise ValidationFailed(str(error)) from None
        identity = await self._storage.read_identity_by_email_digest(email_digest(email))
        if identity is None:
            identity = await self._made(new_identity(email, utcnow()), display_name)
        memberships = await self._with_personal(
            identity,
            await memberships_of(self._storage, identity.id, self._options.max_orgs_per_identity),
        )
        return await self._issue_login(identity, memberships)

    async def verify_second_factor(self, ictx: IdentityContext, totp_code: str) -> IssuedLogin:
        if ictx.credential_kind is not CredentialKind.LOGIN:
            raise InvalidCredential("a second factor is verified on a sign-in credential")
        identity = await self._storage.read_identity(ictx.identity_id)
        if identity is None:
            raise InvalidCredential("the identity is gone")
        digest = email_digest(identity.email)
        now = utcnow()
        # A run of wrong codes for the email makes the next one wait, counted
        # in the tenancy role's own storage, so it holds when the cache is
        # down and whatever address the guesses come from.
        run = await self._storage.read_sign_in_delay(digest)
        if run is not None:
            wait = sign_in_delay(
                run.failures,
                run.last_failed_at,
                now,
                free=self._options.sign_in_free_failures,
                base=self._options.sign_in_delay_base,
                cap=self._options.sign_in_delay_cap,
            )
            if wait > timedelta(0):
                raise SignInDelayed(wait)
        if not await self._second_factor_holds(identity, totp_code):
            await self._storage.record_failed_sign_in(digest, now)
            raise InvalidCredential("the code is wrong or was used already")
        if run is not None:
            await self._storage.clear_failed_sign_ins(digest)
        memberships = await memberships_of(
            self._storage, identity.id, self._options.max_orgs_per_identity
        )
        presented = await self._storage.read_session_by_id(ictx.credential_id)
        if presented is None:
            raise InvalidCredential("the sign-in behind the code is gone")
        _, sign_in = presented
        # The new sign-in stands for the same visit to the provider as the one
        # that presented the code, so it carries the same provider session,
        # and it ends that one in the write that lands it: a person holds one
        # sign-in at a time, as a tab holds one session (ADR 0068).
        token, verified = self._new_login(
            identity, second_factor_at=now, provider_session_id=sign_in.provider_session_id
        )
        await exchange_sign_in(
            self._storage, EMPTY_UUID, verified, ended_by(sign_in, identity.id, now)
        )
        return IssuedLogin(token=token, expires_at=verified.expires_at, memberships=memberships)

    async def _signed_in(self, rctx: RequestContext, signed_in: ProvidedSignIn) -> IssuedLogin:
        """The identity the provider vouched for, found, linked, or made, and
        the places it holds, joined first to the org the sign-in named when
        an invitation or a single sign-on puts the person there."""
        person = signed_in.user
        if not person.email_verified:
            raise EmailNotVerified("verify your email address with the sign-in provider first")
        if is_platform_email(person.email):
            raise InvalidCredential("that address belongs to the platform")
        identity = await self._identity_of(signed_in)
        memberships = await memberships_of(
            self._storage, identity.id, self._options.max_orgs_per_identity
        )
        if signed_in.organization_id is not None and await self._join_through_provider(
            rctx, identity, signed_in, memberships
        ):
            memberships = await memberships_of(
                self._storage, identity.id, self._options.max_orgs_per_identity
            )
        memberships = await self._with_personal(identity, memberships)
        return await self._issue_login(
            identity, memberships, provider_session_id=signed_in.session_id
        )

    async def _identity_of(self, signed_in: ProvidedSignIn) -> Identity:
        """By the issuer and the subject; else by the verified email, linked
        from now on; else a new person. A person who changed their address at
        the provider is found by the subject and keeps the identity they
        have."""
        issuer, person = self._provider.issuer, signed_in.user
        identity = await self._storage.read_identity_by_issuer_subject(issuer, person.id)
        if identity is not None:
            return identity
        by_email = await self._storage.read_identity_by_email_digest(email_digest(person.email))
        now = utcnow()
        if by_email is None:
            return await self._made(
                new_identity(person.email, now, issuer=issuer, subject=person.id),
                person.display_name,
            )
        # The provider verified the address this identity holds, so it is the
        # same person: an identity the seeding or the operator plane made, or
        # one an older release made with a password. It is linked once; a link
        # to another subject is replaced, since the address, verified again,
        # decides who holds it.
        linked = by_email.model_copy(
            update={
                "issuer": issuer,
                "subject": person.id,
                "updated_at": now,
                "updated_by": by_email.id,
            }
        )
        try:
            await self._storage.write_identity(linked)
        except UniqueKeyTaken:
            # A second sign-in of the same subject linked it meanwhile.
            raced = await self._storage.read_identity_by_issuer_subject(issuer, person.id)
            if raced is None:
                raise
            return raced
        return linked

    async def _made(self, identity: Identity, display_name: str) -> Identity:
        """A new person with their personal org, in one commit. Two first
        sign-ins that race for one address or subject meet the unique key,
        and the loser reads the winner's identity."""
        try:
            await create_person(self._storage, identity, display_name)
        except UniqueKeyTaken:
            raced = await self._storage.read_identity_by_email_digest(email_digest(identity.email))
            if raced is None:
                raise
            return raced
        return identity

    async def _join_through_provider(
        self,
        rctx: RequestContext,
        identity: Identity,
        signed_in: ProvidedSignIn,
        held: tuple[OrgMembership, ...],
    ) -> bool:
        """The membership a sign-in through one of the provider's organizations
        lands: the invitation the person accepted, with its role; else, for a
        sign-in through the org's single sign-on, a member's place when the
        person's address is in a domain the org verified. Anything else lands
        nothing, and the sign-in goes on: a place the person cannot have is
        not a reason to refuse them their own. True when it tried to land one.

        A person who holds a live place in the org the organization stands
        for is not asked about: each of their places carries its org's
        organization id. The provider's invitations are read for them only
        when the org holds a pending invitation to their own address, which
        their sign-in may have accepted. Anyone else is asked about in full,
        and the provider's invitations are read only while the org holds a
        pending invitation, since only a pending one is accepted."""
        organization_id = signed_in.organization_id
        assert organization_id is not None
        member_of = next((m.org for m in held if m.org.provider_org_id == organization_id), None)
        try:
            if member_of is not None:
                org, provided = member_of, None
                pending = await self._storage.read_pending_invitation(org.id, identity.email)
            else:
                provided = await self._provider.get_organization(
                    organization_id, deadline=rctx.deadline
                )
                found = await self._org_of(provided)
                if found is None:
                    return False
                org = found
                pending = next(iter(await self._storage.read_invitations(org.id, None, 1)), None)
            accepted = (
                None
                if pending is None
                else await self._provider.accepted_invitation(
                    organization_id=organization_id,
                    user_id=signed_in.user.id,
                    email=signed_in.user.email,
                    deadline=rctx.deadline,
                )
            )
        except (ProviderRefused, ProviderUnavailable) as error:
            log.warning(
                "sign-in through organization %s joined nothing: %s",
                organization_id,
                error,
            )
            return False
        invitation = (
            None
            if accepted is None
            else await self._storage.read_invitation_by_provider_id(org.id, accepted.id)
        )
        try:
            if invitation is not None and invitation.state is InvitationState.PENDING:
                await self._accept(rctx, org, identity, signed_in, invitation)
                return True
            if provided is not None and signed_in.via_sso and org.kind is OrgKind.TEAM:
                if sso_joins(identity.email, provided.verified_domains):
                    await self._join(rctx, org, identity, signed_in, Role.MEMBER, org.created_by)
                    return True
        except MembershipLimitReached as error:
            # A person over their own bound of orgs: the invitation stays
            # pending, and the sign-in goes on into the places the person has.
            log.warning("sign-in into org %s joined nothing: %s", org.id, error.message)
        return False

    async def _org_of(self, provided: ProvidedOrganization) -> Org | None:
        """The living team or personal org the provider's organization stands
        for: the one its external id names, which names it back."""
        try:
            org_id = UUID(provided.external_id or "")
        except ValueError:
            return None
        org = await self._storage.read_org(org_id)
        if org is None or org.deleted_at is not None or org.provider_org_id != provided.id:
            return None
        return org

    async def _accept(
        self,
        rctx: RequestContext,
        org: Org,
        identity: Identity,
        signed_in: ProvidedSignIn,
        invitation: Invitation,
    ) -> None:
        """The invitation's membership, with its role and recorded as its
        inviter's, and the invitation accepted, in one commit. A person who
        is a member already keeps their place and the invitation closes."""
        now = utcnow()
        user_id = new_id()
        accepted = invitation.model_copy(
            update={
                "state": InvitationState.ACCEPTED,
                "accepted_user_id": user_id,
                "updated_at": now,
                "updated_by": invitation.created_by,
            }
        )
        user, created = await self._join(
            rctx,
            org,
            identity,
            signed_in,
            invitation.role,
            invitation.created_by,
            accepted,
            user_id,
        )
        if not created:
            await self._storage.write_invitation(
                org.id, accepted.model_copy(update={"accepted_user_id": user.id})
            )

    async def _join(
        self,
        rctx: RequestContext,
        org: Org,
        identity: Identity,
        signed_in: ProvidedSignIn,
        role: Role,
        actor_id: UUID,
        invitation: Invitation | None = None,
        user_id: UUID | None = None,
    ) -> tuple[User, bool]:
        shown = signed_in.user.display_name or identity.email.partition("@")[0]
        return await add_member_to(
            self._storage,
            self._relay,
            org_id=org.id,
            user_id=user_id or new_id(),
            email=identity.email,
            display_name=shown,
            role=role,
            actor_id=actor_id,
            request=rctx,
            max_orgs=self._options.max_orgs_per_identity,
            invitation=invitation,
        )

    async def _with_personal(
        self, identity: Identity, memberships: tuple[OrgMembership, ...]
    ) -> tuple[OrgMembership, ...]:
        """The places a sign-in answers with, the personal org among them. A
        person the release before this one made has none yet, whether the
        backfill ran before it or not, so the sign-in makes it: every person
        who signs in has a place to work. Two sign-ins that race to make it
        meet the unique key, and the loser reads the winner's."""
        if any(m.org.personal for m in memberships):
            return memberships
        if len(memberships) >= self._options.max_orgs_per_identity:
            # A place past the bound would make every read of this person's
            # places refuse; a person at the bound has places to work.
            return memberships
        name = memberships[0].user.display_name if memberships else ""
        try:
            await create_person(self._storage, identity, name, new=False)
        except UniqueKeyTaken:
            pass
        return await memberships_of(self._storage, identity.id, self._options.max_orgs_per_identity)

    async def _second_factor_holds(self, identity: Identity, code: str) -> bool:
        """The code matches the identity's enrolled secret in the window around
        now, and its step was not used before: the step is recorded in one
        conditional write, so of two sign-ins presenting one code, one passes."""
        if not identity.totp_enrolled or identity.totp_secret is None:
            raise ValidationFailed("no second factor is enrolled for this sign-in")
        secret = self._totp.open(identity.id, identity.totp_secret)
        step = matching_totp_step(secret, code, self._clock())
        if step is None:
            return False
        return await self._storage.use_totp_step(identity.id, step)

    async def _issue_login(
        self,
        identity: Identity,
        memberships: tuple[OrgMembership, ...],
        *,
        provider_session_id: str | None = None,
    ) -> IssuedLogin:
        """The credential that carries no tenant, stored under the system scope."""
        token, session = self._new_login(identity, provider_session_id=provider_session_id)
        await create_session(self._storage, EMPTY_UUID, session)
        return IssuedLogin(token=token, expires_at=session.expires_at, memberships=memberships)

    def _new_login(
        self,
        identity: Identity,
        *,
        second_factor_at: datetime | None = None,
        provider_session_id: str | None = None,
    ) -> tuple[str, Session]:
        """A sign-in credential and the row that keeps it, as its digest."""
        now = utcnow()
        token = mint_token(CredentialKind.LOGIN)
        session = Session(
            id=new_id(),
            created_at=now,
            updated_at=now,
            created_by=identity.id,
            updated_by=identity.id,
            identity_id=identity.id,
            token_hash=hash_token(token),
            credential_kind=CredentialKind.LOGIN,
            expires_at=now + self._options.login_ttl,
            second_factor_at=second_factor_at,
            provider_session_id=provider_session_id,
        )
        return token, session

    async def exchange_login(self, ictx: IdentityContext, org_id: UUID) -> IssuedSession:
        self._refuse_operator_token(ictx)
        org, user, membership = await principal_in(
            self._storage, org_id, ictx.identity_id, self._options.max_orgs_per_identity
        )
        # The credential presented: the sign-in, or the session a switch ends.
        # The new session carries the provider's session it came from.
        presented = await self._storage.read_session_by_id(ictx.credential_id)
        if presented is None:
            raise InvalidCredential("the credential behind the exchange is gone")
        now = utcnow()
        token = mint_token(CredentialKind.SESSION_TOKEN)
        session = Session(
            id=new_id(),
            created_at=now,
            updated_at=now,
            created_by=user.id,
            updated_by=user.id,
            identity_id=ictx.identity_id,
            user_id=user.id,
            token_hash=hash_token(token),
            credential_kind=CredentialKind.SESSION_TOKEN,
            expires_at=now + self._options.session_ttl,
            provider_session_id=presented[1].provider_session_id,
        )
        if ictx.credential_kind is CredentialKind.SESSION_TOKEN:
            await self._switch(ictx, presented, org_id, session, now)
        else:
            await self._exchange_sign_in(ictx, presented, org_id, session, now)
        return IssuedSession(
            token=token, expires_at=session.expires_at, org=org, user=user, role=membership.role
        )

    async def _exchange_sign_in(
        self,
        ictx: IdentityContext,
        found: tuple[UUID, Session],
        org_id: UUID,
        session: Session,
        now: datetime,
    ) -> None:
        """The exchange of a sign-in: it ends in the write that lands the
        session, so one sign-in makes one session. A second exchange of it,
        a replay or a retry, is refused, and the person signs in again. A
        sign-in has no socket and no tenant, so nothing is announced."""
        _, presented = found
        check_session(presented, CredentialKind.LOGIN, self._options.session_idle_ttl)
        await exchange_sign_in(
            self._storage, org_id, session, ended_by(presented, ictx.identity_id, now)
        )

    async def _switch(
        self,
        ictx: IdentityContext,
        found: tuple[UUID, Session],
        org_id: UUID,
        session: Session,
        now: datetime,
    ) -> None:
        """The exchange of a session for another: the one presented ends in the
        write that lands the new one, and its revocation is announced under the
        tenant it belonged to, by its own user, so its socket closes."""
        ended_org_id, presented = found
        check_session(presented, CredentialKind.SESSION_TOKEN, self._options.session_idle_ttl)
        ended = ended_by(presented, presented.user_id, now)
        row = self._revocation_row(ictx, ended_org_id, ended, now)
        try:
            await self._storage.replace_session(org_id, session, ended_org_id, ended, (row,))
        except NotFound:
            raise InvalidCredential("the session behind the switch is gone") from None
        except UniqueKeyTaken:
            raise  # a key of the new session, not the one presented
        except Conflict:
            raise CredentialExpired("session revoked") from None
        await self._relay.relay(ended_org_id, row)

    async def get_identity_memberships(
        self, ictx: IdentityContext, after: UUID | None, limit: int
    ) -> OrgMembershipPage:
        self._refuse_operator_token(ictx)
        limit = clamp(limit, self._options.max_limit)
        rows = await self._storage.read_memberships_by_identity(ictx.identity_id, limit + 1, after)
        return OrgMembershipPage(items=tuple(rows[:limit]), has_more=len(rows) > limit)

    async def logout(self, ictx: IdentityContext, return_to: str | None = None) -> SignedOut:
        if return_to is not None and return_to not in self._options.sign_out_return_uris:
            raise ValidationFailed("that is not this environment's sign-out return")
        found = await self._storage.read_session_by_id(ictx.credential_id)
        if found is None:
            raise InvalidCredential("the credential behind the sign-out is gone")
        org_id, presented = found
        now = utcnow()
        if ictx.credential_kind is CredentialKind.SESSION_TOKEN:
            # Announced like any revocation, under the tenant it belonged to,
            # by its own user: the socket it opened, in whichever process
            # holds it, closes on the row the relay publishes.
            ended = ended_by(presented, presented.user_id, now)
            row = self._revocation_row(ictx, org_id, ended, now)
            await self._storage.write_session(org_id, ended, (row,))
            await self._relay.relay(org_id, row)
        else:
            # A sign-in or an operator token: a row of the system scope, which
            # opens no socket and belongs to no tenant, so nothing is announced.
            ended = ended_by(presented, ictx.identity_id, now)
            await self._storage.write_session(EMPTY_UUID, ended)
            log.info(
                "identity %s signed out its %s %s",
                ictx.identity_id,
                ictx.credential_kind.value,
                ended.id,
            )
        return SignedOut(
            session=ended, provider_logout_url=provider_logout_url(self._provider, ended, return_to)
        )

    def _revocation_row(
        self, rctx: RequestContext, org_id: UUID, ended: Session, now: datetime
    ) -> OutboxRow:
        """The row that announces a session's end under its tenant, by the
        session's own user, when the stage that ends it is not a tenant's."""
        return OutboxRow(
            id=new_id(),
            created_at=now,
            org_id=org_id,
            kind="tenancy.session.revoked",
            target_id=ended.id,
            payload=session_payload(ended),
            actor_id=ended.user_id,
            request_id=rctx.request_id,
            traceparent=rctx.traceparent,
            app=rctx.app.type.value,
        )

    @staticmethod
    def _refuse_operator_token(ictx: IdentityContext) -> None:
        """An operator token reaches the operator plane and nothing else: it
        never lists a person's places or enters a tenant."""
        if ictx.credential_kind is CredentialKind.OPERATOR_TOKEN:
            raise InvalidCredential("an operator token reaches the operator plane only")
