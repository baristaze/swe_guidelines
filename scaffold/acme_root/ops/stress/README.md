# Stress tests

A stress test is the traffic generator at a profile, with a duration, a
ramp, and a target stated before the run. It drives the edge the way a
client does and reads the signals back when it ends.

## The session

Each person signs in once per run, by the local sign-in, and out once at
the end. A session opens the realtime channel, reads `/v1/me`, lists the
members, writes the person's display name, creates an API key and revokes
it, reads the events, and waits to see its own change on the socket. A
person thinks between steps.

## The target

A p95 and an error ratio, stated in the file or on the run with
`--p95-ms` and `--error-ratio`. The p95 judges the working requests; the
sign-ins and sign-outs are reported beside it. After the run the
platform's own request and 5xx counts are read back, and the run fails
when they miss the target.

## The file

| Field | Meaning |
|-------|---------|
| `name` | The scenario's name, in the report. |
| `profile` | `light`, `regular`, `heavy`, or `stress`. |
| `duration_seconds`, `ramp_seconds` | How long the run lasts, and how long concurrency takes to climb. |
| `target.p95_ms`, `target.error_ratio` | The pass mark. |
| `weights` | The session's steps by name. Parsed, not applied: the session is fixed. |

## The scenarios

`smoke.yaml`: thirty seconds at `light` on the local stack, p95 500 ms.

```bash
uv run acme-ops stress --scenario ops/stress/smoke.yaml --orgs 0
```

`staging.yaml`: three minutes at `regular`, p95 3000 ms, dispatched by
`.github/workflows/stress.yml`. A deployed environment signs people in
through a browser, so the run refuses it before it provisions anything.

Both targets are illustrative. A team sets its own.
