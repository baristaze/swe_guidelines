# Changelog

The latest release is listed here; every release's notes, older ones
included, stay on its GitHub release. Releases are tagged
`vMAJOR.MINOR.PATCH`; see `CONTRIBUTING.md` for what bumps which
number.

## 0.65.0 (2026-10-23)

A call whose refusal costs its caller what it holds goes through under
load, and every blob crosses by a presigned URL, as a multipart upload
that resumes. Minor, with two reversals: the presigned POST and the
presign that may answer `None` go, and CTX-07 allows a second context
variable.

### Added

- **A holder's calls**, in the Gateway, held by lens NET-37 (high). A
  route whose refusal costs its caller what it holds (a lease's renewal
  or release, a heartbeat, a result, a report) is marked `HOLDER_CALL`.
  Admission counts it in a `holds` lane of its own (50), and its
  credential spends a budget of its own (600 a minute). Lease renewal
  and release carry the mark. NET-32 and NET-08 point at it. ADR 0106.
- A reserve beside each database pool, `ACME_DATABASE_POOL_RESERVE`
  (2; staging 1, production 2): connections only a holder's call takes,
  and only when its pool has none free, on every role and under both
  logins. A slow database that fills the pool still serves the renewal
  that keeps a lease. The connection budget counts it, in
  `arch-scaffold-service` too. ADR 0108.
- Multipart transfers in Buckets: start an upload, presign a part for
  its `Content-Length`, list the parts, complete with the size, abort.
  Media signs only the parts an upload still misses, so an upload cut
  off resumes by its id, and it sweeps abandoned uploads. The Python
  client and the CLI stream parts (`--resume ID`), and the TypeScript
  client adds `uploadFile`. ADR 0107.

### Changed

- A blob crosses only by a presigned URL, at any size, as a multipart
  upload (Buckets, the Core list, ASY-08). This reverses the presigned
  POST. arch-check refuses a presign that may answer `None`, which
  reverses the optional presign. The local backend signs its own URLs.
  Terraform: browser buckets take `PUT`, and the policy lists and aborts
  uploads.
- CTX-07 allows a second context variable: a holder's call's mark, set
  by admission and read only by the storage funnel. This reverses the
  one-variable rule in part.

### Removed

- `presign_post`, and the media route that served a file's content
  through the API.

### Fixed

- A copy's first commit (`scaffold/new.py`) and a render
  (`scaffold/base.py`) hold only what the scaffold's `.gitignore` files
  keep. A checkout whose stack had run put `.local/` (the local secrets
  and buckets) into a copy's history before. A base that holds such
  files loses them at its next move.
- The purge plan test passes on any index its retention column leads,
  or the one `org_id` leads with that column after it, and fails on a
  sequential scan.
