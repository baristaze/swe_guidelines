# ADR 0080: The optional audits count as well as read

**Status**: accepted (2026-09-28)

## Context

OPS-11 (Operations, Operational Skills) makes two audits optional,
`audit-credential-lifetimes` and `audit-provider-calls`, and says of
them: "Both read the code and the settings, and nothing else."

The credential audit states, for every credential on every channel,
what a check costs and the longest a revoked credential keeps working.
The provider audit states, for every flow, the external calls it makes
and how often. Read from the code, each is an estimate: a branch the
reader missed, a call a helper makes, or a check that reads one more
row does not show. `audit-database-calls` answers its own question by
counting, on a database the run makes and drops.

## Decision

Both optional audits read the code and the settings, and also count.
`audit-credential-lifetimes` counts what each check costs, and
`audit-provider-calls` counts the calls each built-in flow makes, as
`audit-database-calls` counts statements:

- on the local stack, in a database the run makes, migrates, and drops,
  with the tools under `ops/audit/`;
- with the identity provider's twin in place of the provider.

Each still holds no credential, reads no environment and no env file,
calls no real provider, and writes to no shared database. What cannot
be counted (boot, a credential no flow reaches) is read from the code.
Each row of the report says which it is: measured or read.

## Consequences

- A counted run needs the local stack up (`make infra-up`, with `make
  migrate` run once). A flow the counter cannot run is reported as not
  measured.
- A review that reads OPS-11 against
  `.agents/skills/audit-credential-lifetimes` or
  `.agents/skills/audit-provider-calls` finds a counted run and cites
  this record.
- The run's database is its own, named after the audit and the day, and
  a run never drops a database it did not make.
