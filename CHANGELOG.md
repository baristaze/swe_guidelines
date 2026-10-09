# Changelog

The latest release is listed here; every release's notes, older ones
included, stay on its GitHub release. Releases are tagged
`vMAJOR.MINOR.PATCH`; see `CONTRIBUTING.md` for what bumps which
number.

## 0.58.0 (2026-10-09)

A read cache is keyed by the tenant's generation, which a write bumps
once its transaction commits. The portal's channel hands over each
notice once, as its cursor passes it. A purge across tenants acts in
one tenant's context, a member's add asks the org's gate, and an
optional investigator's skill says where an integration's deliveries
stop. Minor.

### Added

- A read cache: the scaffold's `ReadCache`
  (`infra/src/acme/infra/cache/read.py`), a typed reader on a scoped
  `CacheInterface`. It reads and puts under
  `<name>:<window>:<generation>` for the tenant the call names, and
  reads the generation before the source. `bump` is one `increment` of
  it. Windows are whole days. A miss, an entry that does not decode,
  and an unreachable cache each read the source. ASY-05 names it as its
  shape, ASY-06 as a call site that fails open, and ADR 0095 records
  it.
- Notices on the channel: a notice, a record the person is told about,
  such as an invitation accepted, is handed over once, as the cursor
  passes it. A push hands it over at once. A replay hands over its
  notices together when it ends, from all its pages, whether it reaches
  the head or a read fails partway. A stopped channel hands over
  nothing, so a switch never shows the old tenant's notice. The portal
  channel takes the optional `isAnnounced` and `announce`, and the
  scaffold's provider passes neither. NET-20 holds the hand-over,
  DEL-40 the switch, and ADR 0096 records it.
- A purge across tenants that must act in the tenant of a row it found,
  such as asking another manager to erase the files attached to it,
  takes that tenant's context from
  `TenancyManagerInterface.sweep_context`: the one the pass minted.
  Under the pass's stage it reads nothing. A tenant marked purged has
  none, and its row goes as it is. Such a purge stays one call.
- A member's admission: `add_member_to`, the one create every path that
  adds a member to an org goes through, takes the org's gate where a
  system bounds who may join. The gate is asked once the person is
  known to be new to the org, so a repeated add never counts a member
  twice. Its refusal comes before any write, and its outbox rows ride
  the add's commit. The scaffold's tenancy carries this seam and the
  purge's, and wires neither. CTX-05, CTX-16, CTX-17, and NET-11 check
  them, and ADR 0097 records them.
- `ops-integration-silent`, an optional investigator's skill in the
  scaffold. It counts an inbound webhook route's answers by status,
  reads the worker's `deliveries` outcomes, the `webhooks` queue, and
  its dead letters, and says where the deliveries stopped, why, and
  what next. Locally it reads through `docker compose` alone; in the
  cloud, under the investigate profile. A tree with no inbound webhook
  leaves it out. OPS-11 lists it among the optional skills, and
  arch-check's required thirteen stay.

### Changed

- Infrastructure, Cache: a write bumps the tenant's generation once its
  transaction commits, never before, so no read caches the old value
  under the new number. ASY-05 holds the bump's time.
- Operations Without a Principal: a deleted tenant keeps its service
  context until a pass finds nothing of it left and marks it purged.
