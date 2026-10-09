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
- **The key is the read's name, the window, and the generation.** An
  entry lives under `<name>:<window>:<generation>`, in the manager's
  scope and under the tenant the call names. A catalog reads a product
  as `product:<sku>`. The generation is a counter in the same scope, one
  per tenant and window, and zero before its first bump. Two tenants
  that read the same name never share an entry or a generation.
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
- **The window is part of every key.** `increment` never moves a
  counter's window once it starts, so a count starts again when its
  window ends, and its numbers come back. A longer window would not
  help: the next count reaches the old window's last number again, and
  an entry cached under it just before the turn may still live. So
  windows are whole days from the epoch, and the generation's key and
  every entry's carry the window: a count that starts again in a new
  window never meets an entry of an old one. A counter's own window runs a day
  from its first bump, which falls inside the day it counts, so no
  number comes back within that day. The constructor refuses a TTL of a
  day or more, since an entry is read only inside its own window.
- **It fails open.** A miss, an entry that does not read back, and a
  cache that cannot be reached each read the source. A generation that
  is no count reads the source and caches nothing.

## Consequences

- A cached read is at most a TTL stale, and only when a bump is lost,
  or when the clocks of a write and a read fall on two sides of a
  window's turn.
- A hit costs two reads of the cache: the generation and the entry.
- Each day starts cold: the first read of each name after the turn goes
  to the source, for every tenant at once.
- Every entry a scope holds for a tenant shares one generation, so one
  write orphans them all. Reads that change apart take scopes apart.
- Every path that writes what a read caches bumps the generation after
  its commit, a backfill and a test that writes storage included, or
  the read stays stale for a TTL.
