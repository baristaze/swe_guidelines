# ADR 0088: A provider's outage is a shared signal beside the breaker

**Status**: accepted (2026-10-08)

## Context

A breaker cuts a failing dependency off for the process that holds it.
Every other process that calls the same provider learns of the outage
on its own, from its own failed calls, and pays the timeouts again
before its breaker opens. The more processes call a provider, the more
each outage costs.

## Decision

- **The outage signal is an infra capability.**
  `OutageSignalInterface` is reached through
  `InfraInterface.get_outages()`. A process reads it before it calls a
  provider (`current`), marks it when its calls fail together (`mark`),
  and clears it when a call succeeds (`clear`).
- **A mark is keyed by the provider and the credential.** The
  credential is the org that holds it, `SYSTEM_SCOPE` for the
  platform's own, and the secret's name, never its value. One org's
  failing key stops no other org's calls, nor the platform's.
- **A mark carries a time to retry.** The entry lives until then. A
  mark that ends earlier than the one held leaves the held one, and a
  read past the retry time is no mark.
- **The signal follows the cache.** Processes on Valkey share one
  signal there, under the `outage` cache scope. A process whose cache
  is its own takes the null signal, which never marks: its breaker
  holds what its calls learned, and it has no one to tell.
- **It fails open.** A cache that cannot answer is a pair with no mark,
  and the caller calls. The provider's own failure still stops it.
- **A step that reads a mark parks.** It does not call. It parks its
  record on `provider_unavailable`
  ([ADR 0039](0039-long-running-work-is-a-record-a-guard-parks-and-a-bound-fails.md)),
  and the park lands a `WAKE_PARKED` row for the org's records parked
  on it, with the retry time as its `not_before`. The relay keys the
  item by the org, the reason, and the time, so every park on one mark
  lands one item. It waits in the queue until then and resumes the
  records staggered, and each woken record asks its guard again.

## Consequences

- One process's failed calls stop every process's calls on the same
  pair at once, for the retry time, with no call paid.
- A race between two marks may keep the earlier retry time of the two.
  That costs one early call to a provider still failing, which marks
  it again.
- A mark is lost when the cache is down or restarts. Each process then
  learns from its own calls, as it would with no signal.
- A record parked on a provider's outage wakes at the retry time
  whether or not any process has called since.
