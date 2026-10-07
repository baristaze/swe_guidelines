# Changelog

The latest release is listed here; every release's notes, older ones
included, stay on its GitHub release. Releases are tagged
`vMAJOR.MINOR.PATCH`; see `CONTRIBUTING.md` for what bumps which
number.

## 0.52.1 (2026-10-07)

A copy's local Postgres reads healthy only once it takes a connection
over TCP, and the release-before test holds a copy that names a test in
its deselect file. Patch: two fixes and a pin bump, and nothing is
reversed.

### Fixed

- The local stack's Postgres healthcheck (`x-postgres` in
  `deployment/local/docker-compose.yml`) asks over TCP:
  `pg_isready -h 127.0.0.1`. On an empty data directory the image's
  entrypoint runs the init script on a server that listens on the local
  socket alone, so the socket check read healthy while a network
  connection was refused, and a service waiting on `service_healthy`,
  `glitchtip-db` among them, could exit 2. `infra/tests/test_local_postgres.py`
  holds every Postgres server's healthcheck, in every compose file
  under `deployment/local/`, to a client that names a host.
- `infra/tests/test_release_before.py` builds its deselect file from the
  real file's comments and its own line, so the first test a copy names
  in `scripts/release_before_deselect.txt` no longer breaks the copy's
  unit gate.

### Changed

- The pinned tools move: uv 0.12.19 and ruff 0.16.9
  (`.github/pins/requirements.txt`).
