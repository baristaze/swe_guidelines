# ADR 0025: The rules of 0.29.0 that wait for the feature they bind

**Status**: accepted (2026-09-22), trimmed (2026-09-22): the three
logins, the operator's second factor and token, the traffic identity
between runs, a session's idle lifetime, and the enqueue permission that
covers a handler's calls (`WORK_ENQUEUE_PERMISSIONS`, held by the
worker's tests since the first user-caused kinds) hold now and
are gone from the list; trimmed (2026-09-26): the work queue's and the
outbox's alarms (`OPS-15`) hold, from the gauges the worker's sweep
writes. Each item left names what closes it.

## Context

Guideline v0.29.0 adds rules for features Acme has only in part, and one
rule whose default Acme decided against. Every other rule of 0.29.0 holds
in the code. `make check` runs `arch-check` at the pinned tag, v0.37.0
today, and the items below still hold at it.

## Decision

These are deviations, each recorded with its reason and its route back:

- **The release push by an app token (`DEL-38`).** No ruleset restricts
  who may push to `release`, so the workflow token's fast-forward is
  allowed and the workflow dispatches the production deploy itself
  (ADR 0024). The app becomes the one bypass actor the day `release` is
  locked.
- **A kept build that refuses a second write (`DEL-31`).** Production's
  artifacts bucket refuses every direct write and delete under
  `builds/portal/`, the administrator's included, so only replication
  writes there. A changed source object still replicates as a new
  version, because S3 has no condition that refuses a replicated
  overwrite. The recorded digest catches it before any plan, and
  `resolve` refuses it (ADR 0024).
- **The desired count left to autoscaling.** The guideline puts
  `desired_count` in `ignore_changes`. Acme keeps it out, as the service
  module says: autoscaling is off by default, and with it off an ignored
  count would make the root's number a lie. With autoscaling on, an apply
  returns a scaled service to its floor, and the policy raises it again
  within its cooldown.

## Consequences

- With these recorded, a check against the pinned text reads them as
  decisions rather than drift.
- Each ticket that lands removes its line here. The ADR is superseded when
  the last one does.
