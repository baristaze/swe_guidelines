# Changelog

The latest release is listed here; every release's notes, older ones
included, stay on its GitHub release. Releases are tagged
`vMAJOR.MINOR.PATCH`; see `CONTRIBUTING.md` for what bumps which
number.

## 0.42.0 (2026-09-30)

The scaffold's ops skills read the series the app exports, and ask the
error tracker for a window it takes. Minor: the two skills gain the
cloud call of each signal they report, and a test holds their schemas.
Nothing is reversed.

### Added

- `ops-watch` step 5 gives one cloud call that prints a batch's four
  numbers: the requests, the 5xx, the p95, and the worker's failures.
  Step 2 reads the load balancer's ARN once, for the p95.
- `ops-investigate` gives the cloud call of each signal it reports:
  step 4 the request count, the counts by status and route, and the
  load balancer's p95; step 5 the outcomes and the database's
  connections.
- The scaffold's tests hold every `SEARCH` schema the two skills write
  to the ones its dashboard module writes, and pin the tracker's
  dialect, the reading of an empty batch, and the cloud p95's unit.

### Changed

- A watch batch whose cloud `requests` is 0 read nothing, since the
  load balancer's health checks are requests: the batch writes "metrics
  not read", never a zero.
- The cloud p95 is one number for every route together, written in
  milliseconds, or `none` when no request crossed the load balancer.
  The cloud has no p95 by route.

### Fixed

- `ops-investigate` step 4 searches the schema the app's series carry,
  with `OTelLib`. The schema it wrote before matched nothing, so the
  request query read zero.
- `ops-investigate` step 6 asks the tracker for its window as the search
  term `lastSeen:-<since>`, only of Sentry's own host, and keeps the
  issues by their `lastSeen` field. It sends no `statsPeriod`, which the
  tracker takes only as `24h` or `14d` and answered with a 400 for the
  default hour.
