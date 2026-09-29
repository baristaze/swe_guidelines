# Idempotency records

The durable outcome of a request the caller may send twice. This is one
of the kinds of thing [Acme is made of](../../../../README.md).

## What it holds

- **Record**: one per org, user, and the key the caller chose: a
  digest of the request, the id the create uses, the token of the
  attempt that holds it, and, once the request ran, its outcome. An
  operator's record lives under the system scope, keyed by the
  operator's identity.
- **Attempt**: one run of the request: the id its create uses and a
  token that names this run and when it began.

## What can happen

- **Begin.** Before a creating request runs, a pending record is
  written. A finished record replays its outcome and nothing runs. A
  record another attempt holds within its lease asks the caller to
  wait. The same key with a different request is refused.
- **Finish.** The outcome is stored, while the attempt still holds the
  record.
- **Release.** The attempt failed in a way a retry may change. The
  record keeps its digest and its id; the next retry re-arms it.
- **Sweep.** Finished and released records go after 24 hours, and a
  pending record nobody came back for after ten pending leases.

## The rules

- **One id, however many runs.** The create's id is minted with the
  record and kept across retries, so a rerun finds the row a failed run
  left.
- **The lease is read off the attempt**, never off a clock on the
  record, so two attempts are never ordered by a skewed clock.
- **A refusal is an outcome; a failure is not.** A 4xx is stored and
  replayed. A 5xx releases the record, and the retry runs again.
- **A secret is shown once.** A replay answers the stored row with the
  secret absent, and says it is a replay.
- **A lost attempt is refused.** Finish and release are conditional on
  the attempt's token.
- **One re-mint.** Creating an API key is the one create that issues a
  secret. A rerun whose first run stored no outcome mints a new secret
  on the same key, only while the record still holds this attempt
  ([ADR 0014](../../../../../docs/adr/0014-the-re-mint-reads-the-idempotency-marker.md)).

## How another namespace composes it

A namespace does not call it. The gateway wraps every creating `POST`
that carries an `Idempotency-Key`: it begins the record, hands the
handler the attempt, and finishes or releases it. The handler creates
under `attempt.target_id`, and the namespace's create treats an id it
already holds as the retry it is, answering the row as stored.
