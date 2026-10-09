# Changelog

The latest release is listed here; every release's notes, older ones
included, stay on its GitHub release. Releases are tagged
`vMAJOR.MINOR.PATCH`; see `CONTRIBUTING.md` for what bumps which
number.

## 0.56.0 (2026-10-09)

Two concepts join the Work Queue, each with its lens and its shape in
the scaffold: a kind's own lane, on which the relay lands every item of
that kind; and a tenant's own cap on a lane, which an operator sets and
the claim holds in place of the lane's. Minor.

### Added

- The Work Queue: a kind can have a lane of its own, such as a
  long-held kind kept apart from short ones. `WORK_LANES` names the
  lane of each such kind, and `relayed_lane` reads it, for the relay
  and for a worker of the kind's own. The relay lands each item on its
  kind's lane, and the item of a kind the registry does not name on the
  default one; a direct create keeps the lane its caller sets. A
  long-held item on a lane of its own counts against no cap of the lane
  of short items. ASY-16 holds it, and ADR 0092 records it.
- The scaffold's `WORK_LANES` (empty) and `relayed_lane(kind)` in
  `work_item.py`, which `enqueue_relayed` reads for each relayed item
  and its wake.
- `arch-scaffold-worker --lane`: the kind's line in `WORK_LANES`, and a
  replica of the worker on that lane in `dev.sh`, compose, and
  Terraform. A worker of the kind's own reads `relayed_lane`; a shared
  worker's replica takes the lane from its deployment, spelled as the
  registry spells it.
- The Work Queue: a tenant can have a cap of its own on a lane, which an
  operator sets through the operator plane, with no deployment. It
  holds for that tenant in place of the lane's cap, on a lane with a cap
  or without one, in the claim's one statement. A tenant with neither
  cap is never passed over. ASY-38 holds it, and ADR 0093 records it.
- The scaffold's `TenantCap`: one `queue.tenant_caps` row per tenant
  and lane, under the one policy every tenant table has (migration
  `queue` `202609280003`). The work operator manager sets and clears it
  with `OperatorPermission.WRITE`, and reads it with `READ`, at
  `/v1/admin/orgs/{org_id}/work/lanes/{lane}/cap`, in the API document
  and both clients. A tenant past its retention takes its caps with it.

### Changed

- The claim's statement, in both storage impls, joins the tenants' own
  caps to their count (`rules.cap_for`, `rules.is_at_cap`). With no
  lane cap, it counts only the tenants that have a cap of their own.
- Scalability by Design holds a tenant at its cap at the claim: the
  lane's, or one of its own that an operator sets.
- `ACME_WORKER_TENANT_CAP` at 0 sets no lane cap, and a tenant's own cap
  on the lane still holds. ADR 0087 follows.
