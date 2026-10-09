# ADR 0092: A relayed item lands on its kind's lane

**Status**: accepted (2026-10-09)

## Context

Most work starts with a core write: the write lands a `work.<kind>`
outbox row, and the relay enqueues the item. The row carries no lane,
so every relayed item lands on the default lane. A long-held kind then
shares that lane with short items. Its items count against the lane's
tenant cap, so a tenant running a few of them waits on its short
work, and a worker deployed on a lane of its own for the kind claims
nothing. The guideline's "The Work Queue" gives such a kind a lane of
its own, and this records how Acme routes a relayed item there.

A lane on each `work.<kind>` row would let every write choose a lane,
but the lane belongs to the kind, not to the write. Every producer
would have to name it the same way, and the outbox would carry routing
that only the queue reads.

## Decision

- **The lane is the kind's, in one registry.** `WORK_LANES`, in
  `om/work/types/work_item.py` beside `WORK_PAYLOADS`, maps a kind to
  its own lane. It is empty in the scaffold. A kind it does not name
  runs on the default lane.
- **The relay and the worker read the same function.**
  `relayed_lane(kind)` reads the registry. The relay lands each relayed
  item on it, and its wake names that lane. A worker that serves a kind
  on its own lane takes its lane from it, never from a copy of the
  name.
- **A direct create keeps its caller's lane.** `enqueue` takes the item
  as its caller built it, lane included; a caller that wants the kind's
  lane reads `relayed_lane`.

## Consequences

- A kind gets a lane of its own with one line in the registry and a
  worker deployed on that lane. The relay is not edited.
- A long-held item on its own lane counts against no cap of the
  default lane, so a tenant's short items keep their share there.
- A lane registered with no worker claiming from it holds its items
  until one does. The backlog gauge reads the oldest ready item on any
  lane, so its alarm names the wait.
