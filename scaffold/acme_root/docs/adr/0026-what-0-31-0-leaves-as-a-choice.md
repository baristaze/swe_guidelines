# ADR 0026: What guideline 0.31.0 leaves as a choice, and what release B removes

**Status**: accepted (2026-09-22). When the two dead identity columns
are dropped is superseded (2026-09-25) by
[ADR 0038](0038-a-dead-column-leaves-the-mapping-before-the-table.md):
they leave the mapping first, and the release after drops them. The
drop is done (2026-09-25): migration 202609290000 takes both out of the
table. The session lifetimes are amended (2026-09-26) by
[ADR 0063](0063-sessions-last-weeks.md): 14 days idle and 30 days
absolute, longer than the guideline's defaults.

## Context

Acme moves its pin from guideline v0.29.0 to v0.31.1 in one change,
release A. It adds the three database logins, the fence that admits the
system scope to the system login, operator tokens and a second factor,
the grant job, the caller's version and 412, the per-tenant work-item
key, and the bounded upload. Most of the new rules now hold in the code.

A few rules name a shape Acme does not take, because Acme has a
reason or lacks the feature the rule is for. Release A also keeps three
compatibility pieces so the release before keeps serving while it rolls
out. This record lists both.

## Decision

These are choices, each kept on purpose:

- **More system-scope methods than the guideline lists (`CTX-12`).**
  "The Second Fence" names the sweeps and the pre-identity lookups.
  Acme passes no tenant in a few more, and each says why in its
  docstring. Most read or write the two `system` tables, `identities`
  and `sign_in_delays`, which have no tenant to pass:
  `read_identity`, `write_identity`, the TOTP writes
  (`write_totp_secret`, `confirm_totp`, `use_totp_step`), and the
  sign-in delay (`read_sign_in_delay`, `record_failed_sign_in`,
  `clear_failed_sign_ins`, `purge_sign_in_delays`). A second group is
  the operator plane's reads across tenants: `read_orgs`, `count_orgs`,
  `count_users`, and `read_org_by_slug`. A third is the steps between an
  identity and its tenants: `read_users_by_identity`,
  `read_memberships_by_identity`, and `read_session_by_id`. The last
  group is the two counters behind the metrics: `count_since` and
  `count_created_since`. `[tool.arch-check.options.CTX-12]` in
  `pyproject.toml` and `om/tests/unit/test_storage_exceptions.py` hold
  the same list.
- **Sessions, API keys, and socket tickets stay `both`.** The guideline's
  scaffold makes them `identity` tables. In Acme each one belongs to
  one person inside one org: a session is in one org at a time, and a
  switch ends it (ADR 0020). So their policy stays on `org_id`, narrowed
  by the person. An operator token is a session under the system scope,
  which only `read_session_by_digest`, on the system login, finds.
- **No `read_identity_by_issuer_subject`.** It is the fifth pre-identity
  lookup, behind an external provider's find-or-create. Acme signs in
  with an email and a password and has no external provider, so there
  is nothing for it to look up. It arrives with the first provider.
- **The session lifetimes.** The guideline's defaults are 24 hours idle
  and 7 days absolute. Acme keeps shorter ones: 4 hours idle
  (`ACME_SESSION_IDLE_LIFETIME_SECONDS`) and 12 hours absolute
  (`ACME_SESSION_LIFETIME_SECONDS`). A person signs in once a working
  day. An operator token lasts an hour at most, everywhere.
- **`valkeys://` to the cache.** The guideline writes `rediss://` for
  TLS to the cache. Acme's client is Valkey's, whose TLS scheme is
  `valkeys://`. It is the same transport under the client's own name.
- **The work item's unique index leads with the tenant (`ASY-16`).**
  The lens asks for a unique index on `(org_id, idempotency_key)` and
  calls one on the key alone a violation, because a key another tenant
  holds would answer `KEY_EXISTS` and the read-back under this tenant
  would find nothing. `queue.work_items` carries the index the lens
  names. The rule's coverage is partial: it reads a unique index on the
  key alone and nothing else, so it reports the shape it asks for. The
  exception in `pyproject.toml` names this record.
- **Audit redaction has nothing to redact today (`STO-34`).** An audit
  entry is an event (ADR 0011), and every event, outbox payload, and
  work-item payload carries ids only. So the erasure sweep finds no
  personal value in an audit entry. The redaction arrives with the
  first audit entry that records a value.

Release B followed release A once every task of the release before had
stopped. It removed the three compatibility pieces: the transitional
`'acme'` clause from every `tenant_fence` policy, which closed
[ADR 0023](0023-the-row-level-security-bypass-is-a-setting-for-now.md);
`uq_work_items_idempotency_key`, the work-item key's index on the key
alone, leaving the one led by the tenant; and the deprecated body and
query `version` on the task routes, which `If-Match` and
`expected_version` replaced (ADR 0009).

One piece is left, a release further out than release A planned.
`identities.failed_sign_ins` and `identities.last_failed_sign_in_at`
are dead: `sign_in_delays` holds the run, nothing writes either, and
the database's default fills the one that is NOT NULL. Release A still
read both, though, because a mapped column is in every `SELECT` the
mapper emits. A migration runs before the services roll, and a release
is compatible with the one before it, so a drop in release B would have
met a release A task mid-rollout and failed its next sign-in. Release B
defers both columns instead: they leave every read, and the mapping
stays for the schema check to compare with. The release after drops the
columns and the mapping together.

## Consequences

- A review that reads `CTX-12`, `STO-34`, or the session lifetimes
  against Acme cites this record.
- Release B is one pull request of contractions: migrations that drop,
  and one API change. It needs no rollout order of its own, since
  nothing running still reads what it drops. What release A still read,
  it does not drop.
- The day Acme adds an external provider, the fifth lookup lands with
  it and its line here goes.
