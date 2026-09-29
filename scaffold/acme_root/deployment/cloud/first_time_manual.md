# AWS First-Time Setup Manual

This is the bootstrap pattern used for Acme. The goal is to keep projects and environments isolated, avoid daily root usage, and use temporary SSO credentials instead of long-lived AWS access keys.

## 1. Target structure

```text
AWS Organization
|
+-- Management account
|   +-- AWS Organizations
|   +-- IAM Identity Center
|   +-- Billing
|   +-- No application workloads
|
+-- Acme OU
    +-- acme-staging AWS account
    +-- acme-prod AWS account
```

The AWS account is the main isolation boundary. Staging and production should therefore be separate AWS accounts.

## 2. Root account

Use the AWS root account only for initial bootstrap and operations that explicitly require root.

Do:

- Enable MFA on root.
- Do not create root access keys.
- Do not use root for normal AWS work.
- Keep application resources out of the management account.

## 3. Create AWS Organization

From the original AWS account:

1. Open **AWS Organizations**.
2. Create an organization.
3. Use the default **All features** mode.

The original account becomes the **management account**.

## 4. Enable IAM Identity Center

1. Open **IAM Identity Center**.
2. Enable an **Organization instance**.
3. Multi-account permissions should be enabled.
4. Record the AWS Access Portal URL.

For Acme, IAM Identity Center is in:

```text
us-east-2
```

This does not determine the region where application infrastructure runs.

## 5. Create the normal human user

Create an IAM Identity Center user such as:

```text
first.last
```

This is separate from the AWS root user, even if both use the same email address.

Do not encode roles or projects in the username.

Require MFA for every Identity Center sign-in: **Settings / Authentication / Multi-factor authentication**, set to prompt on every sign-in, and register an authenticator for the user. The admin permission sets are only as strong as that sign-in.

## 6. Create organization admin access

Create group:

```text
OrgAdmins
```

Add:

```text
first.last
```

Create or use permission set:

```text
AdministratorAccess
Session duration: 1 hour
```

Assign:

```text
Management account
  OrgAdmins -> AdministratorAccess
```

After this works through the AWS Access Portal, stop using root for normal administration.

## 7. Create Acme accounts

Create an OU:

```text
Acme
```

Inside it create two AWS accounts:

```text
acme-staging
acme-prod
```

Each AWS account requires a unique email address. Plus-style aliases are convenient, for example:

```text
<you>+aws-acme-staging@<mail domain>
<you>+aws-acme-prod@<mail domain>
```

These are the accounts' root addresses. Keep the real ones out of the repository.

Keep the default Organizations access role name:

```text
OrganizationAccountAccessRole
```

## 8. Centralize member-account root management

In AWS Organizations / IAM, enable:

- Root credentials management
- Privileged root actions in member accounts

A delegated administrator is not necessary for this small setup.

## 8a. Turn on Cost Explorer and Budgets for the member accounts

A member account can make neither a budget nor an anomaly monitor until the management account turns cost management on for the organization. Do this before the first `scripts/cloud_create.sh`, signed in to the **management account** (not a Acme account):

0. Sign in to the management account as its **root user**, open **Account** (the account menu, top right), find **IAM user and role access to Billing information**, choose **Edit**, tick **Activate IAM Access**, and choose **Update**. Until the root user does this, every role in the account is refused the billing and cost pages, `AdministratorAccess` included, with `You don't have permission to perform the following operation on the AWS Cost Management console: ce:GetPreferences`. Sign out of root, and do the rest as the Identity Center administrator.
1. Open **Billing and Cost Management** and choose **Cost Explorer**, then **Launch Cost Explorer** if it has not been launched. This turns on the cost data every member account reads.
2. Open **Budgets** once, so the service is turned on for the organization.
3. Open **Cost Management preferences** and, under **Linked account access**, allow the member accounts to see their own cost data. Save.

It can take up to a day before a member account answers. Check each Acme account with its administrator profile:

```bash
aws budgets describe-budgets --account-id 111111111111 --max-results 1 --profile acme-staging-admin
aws ce get-anomaly-monitors --region us-east-1 --max-results 1 --profile acme-staging-admin
aws budgets describe-budgets --account-id 222222222222 --max-results 1 --profile acme-prod-admin
aws ce get-anomaly-monitors --region us-east-1 --max-results 1 --profile acme-prod-admin
```

Each answers without an error once it is on (Budgets prints nothing while the account has no budget). The errors before then:

- `Account <id> is a linked account. To enable budgets for your account, ask the payer account to enable budgets first.`: Budgets is not on yet.
- `User not enabled for cost explorer access`: Cost Explorer is not on yet.

The budget is required: `scripts/cloud_create.sh` asks Budgets before it applies and refuses until it answers. The anomaly monitor is not: until Cost Explorer answers, the script leaves the monitor out and says so, and a later run adds it.

A first apply that stopped part way leaves its state in `deployment/terraform/bootstrap/<staging|prod>/terraform.tfstate`. Keep that file: it is the only record of what that apply made, and the next run of the script applies against it and then moves it into the state bucket.

## 9. Create Acme access groups

Create Identity Center groups:

```text
AcmePowerUsers
AcmeReaders
AcmeBootstrapAdmins
```

Add `first.last` to all three during initial setup.

## 10. Create permission sets

Normal development access:

```text
Permission set: PowerUserAccess
AWS managed policy: PowerUserAccess
Session duration: 12 hours
```

Read-only access, the default in production and for inspection in staging:

```text
Permission set: ReadOnlyAccess
AWS managed policy: ReadOnlyAccess
Inline policy: sts:AssumeRole on arn:aws:iam::*:role/acme-investigate-*
Session duration: 12 hours
```

A person reads production and hands an agent the investigate role; every change to production is a pull request and a release. The investigate role trusts the account, narrowed by a condition to this permission set's role, so the sign-in itself must be allowed to call `sts:AssumeRole`. The managed `ReadOnlyAccess` policy does not allow it. The inline policy is what lets the `acme-production-investigate` profile chain from this sign-in; without it the chain answers `AccessDenied`.

Create it as a predefined permission set, then add the inline policy:

1. In the management account, open **IAM Identity Center** (not IAM), then **Permission sets** → **Create permission set** → **Predefined permission set** → `ReadOnlyAccess`. The name of the set is `ReadOnlyAccess`, the name `environments.json` reads.
2. Open the set, then **Inline policy** → **Create** (or **Edit**). The editor opens on an empty template with `"Action": []` and `"Resource": []`; replace all of it with:

   ```json
   {
       "Version": "2012-10-17",
       "Statement": [
           {
               "Sid": "ChainToTheInvestigateRole",
               "Effect": "Allow",
               "Action": "sts:AssumeRole",
               "Resource": "arn:aws:iam::*:role/acme-investigate-*"
           }
       ]
   }
   ```

3. Save, and accept the prompt to re-provision the accounts the set is assigned to.

The page under **IAM** → **Policies** → `ReadOnlyAccess` is the AWS managed policy the set attaches. It is not editable and is not where the inline policy goes. The inline policy grants one thing: assuming the investigate roles, which themselves change nothing. Section 16 checks that it arrived.

Temporary bootstrap access:

```text
Permission set: AcmeBootstrapAdmin
AWS managed policy: AdministratorAccess
Session duration: 1 hour
```

`PowerUserAccess` is insufficient for some initial IAM operations, which is why the temporary bootstrap permission exists.

## 11. Assign access to Acme accounts

Assign to `acme-staging`:

```text
AcmePowerUsers
  -> PowerUserAccess

AcmeReaders
  -> ReadOnlyAccess

AcmeBootstrapAdmins
  -> AcmeBootstrapAdmin
```

Assign to `acme-prod`:

```text
AcmeReaders
  -> ReadOnlyAccess

AcmePowerUsers
  -> PowerUserAccess

AcmeBootstrapAdmins
  -> AcmeBootstrapAdmin
```

`PowerUserAccess` in production is for a change that is explicitly authorized, never for everyday work.

The investigate role in each account trusts the permission set `deployment/cloud/environments.json` names for it (`sso_role_name`): `PowerUserAccess` in staging, `ReadOnlyAccess` in production.

The management account should only receive organization-level admin access:

```text
OrgAdmins
  -> AdministratorAccess
```

Do not assign `AcmePowerUsers` to the management account.

## 12. Expected AWS Access Portal

The user should see approximately:

```text
Management account
  AdministratorAccess

acme-staging
  PowerUserAccess
  ReadOnlyAccess
  AcmeBootstrapAdmin

acme-prod
  ReadOnlyAccess
  PowerUserAccess
  AcmeBootstrapAdmin
```

## 13. Install AWS CLI

On macOS with Homebrew:

```bash
brew install awscli
aws --version
```

## 14. Configure AWS CLI with SSO

Do not manually create or copy long-lived access keys.

Run:

```bash
aws configure sso
```

Use:

```text
SSO session name: acme
SSO start URL: <AWS Access Portal URL>
SSO region: us-east-2
SSO registration scopes: sso:account:access
Default client region: us-west-2
```

For the first profile choose:

```text
Account: acme-staging
Role: AcmeBootstrapAdmin
Profile: acme-staging-admin
```

## 15. Recommended local profiles

Create these six profiles, all sharing the same `acme` SSO session:

```text
acme-staging
acme-staging-ro
acme-staging-admin
acme-prod
acme-prod-power
acme-prod-admin
```

Current Acme account IDs:

```text
acme-staging: 111111111111
acme-prod:    222222222222
```

Each profile's role, and what it is for:

```text
acme-staging     -> PowerUserAccess      normal staging work
acme-staging-ro  -> ReadOnlyAccess       inspection only
acme-staging-admin -> AcmeBootstrapAdmin  temporary bootstrap and IAM-heavy setup only
acme-prod        -> ReadOnlyAccess       the default production profile, inspection only
acme-prod-power  -> PowerUserAccess      only when explicitly authorized
acme-prod-admin  -> AcmeBootstrapAdmin  emergency or bootstrap only, never unless explicitly authorized
```

`acme-prod` is read-only on purpose: the profile a command reaches production with by default can change nothing.

Default workload region:

```text
us-west-2
```

## 16. Verify profiles

```bash
aws sts get-caller-identity --profile acme-staging
aws sts get-caller-identity --profile acme-staging-ro
aws sts get-caller-identity --profile acme-staging-admin
aws sts get-caller-identity --profile acme-prod
aws sts get-caller-identity --profile acme-prod-power
aws sts get-caller-identity --profile acme-prod-admin
```

Expected account IDs:

```text
acme-staging*  -> 111111111111
acme-prod*     -> 222222222222
```

Check that the read-only sign-in may chain to the investigate role. The role name in the output carries the permission set's name, `AWSReservedSSO_ReadOnlyAccess_<suffix>`:

```bash
aws iam list-roles --profile acme-prod \
  --path-prefix /aws-reserved/sso.amazonaws.com/ \
  --query 'Roles[?contains(RoleName, `ReadOnlyAccess`)].RoleName' --output text
aws iam list-role-policies --profile acme-prod --role-name <that role>
```

The second command lists `AwsSSOInlinePolicy` once the inline policy of section 10 is on the permission set. An empty list means the investigate profile cannot chain yet.

## 17. Rules for agents and automation

Use explicit profiles. Do not rely on a global/default AWS profile.

Normal work by a person:

```bash
AWS_PROFILE=acme-staging <command>
```

Normal work by an agent runs under the read-only investigate profile the create run writes, chained from the person's signed-in session. The ops skills refuse the PowerUser and admin profiles as wider than they need:

```bash
AWS_PROFILE=acme-staging-investigate <command>
```

Initial bootstrap only:

```bash
AWS_PROFILE=acme-staging-admin <command>
```

Rules:

- Start with staging.
- Never deploy application workloads into the management account.
- Never create long-lived AWS access keys unless there is an exceptional documented requirement.
- Use Infrastructure as Code for persistent infrastructure.
- Prefer the repository's existing IaC convention; otherwise use Terraform.
- Verify `sts get-caller-identity` before every apply/deploy.
- Verify the account ID matches the intended environment.
- Never infer production from a default profile.
- Do not deploy to production until staging is reproducible and reviewed.
- Use GitHub Actions OIDC for CI/CD instead of static AWS secrets.
- Use bootstrap admin only when normal PowerUser access cannot perform the required IAM/bootstrap operation.

## 18. Desired steady state

Human interactive access:

```text
AWS Access Portal
  -> IAM Identity Center
  -> temporary account role
```

Local agent access:

```text
aws sso login
  -> named AWS_PROFILE
  -> temporary credentials
```

CI/CD access:

```text
GitHub Actions
  -> OIDC
  -> dedicated AWS deployment role
```

Avoid this pattern:

```text
Permanent AWS_ACCESS_KEY_ID
Permanent AWS_SECRET_ACCESS_KEY
```

If the shell profile exports either, remove the export. Terraform prefers exported keys to `--profile`, so an exported key silently decides which account a command reaches. The create and nuke scripts clear them for their own run and say so.

## 18a. Cloudflare token for the delegation and the site's records

`acme.example` is registered at Cloudflare, and its zone stays there. The create run writes into it with an API token: create one with **Zone / DNS / Edit** on the `acme.example` zone only, and pass it as an environment variable for the run:

```bash
export CLOUDFLARE_API_TOKEN=<token>
```

Nothing else needs it; CI never does. Making the token and running the create run are the manual steps. Everything the run writes at Cloudflare, it writes itself, and every write is safe to repeat:

- **The API and the portal** (`api.`, `app.`, each under `staging.` for staging) are delegated to Route 53: one NS record per name server of the zone the bootstrap root made, and any stale NS record at that name deleted.
- **The company site** is `acme.example` in production and `staging.acme.example` in staging. Neither can be delegated. The apex is the Cloudflare zone's own apex, where an NS record cannot sit, and a delegation of `staging.acme.example` would hide the `app.staging` and `api.staging` delegations beneath it. So the site's names are records in the Cloudflare zone, **DNS only** (not proxied), so CloudFront serves TLS with its own certificate:
  1. The bootstrap root requests the site's certificate in us-east-1. The run writes its validation record at Cloudflare as a CNAME, then waits until ACM has issued it (step 3b).
  2. A deploy makes the site's distribution, with the issued certificate. The environment root finds the certificate by the site's name.
  3. The next create run finds the distribution by its alias and writes the site's name as a CNAME to the distribution's domain (step 3c). At the apex Cloudflare flattens the CNAME into addresses. Until then the step says there is no distribution yet.

  If the site's name already holds an `A`, `AAAA`, or `NS` record at Cloudflare, the run refuses and names it, since what that record serves is a person's call. Remove it by hand if the site is to serve there, then run again.

The site is optional, so none of this holds up a deploy. Until the environment's `SITE_DOMAIN_NAME` is set and the site's certificate is issued, a deploy (and a release, and the fast rollback) plans, applies, and publishes everything else exactly as always, leaves the site out, and says so in the run's summary.

So the site takes two create runs, and a deploy between them. When the site comes to an environment that already runs, as it did to staging:

1. The change that brings the site merges. Its deploy leaves the site out and says why.
2. The create run (`scripts/cloud_create.sh staging` under `acme-staging-admin`): the certificate, its validation record, the wait until it is issued, the grants to keep and replicate the site's builds, and `SITE_DOMAIN_NAME` on the GitHub environments. It dispatches a deploy of `main`.
3. That deploy, once green, has made the site's distribution and published the site.
4. The create run again: step 3c writes the site's CNAME, and the site answers at its name.

For production the same four happen around releases: the change is already released, then the create run (`scripts/cloud_create.sh production` under `acme-prod-admin`), then a release, then the create run again. Production's run also lets staging's replication write the site's builds into production's artifacts bucket, so that release takes a commit staging built after it, the same rule as the first release. A new environment follows its first-time order (section 19) and runs the create run once more after its first green deploy, for the CNAME.

The records the Terraform does not hold stay at Cloudflare when an environment is destroyed; the nuke lists them.

## 19. Bootstrap, then hand the admin back

The bootstrap is the `ops-cloud-deployment-create` skill, which runs `scripts/cloud_create.sh`, one account at a time, dry run first:

1. `--env staging` under `acme-staging-admin`: staging's account and its first deploy.
2. `--env production` under `acme-prod-admin`: production's account, no release yet.
3. `--env staging` again: it finds production's artifacts bucket and turns replication on.

The first release is a commit merged to `main` after step 3.

After the deploy roles work, remove `first.last` from `AcmeBootstrapAdmins`, so no one holds `AcmeBootstrapAdmin` day to day. Add it back for a run that needs it, such as a destroy or a change to a bootstrap root, and remove it again after.

A bootstrap root is applied by a person before the change that needs it merges. When a pull request changes `deployment/terraform/bootstrap/`, for example to let the deployer read a new secret, apply that root from the pull request's branch under `acme-<env>-admin` (for production, `acme-prod-admin`), then merge. A merge whose deploy needs a permission the bootstrap has not granted stops half applied.

## 19a. The providers: Stripe, WorkOS, and Slack

Three providers sit outside AWS: Stripe takes payment, WorkOS signs
people in, and Slack carries the org's channel. Each is set up by hand
once, in its own dashboard, and each has a page that walks through it
for a person who has never opened that dashboard:

- [Stripe](../../docs/runbooks/providers/stripe.md): the sandbox and
  the live account, the two restricted keys (the runtime key the
  processes hold, and the bootstrap key the person holds),
  `acme-ops stripe-bootstrap`.
- [WorkOS](../../docs/runbooks/providers/workos.md): the Staging and
  Production environments, the Acme App application, its own API key
  (never the environment's) and its Redirects tab,
  `acme-ops workos-bootstrap`.
- [Slack](../../docs/runbooks/providers/slack.md): the environment's
  own app, made from its manifest in `deployment/slack/`, its client
  id, its client secret and signing secret, and public distribution,
  so each org installs it into its own workspace.

What they share is the order, because the secret that holds each value
is made by the deploy:

1. The environment's first deploy makes the five secrets, each holding
   `off`: `acme/<env>/stripe_runtime_key`,
   `acme/<env>/stripe_webhook_secret`, `acme/<env>/workos_api_key`,
   `acme/<env>/slack_client_secret`, and
   `acme/<env>/slack_signing_secret`.
   With `off` the environment runs, and says in its logs what is off.
2. A person writes each value under their own sign-in, `acme-staging`
   for staging (in production `acme-prod-power`, when authorized),
   with `AWS_ACCESS_KEY_ID` and its siblings unset, as section 18
   says. `stripe_webhook_secret` is the exception: the Stripe
   bootstrap writes it. Slack's client id is no secret: it is
   committed as `slack_client_id` in the environment root.
3. The next deploy, or a forced new deployment of the service, starts
   tasks that read the new values. A task reads its secrets only at
   start.

Do WorkOS before section 20: the first operator signs up through it,
and without its key every sign-in answers `503`.

## 20. The first operator

A deployed environment starts with no operator: a grant marks an identity, it does not make one. After the environment's first green deploy, follow `docs/runbooks/operator.md`, which is the same walk-through for every operator: sign in, grant, enrol the second factor, check the fence, and write the token into the ops env file. `docs/runbooks/deploy.md` (Grant an operator) is the reference for the workflow itself.

The first environment also needs, once:

- the **smoke identity**: a second address you control, granted `read`, named by the environment's `SMOKE_EMAIL` variable, so the deploy's smoke test runs signed in;
- the **provisioner**, when a traffic or stress run needs one: a third address, granted `write`, whose token is minted for the run and disabled again in production.
