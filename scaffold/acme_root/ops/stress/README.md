# Stress tests

A stress test is the traffic generator at a higher profile, with a
duration, a ramp, and a target stated before the run. It drives the
edge, the API, the way real clients do, never the managers underneath,
and it reads the signals back when it ends.

## What a good one is

- **The edge is the target.** A session lists, adds, edits, completes,
  moves, and deletes tasks, reads the diary, and holds one socket that
  sees its own change. Every request goes through the gateway like a
  person's would.
- **A person signs in once.** The run signs each of its people in at
  the start, one person per worker, and every session that person
  drives reuses that token until the run signs them out at the end.
  The API counts every sign-in against a per-address rate limit, and a
  run signs all its people in from one address; a run that signed in
  per session measured that limit and not the application.
- **Its people sign in by the local sign-in.** They sign in by address
  alone, which only the local stack serves. A deployed environment
  signs people in through the identity provider, in a browser, so a run
  against staging or production is refused before it provisions
  anything, and says why.
- **Sessions are realistic.** Profiles differ by how many orgs and
  members take part, how many run at once, and how long they think
  between requests. `light` is a sanity run; `regular` is a normal day;
  `heavy` is a busy one; `stress` is past that.
- **Shared tasks conflict, and that is counted, not failed.** The
  people of an org share its open list, and a move writes the moved
  task alone. A write that meets a task changed under it, by a reminder
  or by the sweep's respace of a run of ranks, gets a 412, the API's
  optimistic concurrency. The session reads the task
  afresh and writes once more, as a client does, and a task found gone
  is dropped. The report counts both as conflicts; neither is an error
  or a failed session, and every other refusal still is.
- **It ramps.** Concurrency climbs over the ramp, so a knee shows as
  a knee and not as a wall.
- **It soaks.** The duration is long enough for leases to expire,
  sweeps to run, and the cache to fill.
- **The target is stated first, and it is an input.** A p95 and an
  error ratio, before the run, never after it. The scenario states
  one; a run may state its own instead, one number or both, and then
  the scenario's is a default. Either way the run prints the target it
  judged and where each half of it came from. A run without a target
  is a demonstration, not a test, and a target chosen once the numbers
  are in is not a target.
- **The target judges the working requests.** The p95 it holds is over
  the task routes, the event stream, and the socket's ticket: the
  requests a run makes hundreds of. The sign-in and the sign-out, one
  of each per person, are reported beside the verdict, named, with
  their own p95, and never mixed into the number the target holds, so
  the verdict does not move with how often the generator signs in. The
  error ratio is over every request, sign-in and sign-out included: a
  refused sign-in is a refusal whoever made it.
- **The signals are read back.** After the run, the platform's own
  request counter and its 5xx count are read from its telemetry, and
  the run fails when the platform counted no requests or more errors
  than the target allows. The p95 the verdict holds to the target is
  still the generator's; the platform's p95, the queue, and the worker
  outcomes are not read back yet. The error tracker is not one of these
  signals, and a deployed environment names none today: the run reads
  the counter out of the account either way, and says the error event
  leg was not read rather than calling it empty.

The numbers a system is held to are the team's. The shape is not.

## The scenario file

One YAML file per scenario under this folder.

| Field | Meaning |
|-------|---------|
| `name` | The scenario's name, used in the report. |
| `profile` | `light`, `regular`, `heavy`, or `stress`. |
| `duration_seconds` | How long the run lasts, ramp included. |
| `ramp_seconds` | How long concurrency takes to climb to the profile's. |
| `target.p95_ms` | The p95 latency, in milliseconds, the run's working requests must stay under, unless the run states its own with `--p95-ms`. |
| `target.error_ratio` | The share of requests that may fail, as a fraction, unless the run states its own with `--error-ratio`. |
| `weights` | The relative weight of each route in a session, by route name. Parsed, not applied yet: the generator's session shape is fixed. |

## The two scenarios

`smoke.yaml` is the local sanity run: thirty seconds at the light
profile against the compose stack, the smallest scenario there is. CI's
sanity run is `make traffic`, the same profile and duration with no
target. Locally, `--orgs 0` drives the seeded org; without it the run
provisions tenants through the operator plane and needs a provisioner
configured.

```bash
uv run acme-ops stress --scenario ops/stress/smoke.yaml --orgs 0
```

`staging.yaml` is the deployed one: three minutes at the regular
profile against a cloud environment, which provisions its own tenants,
so it takes a provisioner token and no `--orgs 0`. Its target is the
measured one and smoke's is not: a deployed environment misses smoke's
500 ms on a handful of requests, and holding it to a laptop's number
would fail every run for a reason that is not a defect.

```bash
uv run acme-ops stress --scenario ops/stress/staging.yaml --env staging
```

Both targets are illustrative. They say what these two environments
measured, at the size they were, with a little headroom. They do not
say what good performance is. A team adopting this sets its own, from
its own traffic and its own size.

## Asking a harder question of the same scenario

The scenario's target is a default. `--p95-ms` and `--error-ratio`
state one for a single run, and the run says where each half of the
pass mark came from, so a passing run can never be read as a claim it
did not make.

A wiring check is a small run with a generous target: does the
credential hold, do the tenants come and go, do the signals answer.

```bash
uv run acme-ops stress --scenario ops/stress/staging.yaml --env staging \
  --duration 60 --p95-ms 5000
```

A challenging run is the same scenario with a strict one. This is the
interesting run, and the one worth dispatching after a change.

```bash
uv run acme-ops stress --scenario ops/stress/staging.yaml --env staging \
  --p95-ms 1200
```

## The run on staging from CI

A deployed environment refuses the run today (see above): its people
have no way to sign in without a browser. What follows is the wiring
the run keeps for the day they do.

`.github/workflows/stress.yml` runs a scenario against staging on a
dispatch, never on a push. It takes a scenario name and, optionally, a
duration, a `p95_ms`, and an `error_ratio` that override the
scenario's; it knows no environment input, so production cannot be
chosen there at all. The two target inputs are the same two flags, so
the wiring check and the challenging run above are both a dispatch:

```bash
gh workflow run stress.yml --ref main -f scenario=staging \
  -f duration_seconds=60 -f p95_ms=5000
gh workflow run stress.yml --ref main -f scenario=staging -f p95_ms=1200
```

It grants the provisioner
`write` for the run, mints its token through the grant task, drives the
scenario, keeps the report as an artifact, fails on a missed target,
and disables the provisioner's entry again whatever the outcome.
`docs/runbooks/deploy.md` says how to dispatch it.

It is a reference shape for a stress test on staging, not a load test.
One small GitHub runner drives it, so the numbers are what one
generator can ask for from one address, bounded by that runner's CPU,
its one network path, and the per-address rate limit on sign-in. A
load test needs many generators, from many addresses, and that is not
what this is. What it is worth is the wiring: a credential that does
not stand, tenants made and removed, a target stated first, the
signals read back, and a verdict that names the target it held to.
