# The maintenance worker

The background process of Acme. Every replica runs three things side by
side, started in `main.py`.

- **The work loop** (`loop.py`) claims items from the work queue on its
  lane and runs each under the context the claim built. It renews each
  lease, and completes, fails, parks, or refuses the item. The handlers are
  in `handler.py`, `orchestrations.py`, and `accounts.py`. The lane's cap,
  `ACME_WORKER_TENANT_CAP`, keeps one org from holding every worker, and
  an org's own cap, which an operator sets, holds for that org in its
  place.
- **The delivery consumer** (`deliveries.py`) long-polls `Queues.WEBHOOKS`,
  where the API queues each provider's verified delivery, and applies it
  once in the org it names. A message that can never apply is dropped; any
  other failure comes back.
- **The sweep** (`loop.py`) runs on a timer, within a budget. It requeues
  expired leases, relays the outbox, runs the standing chores of the
  tenants one read names as due, ends each resource's lease past its
  expiry and the skew margin and offers the resource to its line, purges
  every row past its retention (`settings.py`), counts the platform's
  size, and logs the queue's gauges.

`serve` runs the three; `health` asks the running process's `/healthz`.

## Adding a work kind

Add the kind, its payload, and the permission that asks for it in
`acme.om.work`. Write a handler that names the permissions it calls with
(`REQUIRES`), and add it to `handlers` in `build_loop`; a test holds the
two to each other. A long-running kind is an `OrchestrationKind` whose step
is mapped in `build_loop`.

## Adding a chore

A chore is housekeeping that needs one tenant's own context, such as
opening the next period of a record kept per period: an
`OrchestrationKind` started with its `period`, which answers a period
already open as stored. Add it to `chores` in `build_loop`. The first
chore also wires `chore_tenants`, the one read across tenants that names
the live tenants where any chore is due, in id order after a cursor. It
lives in the namespace whose rows say a chore is due, and is listed with
the methods that take no tenant. Housekeeping that one call does across
tenants, as a purge does, is an `across` step instead
([ADR 0089](../../docs/adr/0089-the-sweep-runs-standing-chores-in-the-tenants-one-read-names.md)).

## Adding a delivery provider

Implement `DeliveryProviderInterface`: read the delivery, name its org, and
apply it under an id derived from its key, so a copy changes nothing. Add it
to `providers` in `build_consumer`, under the name the API queues it with.
