# ADR 0021: Each environment has an AWS account of its own

**Status**: accepted (2026-09-28)

## Context

Staging and production are two environments. An account per
environment is AWS's own baseline and the guideline's shape (Cloud:
AWS): the account is the boundary, not a set of fences inside one
account. People sign in through IAM Identity Center, and an agent works
inside a session a person opened.

## Decision

- **One account per environment.** `deployment/cloud/environments.json`
  names each one: the account id, the region (`us-west-2`), the
  administrator profile, the Identity Center profile and role an
  operator signs in with, and the public names. The scripts and the
  Terraform read it, and nothing else holds an account id.
- **A bootstrap root per account.** `deployment/terraform/bootstrap/staging`
  and `bootstrap/prod` call `modules/account`, which declares the state
  bucket, the artifacts bucket, the registry, the GitHub OIDC provider,
  the task boundary, the investigate role, the budget, the anomaly
  monitor, the trail, and one hosted zone per public name. Each root
  adds its deploy roles
  ([ADR 0013](0013-each-environment-has-its-own-deploy-credential.md)):
  staging's build and deploy roles, production's plan and deploy pair.
- **Build once, replicate forward.** Staging builds. Its registry
  replicates every image into production's registry, digest for digest,
  and its artifacts bucket replicates every static build under `builds/`
  into production's. The state bucket holds state and nothing else.
  Production's registry and artifacts bucket grant staging exactly these
  writes, and production reads nothing from staging's account.
- **No IAM user and no access key.** The investigate role trusts the
  account's Identity Center role that `environments.json` names
  (`sso_role_name`), matched by ARN pattern. An operator's
  `acme-<env>-investigate` profile chains from their signed-in Identity
  Center profile.
- **Variables per GitHub environment.** `staging`, `production-plan`,
  and `production` each hold `AWS_ROLE_ARN` and `TF_STATE_BUCKET` under
  the same names, with their own values. A staging job never holds a
  production value, and no AWS secret is stored in GitHub.
- **The public names are delegated.** The domain is registered at
  Cloudflare, which keeps its own name servers. So each public name is a
  hosted zone in its environment's account, delegated by NS records. The
  create script writes them with a token held only while it runs.
- **No secret value in state.** The database passwords are generated for
  the run (ephemeral values) and written write-only, to the database and
  to its URL secrets. Neither the state nor a saved plan holds them, so
  the investigate role's read of the state reads no secret.
- **One branch per GitHub environment.** `staging` deploys from `main`,
  `production-plan` and `production` from `release`, set by the create
  script as deployment-branch policies. The roles' trust names the
  branch too.
- **The account is checked twice.** Every provider pins
  `allowed_account_ids`. The create and nuke scripts clear any keys
  exported in the shell, run as the environment's administrator profile
  only, and ask STS who they are before every apply.

## Consequences

- Creation has an order: staging, then production, then staging again.
  S3 refuses a replication rule whose destination does not exist, so
  the second staging run turns replication on. Replication copies from
  the moment it is on, so the first release is a commit merged after
  that. The create script and its skill say so.
- Tearing staging down leaves production's images and builds in place,
  since they are copies in production's account.
- The tag fences inside each deploy role match nothing in a correctly
  applied account. They are defense in depth for a root applied in the
  wrong account.
- An agent cannot sign in on its own. It needs a person's Identity
  Center session, which lasts hours and ends by itself.
- The create and the destroy need the administrator permission set,
  assigned for that run and taken back after.
- Each account has its own budget and anomaly monitor. A cost report
  reads per account, and the environment tag splits one within an
  account.
- The anomaly monitor needs Cost Explorer, which only the organization's
  management account turns on. While it is off, the create script
  leaves the monitor out (`anomaly_monitor = false`) and says so, and the
  budget stands alone. That is a deviation from Cost Boundaries, which
  asks for both, and it ends on the first create run after Cost Explorer
  is on.
