# Changelog

The latest release is listed here; every release's notes, older ones
included, stay on its GitHub release. Releases are tagged
`vMAJOR.MINOR.PATCH`; see `CONTRIBUTING.md` for what bumps which
number.

## 0.49.0 (2026-10-03)

An environment the nuke destroyed costs nothing and holds no live
credential, the create run that brings it back finds what it needs
free, and production's create protects `release`. Minor: the guideline
gains one rule, and nothing is reversed.

A destroy removes what the root declares. What the application wrote,
and what the cloud made for the destroyed resources, is not in the
state, so it stayed: a tenant's live bot token among it. And a recreate
met a zone out of the database's capacity and a DNS record that still
named the distribution the nuke deleted.

### Added

- Creating and Destroying an Environment, and OPS-19: a destroy also
  removes what the environment left outside the state, the secrets the
  application wrote among them; a tenant's secrets stay only beside a
  final snapshot that needs them.
- `scripts/cloud_nuke.sh`, after the destroy, removes what Terraform
  does not own, found by the environment's names: the tenants' secrets
  when the database's final snapshot is not found (kept with it when
  it is, and any other answer refuses), the cluster's Container
  Insights log group, and every task definition revision, paced and
  retried. A root already destroyed, with neither cluster nor database
  left, skips the apply and the destroy and finishes this step; an
  unreadable state refuses.
- The production create run protects `release` (step 5c): a deploy key
  `release` with write access, its private half stored as the
  `RELEASE_DEPLOY_KEY` secret and never shown, and a ruleset that
  restricts creations, updates, deletions, and force pushes, requires
  `no pull request into release`, and lets the deploy key alone
  through. Since such a ruleset passes every write deploy key, the run
  refuses while another exists. The deploy runbook, ADR 0024, and the
  create skill say so.
- `workos-bootstrap` reads the environment's webhook endpoints, and an
  endpoint the desired state names that is missing, disabled, or that
  the key cannot read is a dashboard step the run fails on, instead of
  a line it printed and passed.

### Changed

- The network gives every zone a private subnet, and the database's and
  the cache's subnet groups take them all; the tasks and the public
  subnets stay in the load balancer's two zones, and the first two
  private subnets keep their zones and ranges.
- The create run's step 3c, while no distribution of the account serves
  the site's name, deletes a CNAME there to CloudFront whose target no
  longer resolves, before the deploy; a target that answers, or a
  resolver that cannot tell, refuses.
- `arch-upgrade-scaffold` keeps a lockfile a move merged without a
  conflict while its check passes, and regenerates one that fails from
  the merged file, never from the copy's.

### Fixed

- A trace is found by its request id: the collector copies
  `acme.request_id` into `acme_request_id`, the key X-Ray can filter
  on, and indexes the copy; the exporter kept the dot, so no trace was
  ever found by id.
- An operator who has not enrolled a second factor is told to enrol,
  with the runbook's section, instead of a traceback, and the nuke's
  report says enrolments and tokens go with the database.
- The nuke's report names production's copies of staging's builds only
  when the artifacts bucket replicates.
- The create run says the grants go one at a time, since a dispatch
  made while another waits cancels it, and both runs say a recreated
  environment's smoke step waits on the smoke identity's grant.
- A Node with no corepack still gets pnpm, and `dev.sh` stops loudly
  when a process fails.
