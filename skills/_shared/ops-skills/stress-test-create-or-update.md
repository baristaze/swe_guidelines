---
name: stress-test-create-or-update
description: "Write or edit one stress test scenario of the platform, ops/stress/<name>.yaml: the profile, the duration, the ramp, the soak, the target p95 and error ratio, and the routes it weights. States what a good stress test has and refuses to write one without a target. Writes a file and nothing else; a run is stress-test-run."
allowed-tools: Read, Grep, Glob, Write, Edit
---

# stress-test-create-or-update

A stress test is the traffic generator at a higher profile with a
duration and a target, and the target is written before the run. This
skill writes the scenario file. It runs nothing.

## Input

`<name> [--env local|staging|production] [--profile heavy|stress] [--duration 600] [--ramp 60] [--soak 300] [--p95 <ms>] [--error-ratio <ratio>] [--weight <route>=<n> ...]`

`<name>` is required and names `ops/stress/<name>.yaml`; when the
file exists the skill edits it and keeps every field not given. The
target (`--p95` and `--error-ratio`) is required for a new scenario;
ask for it and write nothing until it is given. `--env` is the
environment the scenario is meant for and is only a note in the
file; the run names the environment. Every scenario runs against
`local` with no cloud, so it is testable on the compose stack.

## Role and credential

None. The skill reads and writes files in the repository and holds
no profile and no env file.

## Procedure

1. Read `ops/stress/README.md` and every existing scenario under
   `ops/stress/`, so the new one matches the house shape and reuses
   the route names the generator knows (they are the generator's
   session steps, not raw paths).
2. State the target before anything else. A good stress test has:
   - the edge as the target: sessions through the API's public routes
     and the socket, never a manager or a table;
   - realistic sessions: the generator's own session shape, weighted
     by route, not one route hammered;
   - a ramp: concurrency rises over `ramp` seconds to the profile's
     number, so the first second is not the verdict;
   - a soak: the profile held for `soak` seconds after the ramp, so
     the pool, the queue, and the cache settle;
   - a target stated before the run: the p95 in milliseconds and the
     error ratio, both numbers the team chose, and the scenario says
     who and when in `target.note`;
   - the signals read back after: the run reads the request counter,
     the p95, the worker outcomes, and the error count through the
     signals API, and the pass or fail is against those, never
     against the generator's own clock alone.
3. Write or edit `ops/stress/<name>.yaml`:

   ```yaml
   name: <name>
   env: <env>
   profile: <heavy | stress>
   duration_s: <duration>
   ramp_s: <ramp>
   soak_s: <soak>
   target:
     p95_ms: <ms>
     error_ratio: <ratio>
     note: "<who chose these numbers and when>"
   routes:
     - step: <session step name>
       weight: <n>
   ```

   `duration_s` is at least `ramp_s + soak_s`. Weights are integers
   and sum to any number; the generator normalizes them.
4. Read it back and check every field is set and the total holds.
   Say in the output that a real run is the platform developer's
   choice and that the run skill is `stress-test-run`.

## What it never does

- No run: the generator is not started, no traffic goes anywhere.
- No write outside `ops/stress/`.
- No target invented: a scenario without a p95 and an error ratio
  the person gave is not written.
- No cloud, no credential, no secret.

## Output

```markdown
# Stress scenario: ops/stress/<name>.yaml (<created | updated>)

**Profile.** <profile>, <duration>s (<ramp>s ramp, <soak>s soak)
**Target.** p95 <ms> ms, error ratio <ratio>, chosen by <note>

## Routes

- <step>: weight <n>

## Next

- `stress-test-run <name> --env local` proves the wiring; a run against
  a cloud environment is the platform developer's choice.
```
