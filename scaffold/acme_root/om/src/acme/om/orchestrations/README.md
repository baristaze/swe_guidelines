# Orchestrations

Work that takes minutes, kept as a record and done a step at a time.
This is one of the kinds of thing [Acme is made of](../../../../README.md).

## What it holds

- **Orchestration**: one long job of an org: its kind, its input, a
  status, and a cursor where the next step starts. It counts what its
  steps applied and skipped, names the first twenty skipped rows with
  a reason, and carries a version every write names.
- **Kind**: what the job is, with the shape of its input. The core has
  one, `noop`, which steps through a count and changes nothing else. A
  product replaces it with its own kinds.
- **Status**: running, parked, succeeded, or failed.
- **Park reason**: why a parked record waits, and so what wakes it. The
  core has one, `provider_unavailable`.
- **Fail reason**: why a record ended unfinished: a bound of its input,
  or `defect`, a step that still failed on its last attempt.
- **Period**: for a record kept per period, such as a day, the org, the
  kind, and the period are its unique key.

## What can happen

- **Start.** The record lands running at its first cursor, with the
  work item of its first step, in one commit.
- **Step.** A worker claims the step's work item and does one batch.
  The batch's effect, the record's next cursor, and the next step's
  work item land in one commit.
- **Park.** A guard stops the record at its cursor and keeps what it
  made, in the step's own commit.
- **Wake.** A parked record runs again from its cursor when the reason
  clears (a `WAKE_PARKED` work item wakes every record of the org
  parked for it, a couple of seconds apart), or when a person resumes it.
- **Succeed** after the last step, or **fail** on a bound.
- **Sweep.** A settled record goes thirty days later. A parked or a
  running one is kept.

## The rules

- **A guard parks, a bound fails.** A limit that can clear keeps the
  record resumable; a limit of the input ends it. Nothing a record made
  is undone by its end.
- **Anything else is the queue's.** A store that does not answer makes
  the step raise, and the work queue retries it from the last cursor.
  On the last attempt the record fails as `defect`.
- **Every write names the version it read.** A worker that held an item
  past its lease is refused, and its batch lands nothing.
- **A step runs twice and does once.** What a step makes takes ids
  derived from the record and the row, so a second run meets them.
- **A woken record asks its guard again** before its next step.
- **Its push names its version**
  ([ADR 0061](../../../../../docs/adr/0061-a-push-names-the-version-it-wrote.md)).

## How another namespace composes it

A product adds a kind to `OrchestrationKind` and its input shape to
`ORCHESTRATION_INPUTS`. The step lives in the namespace whose rows it
changes: it writes its rows and the record's `Step` in one commit, the
record's rows from `steps.step_rows` among them. The worker's
orchestration handler maps the kind to that step. A guard adds a
`ParkReason`, and whatever clears it asks for `WAKE_PARKED`
([ADR 0039](../../../../../docs/adr/0039-long-running-work-is-a-record-a-guard-parks-and-a-bound-fails.md)).
