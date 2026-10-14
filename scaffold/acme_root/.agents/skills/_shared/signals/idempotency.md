# Signals: idempotency

The markers that make a retried write answer once: a key, the attempt
that holds it, and the answer it gave.

## Outcomes

- `idempotency`, counted by the API's gateway: `recorded`, a write's
  answer stored under its key; `replayed`, a retry answered from it;
  `in_progress`, a retry refused while the first attempt still runs;
  `released`, an attempt that failed, so a retry runs the request
  again; `attempt_lost`, an attempt that ran past its lease while a
  retry took the marker over.
