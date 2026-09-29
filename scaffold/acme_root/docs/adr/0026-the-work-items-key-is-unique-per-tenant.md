# ADR 0026: The work item's key is unique per tenant

**Status**: accepted (2026-09-28)
## Context

ASY-16 (The Work Queue) asks for a unique index on
`(org_id, idempotency_key)` on the work table, and calls one on the key
alone a violation. With the key alone, a key another tenant holds
answers "exists", and the read-back under this tenant finds nothing.

`arch-check` reads the rule partly. It looks for a unique index on the
key alone and nothing else, so it reports the shape the lens asks for.

## Decision

`queue.work_items` carries `uq_work_items_org_id_idempotency_key`, the
unique index on `(org_id, idempotency_key)` the lens names, led by the
tenant. There is no index on the key alone.

`[[tool.arch-check.exception]]` in `pyproject.toml` excuses ASY-16 on
`om/src/acme/om/work/storage/tables/work_items.py` and names this
record. It records the checker's partial reading, not a departure from
the rule.

## Consequences

An enqueue under a key the same tenant holds answers the item as
stored. A key another tenant holds is no conflict: each tenant has its
own space of keys.

The exception goes when the checker reads a compound unique index that
the tenant leads as the shape it asks for.
