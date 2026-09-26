# Changelog

The latest release is listed here; every release's notes, older ones
included, stay on its GitHub release. Releases are tagged
`vMAJOR.MINOR.PATCH`; see `CONTRIBUTING.md` for what bumps which
number.

## 0.36.0 (2026-09-26)

The audits are built-in skills, a side effect is done only when it
happened, and a trust decision pushed to a socket has a pull behind it.
Four questions about a system repeat after a large change, before a
release, and as the product grows, so each is now a skill every system
ships. The bus delivers at most once. A relay could mark an outbox row
done after the bus dropped its publish, and nothing sent it again. A
socket that missed a revocation kept its trust until the session
expired. A tree on 0.35.0 copies the four audits and their tools,
leaves a dropped publish pending, and adds the socket's recheck. Minor:
rules are added and sharpened. `publish` may now answer a boolean,
which widens ASY-09 and reverses nothing.

### Added

- "Operations, Operational Skills": four audits join the nine
  operational skills. `audit-retention` finds the stores that grow
  without bound and what trims each. `audit-query-indexes` checks the
  indexes against the queries and names what breaks first under load.
  `audit-database-calls` counts the database calls of each endpoint and
  flow, at least and at most. `audit-deploy-time` finds where a
  deploy's minutes go and what would shorten it. An audit is read-only.
  An audit of the database builds a database of the run's own on the
  local stack, seeds it at a stated scale, and drops it, so it never
  logs in to a shared one. Its report puts the answer first, then a
  verdict per row, the findings by impact with a fix and an effort
  each, and what it could not verify. It proposes tickets and never
  fixes.
- Lens OPS-11 (The built-in skill set, one per task that repeats) lists
  thirteen skills, and `arch-check`'s OPS-11 fails a tree that lacks
  one. An existing tree copies the four templates from
  `skills/_shared/ops-skills/` into `.claude/skills/<name>/SKILL.md`,
  with its root package for `acme`, and adds the tools under
  `ops/audit/` that they run. Lens OPS-10 (The local stack is an
  environment every skill runs against) names the deploy audit beside
  the administrator's two as a skill that acts on a cloud.
- "Operations, Operational Skills": two optional audits.
  `audit-credential-lifetimes` states, for every credential kind on
  every channel, where it is checked, how often, and what a check
  costs; the longest a revoked credential keeps working; how a role
  change reaches it; its rate limit; and whether its check fails open
  or closed. `audit-provider-calls` states, for every flow, its
  external calls; whether they repeat, depend on each other, or sit in
  the request path; whether the client is reused; the timeout times the
  retries; and the request's own deadline. Both read the code and the
  settings alone and hold no credential. OPS-11 still requires the
  thirteen, and `arch-check` passes a tree with or without the two.
- `arch-scaffold-new` copies fifteen templates: the nine operational
  skills, the four audits, and the two optional audits. Its ops package
  writes the tools under `ops/audit/`: the run's own database, the
  seed, the plans, the database call counter, and the deploy timeline.

### Changed

- "Infrastructure, Topics", lens ASY-09 (Topics are a fixed enum with a
  typed payload map): `publish` returns no id. It may answer one
  boolean: the bus took the event, or it did not. A caller that
  publishes on behalf of a side effect keeps that effect pending on
  `False`. A caller that publishes a hint ignores the answer.
  `arch-check`'s ASY-09 accepts `-> None` or `-> bool`, so a tree on
  0.35.0 still passes.
- "The Storage Layer, Database Roles", lens STO-20 (A handoff after a
  core write is a core row plus an outbox row): the relay marks an
  outbox row done only when its side effect happened, the destination
  row written and the publish taken by the bus. A dropped publish
  leaves the row pending, and the sweep relays it again. One rule holds
  for every kind of row that publishes. A relay that marks a row done
  when the bus refused its publish is a violation. An existing tree
  makes its `publish` answer a boolean and its relay mark a row done
  only on `True`, as the scaffold does.
- "The Network Layer, Realtime at the Edge", lens NET-30 (The stream is
  a stream of hints, whole per tenant): a frame and a replayed record
  may carry one field of the entity, its compare-and-set `version`,
  when the entity has one. A client that already holds that version
  skips the read. The write's outbox row carries it, and the relay
  copies it onto the `Event` and the publish. Any other field of the
  entity on a frame is still a violation.
- "OpContext, Stages", lens CTX-27 (A socket closes at its session's
  expiry and on the revocation frame): every socket rechecks its
  session and membership each `session_recheck_interval`, five minutes
  by default, and closes when either has ended or the role has changed.
  That interval is the stated bound on stale trust. The recheck never
  moves `last_seen_at`, so it does not renew the idle window. A
  session's lifetime bounds trust, and a connection's lifetime bounds
  resources. The server does not cap a connection's life, and closing
  or pausing a connection never ends the session. An existing socket
  adds the recheck. The scaffold's socket calls the tenancy manager's
  `recheck_session(ctx)`, and its close test covers a dropped
  revocation.
- "Client App Architecture, Realtime: One Channel per App": the
  provider may pause the socket while the app is hidden, and resume it
  with a fresh ticket and a replay after its cursor.
- "Operations, Operational Skills" and lens OPS-11: an audit of calls
  ranks its fixes. It removes a call, folds it into another, defers it
  off the request path, or caches its answer, and only then runs calls
  in parallel. Concurrent reads on a bounded pool hold more connections
  at once, and can make the tail worse for every other request.
  `audit-database-calls` ranks its fixes the same way.
