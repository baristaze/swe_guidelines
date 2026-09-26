# Changelog

The latest release is listed here; every release's notes, older ones
included, stay on its GitHub release. Releases are tagged
`vMAJOR.MINOR.PATCH`; see `CONTRIBUTING.md` for what bumps which
number.

## 0.34.0 (2026-09-26)

A hint reads the entity it names, and a switch mounts every screen
afresh. The client text allowed an invalidation or a cache write with
no default, and the app scaffold invalidated whole collections on every
hint and re-read them after every write. A change then cost one
collection per open tab. The cache write is now the default. Minor: a
rule is added.

### Changed

- "Client App Architecture, State and Data": a write puts its own
  answer into the cache and does not re-read the collections that
  answer covers. A hint reads the one entity its `target_id` names,
  through the authorized read, and places it; a read that finds nothing
  removes it. A collection is read again only where placement cannot
  decide: at the edge of a loaded page, or past a burst of hints. A
  read that answers late never overwrites a newer version.
- "Client App Architecture, One Tenant at a Time": after a switch every
  screen starts again in the new tenant. Dropping a cache does not make
  a mounted screen read, so the signed-in tree is mounted afresh per
  tenant.
- Lens DEL-13 (TanStack Query for server state, Zustand for client
  state): **Look for** adds what a hint and a successful write cost in
  reads; **Violation** adds a router that invalidates whole collections
  on every hint, and a mutation that re-reads collections its own
  answer already covers. The Principle and the severity are unchanged;
  258 lenses.
- `arch-scaffold-app`: the router reads the entity a hint names and
  places it, and a successful mutation writes its answer into the
  cache, instead of invalidating by name.
