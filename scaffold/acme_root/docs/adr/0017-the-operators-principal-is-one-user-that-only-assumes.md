# ADR 0017: The operators' principal is one IAM user that can only assume the read-only roles

**Status**: superseded (2026-09-21) by [ADR 0021](0021-each-environment-has-an-aws-account-of-its-own.md).
Each environment moved to an AWS account of its own, and people sign
in through IAM Identity Center; the `acme-operators` user and its key
are gone. The record stays for the interval it covers.
Amended (2026-09-22): only the grant job writes the operator
allowlist; see the note at the end.

## Context

"Operations" > "Operator Roles" says the principal an agent holds is
one user whose only permission is to assume the read-only roles, and
that moving to the cloud's identity center when the team grows changes
the roles' trust policy and nothing below it. It names the shape, not
the AWS mechanism.

AWS recommends IAM Identity Center for people: short-lived credentials
from a browser sign-in, permission sets instead of users. It is the
better practice, and it costs an organization, an identity source, and
a sign-in that an agent cannot complete on its own. Acme is operated
by two people and their agents, and the agents are the ones that hold
the credential most of the day.

## Decision

The `shared` root declares one IAM user, `acme-operators`, with one
inline policy: `sts:AssumeRole` on `arn:aws:iam::<account>:role/acme-investigate-*`.
It holds no other permission, so the widest thing a leaked key can do
is read. Its access key is not in Terraform: `scripts/cloud_create.sh`
mints it once under the administrator profile, writes it into
`~/.aws/credentials` as the `acme-operators` profile, and writes the
two role profiles that chain from it, `acme-staging-investigate` and
`acme-production-investigate`. The investigate roles trust that user
and the arns in `operator_principal_arns`.

The supporter holds no cloud role of its own. It is the investigate
profile plus an operator identity whose allowlist entry is read, and
the tenant's rows are read through the operator plane (ADR 0018).

## Consequences

- A person joining the team is added to `operator_principal_arns`
  through a pull request, or given the same user's key. Neither widens
  what an operator can do.
- Moving to Identity Center later is a change to the investigate
  roles' trust policy (a permission set assumes them) and the removal
  of the user. The profiles, the skills, and the runbooks keep their
  names because they name the role profiles, not the source.
- The key is a long-lived credential and rotation is by hand: mint a
  new one, rewrite the profile, delete the old. That is the price of
  an agent that signs in with no browser, and it is bounded by the
  user holding nothing but `AssumeRole`.

## Amended: only the grant job writes the allowlist

The supporter's operator identity is no longer a password in a file.
An operator's allowlist entry, in every deployed environment, is
written by one path: `acme-api grant-operator`, run as the grant task
by `grant-operator.yml` (ADR 0022). No person's cloud profile and no
operator-plane route writes it. What the supporter holds is a `read`
operator token of at most an hour, as ADR 0018 now says.
