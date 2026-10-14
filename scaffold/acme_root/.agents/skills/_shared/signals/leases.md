# Signals: leases

A leasable resource one holder may use at a time, granted under a
fencing token, with a wait line in front of it.

## Kinds

- `noop`: the core's one leasable resource kind, whose grant starts
  nothing. A product's kinds are in its own namespaces' files.

## Outcomes

- `leases`: a lease on a leasable resource counts `granted`, then
  `released`, `expired`, or `revoked`; a request that left its line
  without one counts `request_expired` or `request_<why>` (`asked`,
  `waiter_gone`, `refused`).
