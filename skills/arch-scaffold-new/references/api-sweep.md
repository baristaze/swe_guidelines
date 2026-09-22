# arch-scaffold-new: the API's sweep, under `--no-worker`

Step 4 of `arch-scaffold-new` reads this file, and only with
`--no-worker`. It is the spec of the one module that step writes,
`services/api/src/<root>/services/api/sweep.py`. The conventions in
`../_shared/scaffold-conventions.md` hold throughout, and what must
never be missed is in the skill's `Created` section and in step 4, not
here.

## Shape

The sweep is the loop of `arch-scaffold-worker` without the queue. Read
the `loop.py` row of
`${CLAUDE_SKILL_DIR}/../arch-scaffold-worker/references/worker.md` for
the shape each step takes, then write the steps below and no others: a
tree under `--no-worker` has no work queue, so there is no claim, no
lease, no heartbeat, and no requeue of stale items.

## Files

| File | Holds |
|------|-------|
| `services/api/src/<root>/services/api/sweep.py` | the sweep on a timer at `sweep_interval`, started in the lifespan after `start()` and cancelled before `close()`, every step idempotent and wrapped: the relay of what a crash left in the outbox, `relay_pending(rctx, limit)`, and the purge of done outbox rows, `purge_done(rctx)`; the purges of idempotency markers, socket tickets, and sessions, through the idempotency manager's `purge_markers(rctx)` and the tenancy manager's `purge_socket_tickets(rctx)` and `purge_sessions(rctx)`, which step 1 wrote, each one cross-tenant statement bounded by a batch size under the system scope; the purge of soft-deleted rows, once an entity composes the mixin, per service context, each context built by the tenancy manager's `service_contexts(rctx)`; and the outbox lag gauge `<root>_outbox_lag_seconds`, set from the relay's `read_lag(rctx)`, the age of `read_oldest_pending()` |
| `services/api/src/<root>/services/api/settings.py` | `sweep_interval`, which this step adds beside the service's other settings |
| `.env.example` | `<ROOT>_SWEEP_INTERVAL`, which `tests/test_settings.py` holds to the settings class |
