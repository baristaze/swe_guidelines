"""Every exception raised inside the platform is rooted here. The root
carries the status and the stable code a boundary needs to present it."""

from datetime import timedelta


class PlatformException(Exception):
    """Root of every exception raised inside the platform."""

    http_status: int = 500
    code: str = "platform_error"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.code)

    @property
    def message(self) -> str:
        return str(self.args[0]) if self.args else self.code


class NotFound(PlatformException):
    http_status = 404
    code = "not_found"


class Conflict(PlatformException):
    http_status = 409
    code = "conflict"


class UniqueKeyTaken(Conflict):
    """A unique key the upsert's read did not see was taken by the time it wrote:
    a key race, reported as a Conflict and never as a driver error."""

    code = "unique_key_taken"


class MembershipLimitReached(Conflict):
    """One identity is a member of as many orgs as a person may join. A create
    that would add one more is refused, and a read that finds more than the
    bound (two adds that raced) is refused rather than cut short."""

    code = "membership_limit_reached"


class PreconditionFailed(PlatformException):
    """The caller's expected version no longer matches: a compare-and-set
    found the row at another version, or gone. Another writer landed between
    the caller's read and this write, so the caller's snapshot is stale and
    must be read again."""

    http_status = 412
    code = "precondition_failed"


class ValidationFailed(PlatformException):
    http_status = 422
    code = "validation_failed"


class NotAuthorized(PlatformException):
    http_status = 403
    code = "not_authorized"


class NotAuthenticated(PlatformException):
    http_status = 401
    code = "not_authenticated"


class Unavailable(PlatformException):
    """Not right now: a backend that is down, a breaker that is open, a
    request refused past this process's admission bound. The caller reads the
    code and comes back rather than reading a failure of its own request."""

    http_status = 503
    code = "unavailable"


class StorageException(PlatformException): ...


class TenantMismatch(StorageException, Conflict):
    """A write named a row that belongs to another tenant."""


class CrossRoleStatement(StorageException):
    """A statement touched tables of more than one database role."""

    code = "cross_role_statement"


class RowDeleted(StorageException, Conflict):
    """A write would have brought a soft-deleted row back. Every update is a
    read, a copy, and a write of the whole entity, so a delete that commits in
    between would otherwise be undone; there is no restore in this domain, and
    the caller reads the row again."""

    code = "row_deleted"


class TenancyException(PlatformException): ...


class InvalidCredential(TenancyException, NotAuthenticated):
    """The credential is unknown, malformed, or of a kind this route does not accept."""


class CredentialExpired(TenancyException, NotAuthenticated):
    """The credential was valid once and is not any more."""


class SignInDelayed(TenancyException):
    """A run of failed second-factor codes for this identity: the next attempt
    is not checked before `retry_after` has passed."""

    http_status = 429
    code = "sign_in_delayed"

    def __init__(self, retry_after: timedelta) -> None:
        super().__init__("too many failed sign-ins; try again later")
        self.retry_after = retry_after


class NotAnOperator(TenancyException, NotAuthorized):
    """The identity is not on the operator allowlist."""


class PersonalOrgFixed(TenancyException, Conflict):
    """A personal org stays its person's: it is deleted only with its person,
    and its person is not removed from it and does not change role in it, so
    it never changes hands."""

    code = "personal_org_fixed"


class LastOwner(TenancyException, Conflict):
    """An account is not deleted while its person is the last owner of a team
    org: the org would be left with nobody to run it. The refusal names each
    such org, by id, name, and slug, so the person hands it on first."""

    code = "last_owner"

    def __init__(self, orgs: tuple[tuple[str, str, str], ...]) -> None:
        names = ", ".join(name for _, name, _ in orgs)
        super().__init__(f"you are the last owner of {names}; make someone else an owner first")
        self.orgs = orgs


class OperatorRoleHeld(TenancyException, NotAuthorized):
    """An account on the operator allowlist is not deleted: the operator role
    is taken off first, by the grant job, so the platform never loses an
    operator by a click."""

    code = "operator_role_held"


class SecondFactorRequired(TenancyException, NotAuthenticated):
    """An operator with an enrolled second factor presented a sign-in that
    verified no code. The operator plane never admits a sign-in alone."""

    code = "second_factor_required"


class SecondFactorNotEnrolled(TenancyException, NotAuthorized):
    """An operator whose second factor is not enrolled yet reached a route
    other than the two that enrol it."""

    code = "second_factor_not_enrolled"


class OperatorTokenRequired(TenancyException, NotAuthorized):
    """An operator's sign-in, with its second factor, reached a route other
    than the mint. The sign-in mints one operator token, and the plane reads
    and writes with that token only (ADR 0068)."""

    code = "operator_token_required"


class WorkException(PlatformException): ...


class LeaseLost(WorkException, Conflict):
    """The item is no longer claimed by this worker; another one may hold it."""


class WorkNotFailed(WorkException, Conflict):
    """An operator's requeue named an item that is not failed: one that is
    queued, running, or done has a way forward already."""

    code = "work_not_failed"


class EventsException(PlatformException): ...


class StreamTruncated(EventsException):
    """A read of the stream after a seq below the tenant's floor: the events
    between that seq and the floor are trimmed, so no page can close the gap.
    The caller stops replaying, reads afresh what it shows, and goes on from
    `head`. Gone, not a conflict: asking again never succeeds (ADR 0040)."""

    http_status = 410
    code = "stream_truncated"

    def __init__(self, *, floor: int, head: int) -> None:
        super().__init__(f"the stream is kept after seq {floor}; read afresh and go on from {head}")
        self.floor = floor
        self.head = head


class IdempotencyException(PlatformException): ...


class DuplicateIdempotencyKey(IdempotencyException, Conflict):
    """Another record already carries this (tenant, user, key)."""


class IdempotencyKeyReused(IdempotencyException, ValidationFailed):
    """The key was seen before with a different request."""


class IdempotencyInProgress(IdempotencyException, Conflict):
    """The first request under this key has not finished yet."""

    code = "idempotency_in_progress"


class IdempotencyAttemptLost(IdempotencyException, Conflict):
    """The marker is no longer this attempt's: a retry took it over after the
    pending lease passed, and only the holder may finish or release it."""

    code = "idempotency_attempt_lost"


class SignInRefused(TenancyException, NotAuthenticated):
    """The identity provider did not sign the person in: the code was spent,
    expired, or never issued, or the person declined a device sign-in, or it
    expired before they confirmed it. Start the sign-in again."""

    code = "sign_in_refused"


class EmailNotVerified(TenancyException, NotAuthenticated):
    """The identity provider signed the person in with an address it has not
    verified. Acme links a person by a verified address only."""

    code = "email_not_verified"


class SignInPending(TenancyException):
    """A device sign-in the person has not confirmed yet: ask again after the
    interval the start named."""

    http_status = 400
    code = "sign_in_pending"


class SignInSlowDown(SignInPending):
    """A device sign-in asked about too often: wait longer before asking again."""

    code = "sign_in_slow_down"


class InvitationClosed(TenancyException, Conflict):
    """The invitation was accepted or revoked already; only a pending one is
    sent again or revoked."""

    code = "invitation_closed"


class SlackException(PlatformException): ...


class SlackWorkspaceTaken(SlackException, Conflict):
    """The Slack workspace is installed for another org. A workspace speaks
    for one org; the other org removes the app first."""

    code = "slack_workspace_taken"


class BillingException(PlatformException): ...


class PlanLimitReached(BillingException):
    """A change would take the org past a bound of its plan: the next active
    task, the next member, an api key on a plan without them. Nothing the org
    already has is touched; the refusal names the lever, the plan, the bound,
    and the plan that lifts it, and a client turns it into an upgrade."""

    http_status = 402
    code = "plan_limit_reached"

    def __init__(
        self, *, plan: str, lever: str, limit: int | None, suggested_plan: str | None
    ) -> None:
        what = lever.replace("_", " ")
        if limit == 1:
            what = what.removesuffix("s")
        if limit == 0:
            super().__init__(f"the {plan} plan includes no {what}")
        else:
            super().__init__(f"the {plan} plan allows {limit} {what}")
        self.plan = plan
        self.lever = lever
        self.limit = limit
        self.suggested_plan = suggested_plan


class SubscriptionExists(BillingException, Conflict):
    """The org pays for a plan already: a change between paid plans, or a
    cancellation, happens in the processor's portal or through the account's
    own operations, never through a second checkout."""

    code = "subscription_exists"
