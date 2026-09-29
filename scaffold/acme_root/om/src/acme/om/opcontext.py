"""The context an operation takes first, in two orthogonal ideas.

Stages are concrete frozen types, one per amount of evidence a request has
gathered: `RequestContext` (a request exists), `IdentityContext` (a person
is verified), `OpContext` (a membership is established), `OperatorContext`
(an operator is admitted). A subclass is a refinement, so every stage is
accepted where a weaker one is asked for. A stage above the request stage
is produced only by a transition, an operation of the tenancy manager or
one that asks it, as the worker's claim does, and nowhere else; a function
that takes a stage relies on its invariant instead of re-checking it.

Scopes are structural views (`Protocol`) over what a stage carries:
`RequestScope`, `TenantScope`, `ActorScope`, `CredentialScope`, and the one
named composition, `ProvenanceScope`. A function that reads only a few
fields declares the scope it reads, and its callers keep passing the stage
they hold.

Roles, permissions, credential kinds, and app types are declared here, so
this module imports nothing above `base.py` and the tenancy namespace reads
them rather than the other way round."""

from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from acme.om.base import EMPTY_UUID, Platform
from acme.om.exceptions import NotAuthorized

__all__ = [
    "ActorScope",
    "AppContext",
    "AppType",
    "CredentialKind",
    "CredentialScope",
    "IdentityContext",
    "OpContext",
    "OperatorContext",
    "OperatorPermission",
    "OperatorRole",
    "Permission",
    "ProvenanceScope",
    "RequestContext",
    "RequestScope",
    "Role",
    "SecurityContext",
    "TenantScope",
    "build_context",
]


class Role(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"
    VIEWER = "viewer"
    SERVICE = "service"  # a worker: on a person's earlier request, or a sweep of a tenant


class Permission(StrEnum):
    READ = "read"
    WRITE = "write"
    MANAGE_MEMBERS = "manage_members"
    MANAGE_KEYS = "manage_keys"
    MANAGE_BILLING = "manage_billing"


class OperatorRole(StrEnum):
    """What an allowlist entry lets an operator do across every tenant. Write
    includes read; the tenancy namespace's table says which permissions each
    role holds, as it does for `Role`."""

    READ = "read"
    WRITE = "write"


class OperatorPermission(StrEnum):
    READ = "read"
    WRITE = "write"
    ENROL = "enrol"
    """What an allowlisted identity holds before its second factor is
    enrolled: the two enrolment calls and nothing else."""
    MINT = "mint"
    """What a sign-in that verified a second factor holds: the mint of one
    operator token, which ends the sign-in, and nothing else. Every read and
    every write on the plane is a token's, so each can be ended by itself."""


class CredentialKind(StrEnum):
    API_KEY = "api_key"
    SESSION_TOKEN = "session_token"
    LOGIN = "login"
    SOCKET_TICKET = "socket_ticket"
    OPERATOR_TOKEN = "operator_token"
    INTERNAL = "internal"


class AppType(StrEnum):
    PORTAL = "portal"
    ADMIN = "admin"
    CLI = "cli"
    API = "api"
    WORKER = "worker"
    SLACK = "slack"  # a call from Slack: a command, an event, an install


class SecurityContext(Platform):
    """Ids and facts, never entities: a manager that needs the user or the org
    loads it, so a role change is seen on the next request."""

    user_id: UUID
    org_id: UUID
    role: Role
    permissions: tuple[Permission, ...]
    teams: tuple[UUID, ...] = ()
    credential_kind: CredentialKind
    credential_id: UUID = EMPTY_UUID  # the session or key; EMPTY_UUID for internal contexts


class AppContext(Platform):
    type: AppType
    version: str  # e.g. "portal@0.7.0"


# Stages: refinement by evidence.


class RequestContext(Platform):
    """The weakest stage: a request exists, nobody is known yet. Minted once
    at the edge (the gateway, the worker loop, an ops command)."""

    request_id: UUID
    app: AppContext
    trace_id: str | None = None
    # The trace context of this request as the W3C `traceparent` header, the
    # form a handoff carries on: an id names a trace, and only the header
    # carries what a later span links to. Read off the tracer once, where the
    # stage is minted; every row a write lands takes it from here. None where
    # no tracer is configured or no span is open.
    traceparent: str | None = None
    # Ambient state one hop back: empty on a request that arrived at the edge,
    # and on a stage minted for a handoff the request that caused the work,
    # read off the work item. Two fields, never one written over the other: a
    # reader asks what happened in this run and what asked for it.
    caused_by_request_id: UUID | None = None
    # The instant this request's time runs out, which the gateway stamps when
    # it admits the request (ADR 0069). Every call it makes to a provider or
    # to AWS is handed it, so the calls share what is left. None where no
    # request waits: a worker's stage, bounded by its item's lease, a socket,
    # an ops command.
    deadline: datetime | None = None


class IdentityContext(RequestContext):
    """A person is verified by their own sign-in. No tenant chosen, on purpose:
    the credential behind it carries none."""

    identity_id: UUID
    email: str
    credential_kind: CredentialKind
    credential_id: UUID
    # What the credential behind the stage records, for the operator gate: a
    # sign-in that verified a TOTP code, and the one permission an operator
    # token carries. Both are read off the stored credential by the
    # transition, never taken from the request.
    second_factor: bool = False
    operator_role: OperatorRole | None = None
    # What the identity behind the credential records, read with it in the
    # same statement: its allowlist entry and whether a second factor is
    # enrolled. The operator gate decides on these, so it reads nothing.
    operator_entry: OperatorRole | None = None
    second_factor_enrolled: bool = False


class OpContext(RequestContext):
    """A membership is established: the person is a user of one tenant with a
    role. What a tenant operation knows about the person is the user inside
    the tenant, not the identity across tenants, so this is not an
    `IdentityContext`."""

    security: SecurityContext

    @property
    def org_id(self) -> UUID:
        return self.security.org_id

    @property
    def user_id(self) -> UUID:
        return self.security.user_id

    @property
    def role(self) -> Role:
        return self.security.role

    @property
    def credential_kind(self) -> CredentialKind:
        return self.security.credential_kind

    @property
    def credential_id(self) -> UUID:
        return self.security.credential_id

    def has(self, permission: Permission) -> bool:
        return permission in self.security.permissions

    def require(self, permission: Permission) -> None:
        if not self.has(permission):
            raise NotAuthorized(f"{self.security.role.value} lacks {permission.value}")

    def in_team(self, team_id: UUID) -> bool:
        return team_id in self.security.teams


class OperatorContext(IdentityContext):
    """The operator plane: an identity on the operator allowlist. No org_id,
    on purpose. Its one field of its own is what the allowlist entry grants,
    so a read operator is refused a write the way a viewer is; the type is the
    evidence of admission, and only the tenancy manager's `admit_operator`
    constructs it."""

    permissions: frozenset[OperatorPermission]

    def has(self, permission: OperatorPermission) -> bool:
        return permission in self.permissions

    def require(self, permission: OperatorPermission) -> None:
        if not self.has(permission):
            raise NotAuthorized(f"operator lacks {permission.value}")


# Scopes: composable capability views. Every member is a read-only property,
# so a frozen field and a property both satisfy it.


class RequestScope(Protocol):
    @property
    def request_id(self) -> UUID: ...

    @property
    def app(self) -> AppContext: ...

    @property
    def traceparent(self) -> str | None: ...


class TenantScope(Protocol):
    @property
    def org_id(self) -> UUID: ...


class ActorScope(TenantScope, Protocol):
    """There is no actor without a tenant."""

    @property
    def user_id(self) -> UUID: ...


class CredentialScope(Protocol):
    @property
    def credential_kind(self) -> CredentialKind: ...

    @property
    def credential_id(self) -> UUID: ...


class ProvenanceScope(ActorScope, RequestScope, Protocol):
    """Who, under which request, from which app: the provenance a write
    records. A named composition because provenance is a domain concept."""


def build_context(
    rctx: RequestContext,
    *,
    user_id: UUID,
    org_id: UUID,
    role: Role,
    permissions: tuple[Permission, ...],
    credential_kind: CredentialKind,
    teams: tuple[UUID, ...] = (),
    credential_id: UUID = EMPTY_UUID,
) -> OpContext:
    """The one place a tenant context is assembled from its parts: the request
    stage it refines and the security facts. The permissions come from the
    tenancy namespace's role table, which is a pure rule this module does not
    import."""
    return OpContext(
        request_id=rctx.request_id,
        app=rctx.app,
        trace_id=rctx.trace_id,
        traceparent=rctx.traceparent,
        caused_by_request_id=rctx.caused_by_request_id,
        deadline=rctx.deadline,
        security=SecurityContext(
            user_id=user_id,
            org_id=org_id,
            role=role,
            permissions=permissions,
            teams=teams,
            credential_kind=credential_kind,
            credential_id=credential_id,
        ),
    )
