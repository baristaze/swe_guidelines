# Changelog

The latest release is listed here; every release's notes, older ones
included, stay on its GitHub release. Releases are tagged
`vMAJOR.MINOR.PATCH`; see `CONTRIBUTING.md` for what bumps which
number.

## 0.55.0 (2026-10-09)

Five concepts join the guideline, each with its section, its lens, and
its shape in the scaffold: a tenant's share of a shared lane, held at
the claim; a provider's outage, as a mark every process reads; the
sweep's standing chores, run only in the tenants one read names as due;
an integration acting as the member its proven address names; and a
portal that reads the one entity a hint names. Minor.

### Added

- The Work Queue: a lane that tenants share can cap the items one
  tenant holds claimed. The claim's one statement passes over a tenant
  at its cap under a live lease, and its items stay unwritten and spend
  no attempt. ASY-38 holds it, and ADR 0087 records it.
- The scaffold's `claim(..., tenant_cap=None)`, the clause in both
  `claim_next` impls, counted once per statement over the claim's
  index, and `ACME_WORKER_TENANT_CAP` (0 sets none).
- The outage signal, beside the breaker: a mark keyed by the provider
  and the credential (the org and the secret's name, never its value),
  with a time to retry. A caller reads it before it calls, marks it when
  its calls fail together, and clears it on a success. A step that reads
  a mark parks on `provider_unavailable` until the retry time, and the
  parks on one mark land one wake for the org, which resumes them
  staggered. CON-24 holds it, and ADR 0088 records it.
- The scaffold's `OutageSignalInterface`, on the infra root as
  `get_outages()`. The cache impl shares the mark over Valkey and fails
  open; one process takes the null impl.
- Maintenance Without a Scheduler states the chore: a step per tenant,
  run under that tenant's service context, only in the tenants one read
  across tenants names as due. A pass has a time budget, and a cursor
  carries the next pass on from the last tenant it ran. ASY-19 holds
  it, and ADR 0089 records it.
- The scaffold's maintenance loop takes `chores` and `chore_tenants`: a
  page of `chore_batch` (1,000) tenants a pass, before the ring, and the
  pass line counts them (`chores`). The scaffold keeps no record per
  period, so it wires no chore; ADR 0089 and the worker's README say
  what a copy wires.
- Operations Without a Principal names `member_context`: an integration
  acting for a person gets the `TenantContext` of the live member whose
  address it names, found by the address's digest, with the member's
  own role and the credential kind `INTERNAL`. The address counts once a
  sign-in through the identity provider proved it; a non-member, a
  removed member, or an unproven address gets none. CTX-16 holds its
  callers: an integration's handler alone, with an address its provider
  vouches is the actor's own, never one typed at the provider. ADR 0090
  records it.
- The scaffold's tenancy manager carries `member_context`, and the
  stage enumeration and its construction sites name it.
- State and Data: a hint that names a version the cache already holds
  reads nothing, and hints that arrive together are read together. An
  entity named twice is read once, and a burst past a bound reads its
  collections once instead. DEL-13 names the reads they rule out.
- The portal's hint reader (`apps/portal/src/realtime/hints.ts`), which
  knows no entity. It gathers a short window of hints, skips a version
  the cache holds, reads each record once, places it or removes it when
  not found, and past a bound reads the collections once.
  `queries/ledger.ts` refuses a late answer.
- `GET /v1/users/{user_id}`: the authorized read a user push leads to,
  held by the tenant-isolation sweep.

### Changed

- Scalability by Design puts a lane's tenant cap before a lane of the
  tenant's own.
- The worker's identity-provider calls (`ProviderCalls`) read the
  outage mark first and park with no call, mark it on a 503, and clear
  it on a success. ADRs 0039 and 0051 follow.
- A user push in the portal goes through the hint reader, instead of
  invalidating the member list and `me`. A replayed page routes the
  last push of each member, not of the entity, and a session's end
  drops what the reader heard and watched.
