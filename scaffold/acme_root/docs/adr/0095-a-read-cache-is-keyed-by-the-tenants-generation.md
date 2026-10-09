# ADR 0095: A read cache is keyed by the tenant's generation

**Status**: accepted (2026-10-09)

## Context

The guideline makes a read cache a projection with a generation
(ASY-05). Its key carries the tenant's generation, a write bumps it once
its transaction commits, the TTL is the backstop, and the cache fails
open (ASY-06). The cache has the `increment` a generation needs, and a
counter reads back through `get`. Nothing reads under it, so each
manager that caches a read would build its own reader.

## Decision

- **The scaffold holds the reader.** `ReadCache` sits on a scoped
  `CacheInterface`
  ([`read.py`](../../infra/src/acme/infra/cache/read.py)). A manager
  that caches a read takes one through its constructor, over a cache
  scope of its own. A manager that caches no read uses none of it.
- **The key is the read's name and the generation.** An entry lives
  under `<name>:<generation>`, in the manager's scope and under the
  tenant the call names. A catalog reads a product as `product:<sku>`.
  The generation is a counter in the same scope, one per tenant, and
  zero before its first bump. Two tenants that read the same name never
  share an entry or a generation.
- **The generation is read before the source.** A value loaded before a
  write and put after its bump lands under a number no read asks for.
- **A write bumps once it commits.** `bump` is one `increment` of the
  generation. A bump before the commit, or inside the transaction, lets
  a read between the two cache the old value under the new number,
  readable until its TTL.
- **The value is typed.** A pydantic `TypeAdapter` writes it as JSON and
  reads it back. An entry that does not read back is a miss, and the
  put that follows replaces it, so a rollout that changes the shape
  costs one read of the source per entry.
- **The generation counts for a day, and the TTL is shorter.**
  `increment` never moves a window once it starts, so the count starts
  again when the day ends. The constructor refuses a TTL that is not
  shorter. The entries of a window's first number are then gone when
  the next window starts, and an entry a later number finds again is at
  most a TTL old.
- **It fails open.** A miss, an entry that does not read back, and a
  cache that cannot be reached each read the source. A generation that
  is no count reads the source and caches nothing.

## Consequences

- A cached read is at most a TTL stale, and only when a bump is lost or
  a window starts again.
- A hit costs two round trips to the cache: the generation and the
  entry.
- Every entry a scope holds for a tenant shares one generation, so one
  write orphans them all. Reads that change apart take scopes apart.
- Every path that writes what a read caches bumps the generation after
  its commit, a backfill and a test that writes storage included, or
  the read stays stale for a TTL.
