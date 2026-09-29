# Writing a flows file of the run's own

`ops/audit/dbcalls.py run` drives the built-in flows of
`ops/audit/dbcalls_flows.py`, then every flow of each `--flows` file, in
the order the file lists them in `FLOWS`. The file lives in the run's
evidence folder, never in the repository. It is plain Python that the
counter imports; the built-in flows are its best examples.

```python
"""Flows the built-in ones do not reach, for this run."""

from typing import Any

from dbcalls_flows import worker_request


async def api_keys_at_size(w: Any) -> None:
    h = w.state["H"]  # the seeded owner's headers, set by the built-in `seed`
    made = 0
    for n in (1, 10, 100):
        while made < n:
            await w.client.post(
                "/v1/api-keys", headers=h, json={"name": f"size {made}", "role": "member"}
            )
            made += 1
        await w.http("api-keys", f"GET /v1/api-keys ({n} keys)", "GET", "/v1/api-keys", headers=h)


async def maintenance_contexts(w: Any) -> None:
    work = w.worker.managers.work
    await w.measure(
        "sweep", "maintenance_contexts", lambda: work.maintenance_contexts(worker_request())
    )


FLOWS = [api_keys_at_size, maintenance_contexts]
```

What a flow has on `w`, the `World`:

- `w.http(area, name, method, path, **httpx_kwargs)`: one request through
  the app, counted; returns the response. `name` is what the report
  calls it: the route and what makes this call differ.
- `w.measure(area, name, lambda: <awaitable>)`: one manager or worker
  call, counted; an exception becomes its status.
- `w.client`: the same app, uncounted, for setup (making API keys,
  signing in a second person).
- `w.state`: what the built-in flows left. `seed` sets `org` (the team
  org), `owner_email`, `bob` (a member), `H` and `bobH` (their headers),
  and `s` (the run's suffix, to keep made names unique).
- `w.container` (the API's managers and services), `w.worker` (the
  worker's container), `w.loop` (the worker loop: `_try_claim`,
  `_sweep_once`), `w.idp` (the identity provider's twin: `issue_code`,
  `confirm_device`, `accept_invitation`, and `signed_event`, which
  answers the body and the `WorkOS-Signature` header of a delivery to
  `POST /webhooks/identity`), and `w.integrations` (the integrations
  root over that twin). A sign-in's redirect is on `55173`. The
  webhook's queue consumer runs through the worker's
  `build_consumer(w.worker)` (in
  `workers/maintenance/src/acme/workers/maintenance/main.py`), one
  message at a time with its `handle`.
- `await w.sql("<statement>")`: a statement as the superuser on the
  audit database, uncounted, for setup no route makes, such as aging
  rows past a retention or making an item ready now.
- `w.mark()` and `w.since(mark)`, with `w.record(area, name, status,
  window)`, to count a span by hand; `drain(w)` in the built-in flows is
  the example (a claim, its handler, and its settle as one count).

The helpers `headers`, `login`, `session`, `personal_session`, `drain`,
and `worker_request` import from `dbcalls_flows`, and the API tests' own helpers from
`api_support` (`services/api/tests/api_support.py`, on the path once
`dbcalls_flows` is imported): `add_member`, `seed_request`, and
`enrol_operator(w.client, w.container, email, OperatorRole.READ)` for the
operator plane's routes, with `OperatorRole` from `acme.om.context`.
The built-in `sweep` flow measures a pass at two tenant counts, so a
pass's cost per tenant is a measure, not a guess. A flow that raises is
named in the run's output and the next one runs; write each flow so it
stands on what `seed` made, not on another flow of the file.
