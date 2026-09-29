# ADR 0013: Each environment has its own deploy credential, and production has two

**Status**: accepted (2026-09-28)
## Context

DEL-38 and Cloud: AWS put the environment protection on the apply:
production waits for a person to approve the plan, and the approval
holds the apply. The guideline declares every cloud resource in
Terraform, IAM included. It says nothing about how a deploy credential
is scoped.

A merge to `main` deploys staging with no approval, by design. So the
credential a staging job holds must reach nothing of production's. And
the credential that writes production must be out of reach until the
approval.

## Decision

Three roles, each declared by its account's bootstrap root
([ADR 0021](0021-each-environment-has-an-aws-account-of-its-own.md)),
each trusted through `StringEquals` on one GitHub environment, one
branch, and the repository's and owner's ids:

| Role | Account | GitHub environment | Branch | May |
|------|---------|--------------------|--------|-----|
| `acme-deploy-staging` | staging | `staging` | `main` | apply staging |
| `acme-plan-production` | production | `production-plan` | `release` | read production and plan it, writing only its own lock and saved plan |
| `acme-deploy-production` | production | `production` | `release` | apply production |

A job presents `repo:<owner>@<owner id>/<name>@<repo id>:environment:<name>`
only when it declares that environment. So the required reviewer on
`production` gates the credential itself: a job that has not waited
there cannot mint the subject the applying role trusts.

Production takes two roles because the plan runs before the approval it
asks for. With one role, the credential that writes production would be
held before anyone approved anything.

Each role's policy stops at what its environment owns: names beginning
`acme-<environment>`, secrets under `acme/<environment>/`, log groups
under `/acme/<environment>/`, and its own keys in the state bucket. It
denies anything tagged as the other environment, what the bootstrap
root owns, any widening of the deploy roles and their trust, a new user
or access key, a role created without the `acme-task-boundary-<environment>`
permissions boundary, and the attachment of any policy but the graph's
own. So a compromised deploy cannot grant itself more.

Each GitHub environment holds one `AWS_ROLE_ARN` and one
`TF_STATE_BUCKET`, under the same names, with its own values.

## Consequences

`production-plan` is a GitHub environment that exists and carries no
reviewer: a reviewer there would hold the plan the reviewer is meant to
read. The deploy runbook says so.

The approval holds the write, not the read. The state holds no secret
value, but a refresh by `acme-plan-production` still reads production's
secrets through the secret store before anyone approves.

The first apply in a new account may meet a missing action as a plain
`AccessDenied` naming the call. The fix is to add that action to the
graph's policy, never to widen the role.

`ecs:RegisterTaskDefinition` and `ecs:DeregisterTaskDefinition` are the
two writes in the graph that cannot be fenced to an environment, and the
policy says so where it grants them. A revision in a foreign family is
inert until something runs it, and running one is fenced.
