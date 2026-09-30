"""The context model: four stages, each produced only by a transition (an
operation of the tenancy manager, or one that asks it, as the worker's claim
does), and five scopes every stage satisfies structurally."""

import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

import acme.om
from acme.infra.cache import CacheScope
from acme.infra.impl.local import InfraLocalImpl
from acme.integrations.identity.absent import IdentityProviderAbsentImpl
from acme.om.base import EMPTY_UUID, new_id
from acme.om.context import (
    ActorScope,
    AppContext,
    AppType,
    CredentialKind,
    CredentialScope,
    IdentityContext,
    OperatorContext,
    OperatorPermission,
    OperatorRole,
    ProvenanceScope,
    RequestContext,
    RequestScope,
    Role,
    TenantContext,
    TenantScope,
    build_context,
)
from acme.om.events.storage.impl.memory import EventStorageMemoryImpl
from acme.om.exceptions import InvalidCredential, NotAnOperator, NotAuthorized
from acme.om.outbox.impl.relay import OutboxRelayImpl
from acme.om.outbox.storage.impl.memory import OutboxStorageMemoryImpl
from acme.om.root import build_tenancy
from acme.om.tenancy import TenancyManagerInterface
from acme.om.tenancy.impl.manager import TenancyOptions
from acme.om.tenancy.rules import operator_permissions_of, permissions_of
from acme.om.tenancy.storage.impl.memory import TenancyStorageMemoryImpl

APP = AppContext(type=AppType.PORTAL, version="portal@test")


TRACEPARENT = f"00-{'0' * 31}1-{'0' * 15}2-01"


def request() -> RequestContext:
    return RequestContext(
        request_id=new_id(), app=APP, trace_id="0" * 31 + "1", traceparent=TRACEPARENT
    )


@pytest.fixture
def manager(tmp_path: Path) -> TenancyManagerInterface:
    outbox = OutboxStorageMemoryImpl()
    infra = InfraLocalImpl(tmp_path)
    relay = OutboxRelayImpl(outbox, EventStorageMemoryImpl(), infra.get_topics())
    return build_tenancy(
        TenancyStorageMemoryImpl(outbox),
        relay,
        infra.get_cache(CacheScope.REALTIME_TICKET),
        TenancyOptions(dev_sign_in=True),
        identity_provider=IdentityProviderAbsentImpl(),
    )


# The stages are refinements: a subclass is accepted where its base is asked for.


def test_every_stage_is_a_request_context_and_only_admin_is_an_identity() -> None:
    assert issubclass(IdentityContext, RequestContext)
    assert issubclass(TenantContext, RequestContext)
    assert issubclass(OperatorContext, IdentityContext)
    assert not issubclass(TenantContext, IdentityContext)
    assert not issubclass(IdentityContext, TenantContext)
    assert not issubclass(OperatorContext, TenantContext)


def test_a_stage_is_immutable() -> None:
    rctx = request()
    with pytest.raises(ValidationError, match="frozen"):
        rctx.request_id = new_id()  # pyright: ignore[reportAttributeAccessIssue]


# The transitions: each produces its stage and refuses what it must.


async def test_login_then_authenticate_login_produces_the_identity_stage(
    manager: TenancyManagerInterface,
) -> None:
    await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    rctx = request()
    login = await manager.sign_in.dev_sign_in(rctx, "ann@example.test")
    ictx = await manager.authenticate_login(rctx, login.token)
    assert type(ictx) is IdentityContext
    assert ictx.email == "ann@example.test"
    assert ictx.credential_kind is CredentialKind.LOGIN
    assert ictx.credential_id != EMPTY_UUID
    # The identity stage refines the request stage it was minted from.
    assert (ictx.request_id, ictx.app, ictx.trace_id, ictx.traceparent) == (
        rctx.request_id,
        rctx.app,
        rctx.trace_id,
        rctx.traceparent,
    )


async def test_authenticate_login_takes_a_session_token_and_refuses_an_api_key(
    manager: TenancyManagerInterface,
) -> None:
    _, org = await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    login = await manager.sign_in.dev_sign_in(request(), "ann@example.test")
    ictx = await manager.authenticate_login(request(), login.token)
    session = await manager.sign_in.exchange_login(ictx, org.id)
    # A live session proves the identity of its user as well as the tenant.
    by_session = await manager.authenticate_login(request(), session.token)
    assert by_session.identity_id == ictx.identity_id
    assert by_session.credential_kind is CredentialKind.SESSION_TOKEN
    ctx = await manager.authenticate(request(), session.token)
    key = await manager.credentials.create_api_key(ctx, "ci", Role.MEMBER)
    with pytest.raises(InvalidCredential):
        await manager.authenticate_login(request(), key.key)
    with pytest.raises(InvalidCredential):
        await manager.authenticate_login(request(), "lgn_never-issued")


async def test_exchange_login_produces_a_session_and_refuses_a_non_member(
    manager: TenancyManagerInterface,
) -> None:
    _, ajax = await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    _, beta = await manager.bootstrap(request(), "Beta", "beta", "bob@example.test", "Bob")
    login = await manager.sign_in.dev_sign_in(request(), "ann@example.test")
    ictx = await manager.authenticate_login(request(), login.token)
    issued = await manager.sign_in.exchange_login(ictx, ajax.id)
    assert issued.org.id == ajax.id and issued.role is Role.OWNER
    with pytest.raises(NotAuthorized):
        await manager.sign_in.exchange_login(ictx, beta.id)


async def test_authenticate_produces_the_tenant_stage_and_refuses_a_login_token(
    manager: TenancyManagerInterface,
) -> None:
    _, org = await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    login = await manager.sign_in.dev_sign_in(request(), "ann@example.test")
    ictx = await manager.authenticate_login(request(), login.token)
    issued = await manager.sign_in.exchange_login(ictx, org.id)
    rctx = request()
    ctx = await manager.authenticate(rctx, issued.token)
    assert type(ctx) is TenantContext
    assert ctx.org_id == org.id and ctx.security.role is Role.OWNER
    assert ctx.credential_kind is CredentialKind.SESSION_TOKEN
    assert ctx.credential_id != EMPTY_UUID
    assert (ctx.request_id, ctx.app, ctx.trace_id, ctx.traceparent) == (
        rctx.request_id,
        rctx.app,
        rctx.trace_id,
        rctx.traceparent,
    )
    with pytest.raises(InvalidCredential):
        await manager.authenticate(request(), login.token)


async def test_admit_operator_produces_the_operator_stage_for_operators_only(
    manager: TenancyManagerInterface,
) -> None:
    await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    await manager.bootstrap(
        request(),
        "Ops",
        "ops",
        "root@example.test",
        "Root",
        operator_role=OperatorRole.WRITE,
    )
    login = await manager.sign_in.dev_sign_in(request(), "ann@example.test")
    with pytest.raises(NotAnOperator):
        await manager.admit_operator(await manager.authenticate_login(request(), login.token))

    # Admitted on the operator token the grant job mints; the sign-in with a
    # second factor is the tenancy manager's tests' subject.
    root = await manager.grant_operator_token(request(), "root@example.test")
    ictx = await manager.authenticate_login(request(), root.token)
    admin = await manager.admit_operator(ictx)
    assert type(admin) is OperatorContext
    assert isinstance(admin, IdentityContext)
    assert (admin.identity_id, admin.email, admin.credential_id) == (
        ictx.identity_id,
        ictx.email,
        ictx.credential_id,
    )
    assert (admin.request_id, admin.app, admin.trace_id, admin.traceparent) == (
        ictx.request_id,
        ictx.app,
        ictx.trace_id,
        ictx.traceparent,
    )
    assert not hasattr(admin, "org_id")
    # The entry's role travels on the stage as permissions, as a membership's does.
    assert admin.permissions == operator_permissions_of(OperatorRole.WRITE)
    assert admin.has(OperatorPermission.READ) and admin.has(OperatorPermission.WRITE)


async def test_a_read_operator_is_admitted_with_read_and_refused_a_write(
    manager: TenancyManagerInterface,
) -> None:
    """The allowlist entry says what the operator may do: `read` admits the
    person and `require(WRITE)` refuses them, in the shape a viewer's `require`
    refuses a tenant write. Seeding the same person again with a wider role
    widens the entry; seeding with a narrower one leaves it."""
    await manager.bootstrap(
        request(),
        "Ops",
        "ops",
        "sup@example.test",
        "Sup",
        operator_role=OperatorRole.READ,
    )
    admin = await manager.admit_operator(
        await manager.authenticate_login(
            request(), (await manager.grant_operator_token(request(), "sup@example.test")).token
        )
    )
    assert admin.permissions == frozenset({OperatorPermission.READ})
    admin.require(OperatorPermission.READ)
    with pytest.raises(NotAuthorized, match="operator lacks write"):
        admin.require(OperatorPermission.WRITE)

    await manager.bootstrap(
        request(),
        "More",
        "more",
        "sup@example.test",
        "Sup",
        operator_role=OperatorRole.WRITE,
    )
    token = await manager.grant_operator_token(request(), "sup@example.test")
    widened = await manager.admit_operator(await manager.authenticate_login(request(), token.token))
    assert widened.permissions == operator_permissions_of(OperatorRole.WRITE)
    await manager.bootstrap(
        request(),
        "Less",
        "less",
        "sup@example.test",
        "Sup",
        operator_role=OperatorRole.READ,
    )
    token = await manager.grant_operator_token(request(), "sup@example.test")
    kept = await manager.admit_operator(await manager.authenticate_login(request(), token.token))
    assert kept.permissions == operator_permissions_of(OperatorRole.WRITE)


async def test_service_and_socket_contexts_refine_the_request_they_are_given(
    manager: TenancyManagerInterface,
) -> None:
    owner, org = await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    rctx = request()
    service = await manager.service_context(rctx, org.id, owner.user_id)
    assert service.security.role is Role.SERVICE and service.request_id == rctx.request_id
    assert {c.request_id for c in await manager.service_contexts(rctx)} == {rctx.request_id}

    login = await manager.sign_in.dev_sign_in(request(), "ann@example.test")
    ictx = await manager.authenticate_login(request(), login.token)
    session = await manager.sign_in.exchange_login(ictx, org.id)
    ctx = await manager.authenticate(request(), session.token)
    ticket = await manager.issue_ticket(ctx)
    socket_request = request()
    socket_ctx = (await manager.redeem_ticket(socket_request, ticket.ticket)).ctx
    assert socket_ctx.credential_kind is CredentialKind.SOCKET_TICKET
    assert socket_ctx.credential_id == ctx.credential_id
    assert socket_ctx.request_id == socket_request.request_id


def test_build_context_copies_the_request_stage_and_reads_security() -> None:
    rctx = request()
    ctx = build_context(
        rctx,
        user_id=new_id(),
        org_id=new_id(),
        role=Role.MEMBER,
        permissions=permissions_of(Role.MEMBER),
        credential_kind=CredentialKind.API_KEY,
        credential_id=new_id(),
    )
    assert (ctx.request_id, ctx.app, ctx.trace_id, ctx.traceparent) == (
        rctx.request_id,
        rctx.app,
        rctx.trace_id,
        rctx.traceparent,
    )
    assert ctx.credential_kind is ctx.security.credential_kind
    assert ctx.credential_id == ctx.security.credential_id
    assert ctx.org_id == ctx.security.org_id and ctx.user_id == ctx.security.user_id
    assert ctx.role is ctx.security.role


def test_a_request_at_the_edge_names_no_cause_and_a_handoff_carries_one() -> None:
    """The two ids are separate fields: the stage a handoff mints has a request
    id of its own and names the request that caused the work beside it, and
    `build_context` carries the cause the way it carries the request."""
    edge = request()
    assert edge.caused_by_request_id is None
    causing = new_id()
    handoff = RequestContext(request_id=new_id(), app=APP, caused_by_request_id=causing)
    assert handoff.request_id != causing and handoff.caused_by_request_id == causing
    ctx = build_context(
        handoff,
        user_id=new_id(),
        org_id=new_id(),
        role=Role.SERVICE,
        permissions=permissions_of(Role.SERVICE),
        credential_kind=CredentialKind.INTERNAL,
    )
    assert (ctx.request_id, ctx.caused_by_request_id) == (handoff.request_id, causing)


# The scopes: typed assignments pyright proves, and the runtime reads them.


def test_every_stage_satisfies_the_scopes_it_carries() -> None:
    rctx = request()
    ictx = IdentityContext(
        request_id=rctx.request_id,
        app=rctx.app,
        identity_id=new_id(),
        email="ann@example.test",
        credential_kind=CredentialKind.LOGIN,
        credential_id=new_id(),
    )
    admin = OperatorContext(
        request_id=rctx.request_id,
        app=rctx.app,
        identity_id=ictx.identity_id,
        email=ictx.email,
        credential_kind=ictx.credential_kind,
        credential_id=ictx.credential_id,
        permissions=operator_permissions_of(OperatorRole.READ),
    )
    ctx = build_context(
        rctx,
        user_id=new_id(),
        org_id=new_id(),
        role=Role.MEMBER,
        permissions=permissions_of(Role.MEMBER),
        credential_kind=CredentialKind.SESSION_TOKEN,
        credential_id=new_id(),
    )

    # Every stage is a RequestScope.
    request_scopes: list[RequestScope] = [rctx, ictx, ctx, admin]
    assert [s.request_id for s in request_scopes] == [rctx.request_id] * 4
    assert all(s.app == APP for s in request_scopes)

    # The identity and operator stages carry a credential.
    credential_scopes: list[CredentialScope] = [ictx, admin, ctx]
    assert [s.credential_kind for s in credential_scopes] == [
        CredentialKind.LOGIN,
        CredentialKind.LOGIN,
        CredentialKind.SESSION_TOKEN,
    ]
    assert credential_scopes[0].credential_id == ictx.credential_id

    # The tenant stage satisfies all five.
    tenant: TenantScope = ctx
    actor: ActorScope = ctx
    provenance: ProvenanceScope = ctx
    assert tenant.org_id == ctx.security.org_id
    assert actor.user_id == ctx.security.user_id
    assert (provenance.user_id, provenance.org_id, provenance.request_id, provenance.app) == (
        ctx.user_id,
        ctx.org_id,
        ctx.request_id,
        ctx.app,
    )


def test_the_object_model_reads_the_trace_context_off_the_stage_and_never_the_tracer() -> None:
    """The traceparent rides the context: it is read off the tracer once,
    where a stage is minted at the edge, and every row the object model lands
    takes it from the stage it was handed. A static scan of `acme.om`."""
    root = Path(acme.om.__file__).parent
    readers = sorted(
        str(path.relative_to(root))
        for path in root.rglob("*.py")
        for node in ast.walk(ast.parse(path.read_text()))
        if isinstance(node, ast.ImportFrom)
        and node.module == "acme.infra.observability"
        and any(alias.name in {"current_traceparent", "traceparent_of"} for alias in node.names)
    )
    assert readers == []
