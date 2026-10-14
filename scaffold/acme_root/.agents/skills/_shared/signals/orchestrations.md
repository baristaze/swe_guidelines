# Signals: orchestrations

The long-running records: one kind per process that outlives a
request, each advanced one step at a time on the work queue.

## Kinds

- `noop`: the mechanism's own kind, whose record succeeds at once
  (`noop_succeeded`). A product's kinds are in its own namespaces'
  files.
- `ORCHESTRATION`: the work kind every step of every record rides.
- `WAKE_PARKED`: the work kind that wakes an org's records once the
  reason they parked for is gone.

## Outcomes

- `orchestrations`: one outcome per kind and state, `<kind>_<state>`:
  `_running` when a record starts, `_parked` when a step waits on a
  provider that did not answer, `_resumed`, `_succeeded`, `_failed`,
  and `_cancelled`, a person's end. A parked record is woken by the
  event that clears its reason (`WAKE_PARKED`) or by a person; one that
  stays parked is read beside the provider's lines. A running record
  whose `_succeeded` never follows is a step the queue keeps retrying:
  read the worker's `failed` outcomes beside it.

## Log lines

- `<kind> <id> in org <org> failed: defect ...` at `ERROR` in
  `/acme/<env>/maintenance`: a `_failed` that is `defect`, a step that
  still failed on its item's last attempt, with the record's id and the
  org's.

## Report

```markdown
- Orchestrations: <kind> <started, parked, succeeded, failed, cancelled> per kind, defects <record and org ids, or none>
```
