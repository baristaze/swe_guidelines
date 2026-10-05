# Changelog

The latest release is listed here; every release's notes, older ones
included, stay on its GitHub release. Releases are tagged
`vMAJOR.MINOR.PATCH`; see `CONTRIBUTING.md` for what bumps which
number.

## 0.51.1 (2026-10-05)

A copy stops and starts its local stack without removing it. Patch:
two developer targets in the scaffold, and nothing is reversed.

### Added

- `make stop` in the scaffold stops every container of every file and
  profile `make down` names, and removes none, so the stack's ports are
  free and its data stays. `make start` starts the containers that
  exist, and only those, waiting on health, with no build, migration,
  or seed; on a tree with no container, it runs `make up`. `make help`,
  the quick start, and the local deployment's Commands table list both
  beside `up` and `down`.

### Changed

- `make down` and `make infra-down` say they remove the containers, so
  they read apart from `make stop`. One `COMPOSE_ALL` names every file
  and profile for `down`, `reset`, `stop`, and `start`.
