"""The durable record of an idempotent request: who sent it, under which
key, what it looked like, and what it produced. Written pending when the
request begins and finished once, then read on every replay. A failure
releases it: the record stays, with its digest and its target id, and only
the attempt goes, so the retry reruns on the same id. A hand-over moves the
attempt and nothing else; the birth time is never rewritten."""

from uuid import UUID

from acme.om.base import Created, Identifiable


class IdempotencyRecord(Identifiable, Created):
    user_id: UUID  # the record is personal to the sender within the tenant
    key: str  # the caller's Idempotency-Key, unique per (tenant, user)
    request_digest: str  # a digest of the request, so a reused key is caught
    target_id: UUID  # the id the create uses, minted before the marker; a take-over keeps it
    # The token of the attempt that holds the marker: minted by begin, replaced
    # by a take-over or a re-arm, cleared by a release. finish and release are
    # conditional on it, so an attempt that lost the marker cannot finish or
    # release what a retry now holds, and a released marker is nobody's. It is
    # a uuid_v7, so it carries the moment the attempt began and the pending
    # lease is read off it (see attempt.py) and never off created_at, which is
    # the marker's birth and is written once.
    attempt_id: UUID | None
    status: int | None = None  # the outcome's status; None while the request runs
    body: str | None = None  # the outcome's body; None while the request runs

    @property
    def pending(self) -> bool:
        return self.status is None

    @property
    def released(self) -> bool:
        """No attempt and no outcome: a failure released it, and the next
        retry re-arms it and reruns on its target id."""
        return self.status is None and self.attempt_id is None
