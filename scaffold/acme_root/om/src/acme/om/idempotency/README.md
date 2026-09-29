# Idempotency records

The durable outcome of a request the caller may send twice. This is
one of the seven kinds of thing [Acme is made of](../../../../README.md).

## The nouns

- **Record**: for one org, one user, and one key the caller chose: a
  digest of the request, the id the create uses, the token of the
  attempt holding the record, and, once the request ran, its outcome.
  There is one record per (org, user, key).
- **Attempt**: one run of the request: the id its create uses and the
  token that names this run. An attempt that lost the record to a
  later one can no longer finish or release it.

## What can happen

- **Begin.** Before a creating request runs, a pending record is
  written. If a finished record already holds the key, its outcome is
  replayed and nothing runs. If another attempt holds it within its
  lease, the caller is told to wait. If the same key arrives with a
  different request, it is refused as a reused key.
- **Finish.** The outcome is stored on the record, conditional on the
  attempt still holding it.
- **Release.** The attempt failed in a way a retry may change. The
  attempt is cleared; the record, its digest, and its id stay.
- **Sweep.** Finished and released records are erased after the
  retention, twenty-four hours by default; a pending record nobody
  came back for goes after ten times the pending lease.

## The rules

- **One id, however many runs.** The id the create uses is minted
  before the record and kept across every retry, so a rerun finds the
  row the failed run left behind instead of making a second one.
- **The lease is read off the attempt.** The attempt's token carries
  the moment it began, so the pending lease is computed from the token
  and never from a clock on the record. Two attempts cannot be ordered
  wrongly by a skewed clock.
- **Held is taken over only past the lease; released is re-armed at
  once.** Of two retries racing for a released record, exactly one
  re-arms it and the other waits.
- **A refusal is an outcome; a failure is not.** A refusal (a 4xx) is
  stored and replayed. A failure (a 5xx) releases the record and the
  retry runs again.
- **A secret is shown once.** The stored outcome of a create that
  issued a secret carries the secret absent. A replay answers with the
  row, the secret missing, and says it is a replay.
- **A lost attempt is refused.** Finish and release are conditional on
  the attempt token, like a worker's lease, so an attempt that ran too
  long cannot overwrite what its retry produced.
- **The one exception.** Creating an API key is the one create that
  issues a secret, and the first secret reached nobody when the record
  stored no outcome. Its rerun mints a new secret on the same key, in
  the same write that would have inserted it, and only while the record
  still holds this attempt. A revoked key is never re-minted.
