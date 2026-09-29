# ADR 0013: Each environment has its own deploy credential, and production has two

**Status**: accepted (2026-09-20), amended by [ADR 0021](0021-each-environment-has-an-aws-account-of-its-own.md)
(2026-09-21): the three roles and their subjects stand. Staging's role
lives in staging's account and production's two in production's, each
declared by that account's bootstrap root instead of `shared`. The
three repository variables became one `AWS_ROLE_ARN` per GitHub
environment.

## Context

DEL-38 and "Cloud: AWS" put the environment protection on the apply:
production waits for a person to approve the plan, and the approval
holds the apply. The guideline says every cloud resource, IAM included,
is declared in Terraform, and it says nothing about how a deploy
credential is scoped; a threat model and supply-chain rules are named
as outside its scope.

Acme read that literally and took the weakest shape it allows. One
role, `acme-deploy`, carried `AdministratorAccess`, and its trust
matched three subjects at once with `StringLike`: staging's
environment, production's, and the `release` branch. Both workflows
assumed it through one repository variable. So a merge to `main`, which
deploys staging with no approval by design, ran under a credential that
owned production's state, its secrets and its database. And in
`deploy-production.yml` the plan job declared no environment at all, so
it held that credential with no gate: the approval held the apply step
while the credential was already out.

That is a privilege boundary, and a reviewer reading the guideline
would not find it, because the guideline leaves it to the project.

## Decision

Three roles, declared in Terraform in the `shared` root, each trusted
through `StringEquals` on one GitHub environment and one branch:

- `acme-deploy-staging`, the `staging` environment on `main`;
- `acme-plan-production`, the `production-plan` environment on
  `release`, read-only apart from its own lock and its own saved plan;
- `acme-deploy-production`, the `production` environment on `release`,
  which writes.

A job emits `repo:O/R:environment:<name>` only when it declares that
environment, so the required reviewer on `production` gates the
**credential**: a job that has not waited there cannot mint the subject
the writing role trusts. Each role carries a permissions boundary that
denies anything tagged with the other environment, the other's state
prefix, any widening of the deploy credentials themselves, and the
attachment of any policy but the graph's own, so the one thing a
compromised deploy cannot do is grant itself more.

Production takes two roles rather than one because the plan runs before
the approval it is the subject of. With one role, production's
credential would again be held outside the gate.

## Consequences

Three repository variables replace one, and `production-plan` is a
GitHub environment that must exist and must carry no reviewer, because
a reviewer there would hold the plan the reviewer is meant to read. The
runbook says so.

The approval holds the write, not the read. `acme-plan-production`
reads production's state, and the state holds the database master
password in clear, so the pre-approval credential sees production's
secrets. Closing that means encrypting values in state, which is its
own piece of work, and this record does not claim otherwise.

The policies have never been evaluated by AWS, because no account
exists. The first real apply is expected to surface a missing action or
two, as a plain `AccessDenied` naming the call; the fix is to add that
action to the graph's policy, not to widen the role. `RegisterTaskDefinition`
is the one write in the graph that cannot be fenced to an environment,
and the code says so where it is granted: a revision registered in a
foreign family is inert until something runs it, and running one is
fenced.
