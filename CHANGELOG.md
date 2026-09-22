# Changelog

The latest release is listed here; every release's notes, older ones
included, stay on its GitHub release. Releases are tagged
`vMAJOR.MINOR.PATCH`; see `CONTRIBUTING.md` for what bumps which
number.

## 0.30.0 (2026-09-22)

The security rules reach the lenses and the scaffolds. Sign-in finds
the identity it signs in, the scaffolds create the three database
logins the policies admit, the operator's second factor is checked at
the operator gate, and every security rule has one lens. `arch-check`
never fails silent or whole, and the benchmark keeps each run to
itself. Minor: rules are added and sharpened, and two are reversed,
which before 1.0.0 bumps the minor number.

### Changed

- **Reversed.** "Operator Roles" and "The Operator Context", lens
  `OPS-07`: a second factor at every operator and cloud sign-in, no
  longer at every sign-in. A tenant's sign-in does not require one.
  The operator gate checks a TOTP code, from an authenticator enrolled
  per operator identity, before admission. An operator with no secret
  enrolled holds `ENROL` alone, and a reused code is refused. `OPS-07`
  owns the rule at high; `DEL-48` and `CTX-20` name it.
- **Reversed.** "Migrating a Deployed Database", lens `DEL-45`: the
  operator allowlist is written only by the grant job, which grants
  every entry and disables one. A route on the operator plane that
  writes the allowlist is a violation.
- "The Second Fence", lenses `STO-28`, `CTX-12`: the four lookups that
  run before an identity is known are named system-scope methods, run
  on the system login, and the identity policy carries the same
  system-login clause as the org policy. Each table carries the policy
  its scope names, and a system table has none. Identities are scoped
  to the identity, never listed among the global tables.
- "The Work Queue", lens `ASY-16`: the idempotency index leads with
  the tenant, `(org_id, idempotency_key)`, so a key is unique per
  tenant, not across tenants. A create that can collide on two keys
  reports which one, and the queue reads back by key.
- One lens owns each breach two lenses shared, at the higher severity.
  `CON-21` owns a check before a create's insert and `OPS-18` the
  environment tag on every resource, both moved from medium to high.
  `DEL-47` owns the deployer's own trust.
- Lenses say only what the guideline says: `OPS-01` flags a writing
  cloud credential and names its two exceptions; `NET-27` checks the
  internal token's audience and key id; `ASY-13` holds a tenant's
  secret to the point of use and a process credential to start;
  `ASY-19` names every purge the sweep runs. `STO-23`, `DEL-05`,
  `DEL-06`, `OM-02`, `CTX-02`, `CON-02`, `ASY-27`, `OPS-12`, `OPS-15`,
  `NET-23`, `ASY-08`, and `ASY-14` follow their sections.
- The rest of the guideline: the index rules are counted as five;
  `presign_put` takes `max_bytes`; work-item and event payloads are
  stored shapes, staged across two releases; the edge idempotency
  table covers 429; an inbound webhook's key is a UUID v5 over the
  provider and its delivery id; a bookkeeping sweep takes the request
  stage; a manager calls a peer for a fact its decision rests on, and
  the service impl joins exposed operations; the relay assigns the
  seq, a socket frame is a hint, and `hello` and every `pong` carry
  the head seq; the cloud write boundary names the smaller
  environment's widening.
- The scaffolds follow: three database logins with their own URLs,
  and the system scope through the funnel; TOTP enrolment, the
  operator gate's check, and a `grant-operator` subcommand; the sweep
  purges markers, tickets, and sessions and sets the queue gauges;
  a namespace's service is wired into the API container; the API docs
  are served locally only; production tags each promoted image
  `prod-<commit>`; the nuke waits for a person's word after its dry
  run; a retried create keeps its `Idempotency-Key`.
- A breach whose ADR quotes the rule is a documented exception: one
  line under a new Deviations section of the review report, never a
  finding and never a lower severity.
- A lens Principle may run to 120 words, a skill description to 500
  characters, and all the descriptions together to 6000.

### Added

- Nine lenses for security rules no lens held, all high: `CTX-33`, a
  cached read sits below authorization; `CTX-34`, the permission that
  enqueues a kind covers its handler's calls; `CTX-35`, a secret is a
  tenant's and no caller writes `credential_ref`; `CTX-36`, failed
  sign-ins are throttled per identity and a session has two lifetimes;
  `ASY-31`, an inbound webhook is authenticated by its signature and a
  replay window; `DEL-49`, an error event carries no secret; `STO-30`,
  a tenant-supplied unique key leads with `org_id`. `STO-21` splits
  into three: `STO-31` holds backups, the rehearsed restore, and
  reconciliation, and `STO-32` the purge of soft-deleted rows and
  personal data. 252 lenses.
- The lens check holds every identifier a lens quotes to the section
  the lens cites.

### Fixed

- The benchmark keeps each run to itself: a run folder is unique and a
  stream file is never reopened. The subject's output keeps every line
  and every byte. The judges read the target the subject saw, tests
  included. The workflow runs the subject in the container.
- `arch-check`: a rule that raises is an `ERROR` finding and the others
  still run; a file the parser cannot hold is a `PARSE` finding; a
  role written with `auto()` is read; a byte order mark is dropped;
  `DEL-10`, `NET-09`, `OM-10`, `ASY-15`, `CON-10`, and `ASY-29` read
  what they missed; a malformed option exits 2.
- The scripts run on the Python `uv` selects, so the 3.10 leg runs
  them on 3.10. They read fences, anchors, section numbers, and
  `allowed-tools` the way the host does.
