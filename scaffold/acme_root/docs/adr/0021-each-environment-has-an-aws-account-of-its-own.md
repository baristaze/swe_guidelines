# ADR 0021: Each environment has an AWS account of its own

**Status**: accepted (2026-09-21). Supersedes [ADR 0017](0017-the-operators-principal-is-one-user-that-only-assumes.md).
Amends [ADR 0013](0013-each-environment-has-its-own-deploy-credential.md):
its three roles and their subjects stand, and they now live in two
accounts.

## Context

Acme has two AWS accounts in one organization, one for staging and one
for production, and IAM Identity Center signs people into both. Each
person holds a PowerUserAccess permission set in each account, plus an
administrator permission set that is assigned for a bootstrap run and
taken back afterwards.

The Terraform assumed one account holding both environments. A
`shared` root owned one registry, one state bucket, one zone, and the
three deploy roles. The line between staging and production was a set
of fences inside that account: denies by tag, by state prefix, by
record name. It also declared the `acme-operators` IAM user, whose
long-lived key the create script minted (ADR 0017).

An account per environment is AWS's own baseline, and it is the
conventional shape. The fences were the boundary. In the new layout
the account is.

## Decision

- **One account per environment.** `deployment/cloud/environments.json`
  names each one: the account id, the region (us-west-2), the
  administrator profile, the Identity Center profile an operator signs
  in with, and the two public names. The scripts and the Terraform read
  it, and nothing else holds an account id.
- **A bootstrap root per account.** `deployment/terraform/bootstrap/staging`
  and `bootstrap/prod` call `modules/account`. It declares the state
  bucket, the registry, the GitHub OIDC provider, the task boundary, the
  investigate role, the budget, the anomaly monitor, and one hosted zone
  per public name. Each root adds its own deploy roles: staging's one,
  and production's plan and apply pair. `shared` is gone.
- **Build once, replicate forward.** Staging builds. Its registry
  replicates every image into production's registry, digest for digest,
  and its artifacts bucket replicates every portal build into
  production's. The state bucket holds state and nothing else, so the
  one bucket staging may write in production's account holds no state.
  Production resolves and releases the copies in its own account, and
  reads nothing from staging's. Production's registry and bucket grant
  staging exactly these writes and nothing else.
- **No IAM user and no access key.** The investigate role trusts the
  account's Identity Center PowerUserAccess role, matched by ARN
  pattern. An operator's `acme-<env>-investigate` profile chains from
  their signed-in Identity Center profile. An agent works inside a
  session a person opened.
- **Variables per GitHub environment.** `staging`, `production-plan`,
  and `production` each hold `AWS_ROLE_ARN` and `TF_STATE_BUCKET` under
  the same names, with their own values. A staging job never holds a
  production value. The AWS side needs no repository secret.
- **The public names are delegated.** The domain is registered at
  Cloudflare, and a domain Cloudflare registers keeps Cloudflare's name
  servers. So each public name (`api.`, `app.`, per environment) is a
  hosted zone of its own in its environment's account, delegated by NS
  records. The create script writes them with a token that is held only
  while it runs.
- **No secret value in state.** The database password is generated
  for the run (an ephemeral value) and written write-only, to the
  database and to its URL secret. Neither the state nor a saved plan
  holds it, so the investigate role's read of the state reads no
  secret.
- **One branch per GitHub environment.** `staging` deploys from `main`,
  `production-plan` and `production` from `release`, set by the create
  script as deployment-branch policies. The roles' trust names the
  branch too.
- **The account is checked, twice.** Every provider pins
  `allowed_account_ids`. The create and nuke scripts clear any keys
  exported in the shell, run as the environment's administrator profile
  only, and ask STS who they are before every apply.

## Consequences

- Creation has an order. Staging goes first, then production, then
  staging again: S3 refuses a replication rule whose destination does
  not exist yet, so the second staging run is the one that turns
  replication on. Replication copies from the moment it is on, so the
  first release is a commit merged after that. The create script and
  its skill say so.
- Tearing staging down leaves production's images and portal builds in
  place, because they are copies in production's account.
- The tag and name fences inside each deploy role stay, and they match
  nothing in a correctly applied account. They are defense in depth for
  a root applied in the wrong account. The state-prefix and record-name
  denies that only made sense inside one account are removed.
- An agent can no longer sign in on its own. It needs the person's
  Identity Center session, which lasts hours and ends by itself. That
  is the loop with a person in it, which the operations posture asks
  for anyway.
- The destroy run needs the administrator permission set again. It is
  assigned for that run the same way it is assigned for a create.
- Two budgets and two anomaly monitors, one per account, where there
  was one. A cost report reads per account, and the environment tag
  still splits one within an account.
- The anomaly monitor needs Cost Explorer, which only the
  organization's management account turns on. Until it does, the
  create script leaves the monitor out and says so, and the budget
  stands alone. That is a deviation from "Cost Boundaries", which asks
  for both from the first apply. It closes on the first create run
  after Cost Explorer is on.
