# Timing

The wait between attempts, written once for every distribution: the
object model, infra, the services and workers, and the Python client. It
is a leaf. It depends on nothing outside the standard library and
imports nothing of the system, so any distribution may take it, the
client included.

- `RetryPolicy` is one growing, capped wait as a value: `base` seconds
  before the first retry, multiplied by `growth` per attempt, never
  longer than `cap`. `jitter` is the share of each wait drawn at random:
  0 draws none, 0.5 half, 1 all of it. A server's `Retry-After` is the
  floor of a wait, and the cap is its bound. No attempt count
  overflows it.
- `Retries` is one loop's count of failures over a policy: each failure
  gives the wait before the next try, and a success starts the count
  over.
- `retry_after` reads a server's `Retry-After`, whole seconds or an HTTP
  date. A value it cannot read is no ask at all.

The randomness and the clock are handed in, so a test asserts both ends
of a wait without sleeping. A store that spells a curve in its own query,
such as the outbox's claim, bounds its exponent by
`RetryPolicy.steps_to_cap()`, as the policy does.

```python
from acme.timing import Retries, RetryPolicy

retries = Retries(RetryPolicy(base=0.5, cap=30.0, jitter=0.5))
await asyncio.sleep(retries.failed())  # after a failure
retries.reset()  # after a success
```

```bash
uv run pytest -q libs/timing/tests
```
