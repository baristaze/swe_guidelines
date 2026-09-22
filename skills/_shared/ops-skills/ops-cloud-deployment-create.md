---
name: ops-cloud-deployment-create
description: "Create one cloud environment of the platform in its own account, from nothing to its first deploy, as that account's administrator: the bootstrap root (state bucket, OIDC trust, deploy and investigate roles, budget, registry, zones), the delegation of its public names, the investigate profile chained from the identity center sign-in, the GitHub environments and their variables, then the first deploy through the pipeline and the smoke test. Runs scripts/cloud_create.sh after checking the profile and the account against deployment/cloud/environments.json, and the GitHub login. Supports --dry-run. The one skill besides nuke that needs a credential that writes."
disable-model-invocation: true
allowed-tools: Read, Grep, Glob, Bash(aws:*), Bash(gh:*), Bash(jq:*)
---

# ops-cloud-deployment-create

The bootstrap. Everything the pipeline needs before its first run is
made here, in the environment's own account, by a person holding that
account's administrator profile with an agent narrating. After this
skill the environment moves only by pull request; the administrator
profile goes back in the drawer.

## Input

`--env staging|production [--dry-run]`

`--env` is required; ask for it when missing. `--dry-run` runs the
script in its dry mode, which prints every command it would run and
runs none, so the whole path is readable before the first resource
exists. `local` is refused: this skill acts on a cloud environment
only, and the local stack has no administrator.

The script also needs `OWNER_EMAIL` and `ALARM_EMAIL` (as environment
variables or as `--owner-email` and `--alarm-email`), and the token
that writes the delegation at the domain's DNS host, as an environment
variable only, so it never lands in shell history. That token is the
run's one credential outside the account: scoped to the domain's zone,
short-lived where the host allows it, and held only for the run. It refuses without
them; ask for any that is missing, and never for the token's value in
the conversation.

## Order

Each run acts on one account. Staging runs first, then production,
then staging again: staging's images and kept bundles replicate into
production's account, and the replication needs production's registry
and bucket to exist. The second staging run finds them and turns it
on. Production's first release is a commit merged to `main` after
that.

## Role and credential

Everything this skill checks against comes from
`deployment/cloud/environments.json`: the environment's `account_id`,
its `admin_profile`, its `sso_profile`, the region, and its public
names. Read them first:

```bash
jq '.region, .environments.<env>' deployment/cloud/environments.json
```

This skill needs the environment's `admin_profile` and refuses
anything else. Before any other command, run

```bash
aws sts get-caller-identity --profile <admin_profile>
```

and check two things: `Account` is the environment's `account_id`,
and `Arn` is the identity center's administrator permission set,
never `assumed-role/acme-investigate-*`. An investigate profile or an
everyday sign-in cannot create a role, and the skill stops rather
than try. The script clears any keys exported in the shell, pins the
profile, and asks the account again before every apply.

The GitHub login is `gh auth status`; it names a user who can write
the repository's environments and their variables.

No env file is read. The script writes one: `~/.config/acme/ops/<env>.env`,
owner-only, with the API's URL and the lines `ACME_OPERATOR_TOKEN`,
`ACME_PROVISIONER_TOKEN`, and the tracker's left empty: no operator
exists until the pipeline's first-operator job has run and the
operator has enrolled a second factor. The file never holds a
password or a TOTP secret, since an agent never signs in with a
password. It writes the investigate profile into
`~/.aws/config`. The skill prints the names of what was written and
never a value.

## Procedure

1. Verify the profile and the account as Role and credential states.
   Check the GitHub login.
2. Run the script, in dry mode first when `--dry-run` was given, or
   when it is the first time this environment is created. The script
   is not among this skill's tools, so every run asks the person
   before it starts, and the person's approval is the go:

   ```bash
   scripts/cloud_create.sh <env> --dry-run
   scripts/cloud_create.sh <env>
   ```

3. Narrate each step as the script reaches it, in one line each, so
   the person can stop it between two:
   - The bootstrap root, applied with local state and then moved into
     the state bucket it made: the artifacts bucket, the registry, the
     OIDC trust, the deploy
     roles (staging's one, production's plan and apply), the investigate
     role `acme-investigate-<env>` with its fences, the permission
     boundary, the budget and the anomaly monitor, and one hosted zone
     per public name. Staging's root also turns the replication into
     production on once production's bucket exists.
   - The delegation: one NS record per name server of each zone,
     written at the domain's DNS host, and any stale one removed.
   - The investigate profile `acme-<env>-investigate`, chained by
     `role_arn` and `source_profile` from the identity center profile
     `<sso_profile>`. No key is minted anywhere.
   - The repository's two branch rulesets, when absent: `main`
     (a pull request with a review and a code owner's review, every
     required check, no force push, no deletion) and `release` (no
     push, no force push, no deletion, and one bypass actor, the
     repository host's app `release.yml` pushes with).
   - The GitHub environments (staging's `staging`; production's
     `production-plan` with no reviewer and `production` with the
     required reviewer), each with its deployment-branch policy
     (staging's `main`, production's `release`), and each one's
     variables from the root's outputs, under the same names in each.
   - The first deploy, through the pipeline: the script pushes nothing
     and applies no environment root itself. For staging it dispatches
     the deploy workflow. For production it names the next steps of
     Order instead.
   - The smoke test, printed as the step after the first operator
     and never run by this skill: the telemetry round trip against
     the deployed base, one traffic session read back by request id
     through CloudWatch, X-Ray, and the error tracker. The deploy
     workflows run it after each apply with the smoke identity's
     operator token, so it is skipped until `grant-operator.yml` has
     granted the smoke identity a `read` entry, the environment's
     `SMOKE_EMAIL` variable names it, and the grant job has minted its
     token (`mint_token: smoke`).
4. Check the result with the investigate profile the script wrote,
   because that is the profile every later skill holds:

   ```bash
   aws sso login --profile <sso_profile>
   aws sts get-caller-identity --profile acme-<env>-investigate
   ```

5. Write the report.

## What it never does

- No apply by hand: the script applies the bootstrap root, which has
  no pipeline, and dispatches the pipeline for the environment; it
  never runs `terraform apply` in an environment root.
- No act outside the environment's account: every provider pins it,
  and the script refuses a profile that resolves anywhere else.
- No cloud user and no access key, for a person or an agent.
- No secret value read or printed: the DNS token, the operator
  tokens, and the tracker token stay in the environment, the secret
  store, or owner-only files, and appear in the report as the names
  that hold them.
- No console clicks: what the script cannot do with the CLI is
  reported as a manual step with its exact command.
- No tenant data; there is none yet.
- No run under any profile but the environment's `admin_profile`, and
  no run twice without `--dry-run` in between: the script is written
  to be rerun, and the dry run shows what a rerun would touch.

## Output

```markdown
# Environment created: <env>

**Credential.** <admin_profile>, <Arn>, account <id> (expected <id>)
**GitHub.** <login>, environments: <names>, variables: <names>

## Made

- Bootstrap root: state bucket <bucket>, roles <names>, budget <usd>/month
- Zones: <name> -> <name servers>, delegated at the domain's DNS host
- Replication into production: <on | off, production not bootstrapped yet | not staging>
- Profile written: acme-<env>-investigate from <sso_profile> (~/.aws/config)
- Env file written: ~/.config/acme/ops/<env>.env
- First deploy: workflow run <url>, <status> | production: waits for Order
- Smoke test: not yet; it follows the smoke identity's grant and its token

## Next

- <the next run of Order, or nothing>
- Dispatch `grant-operator.yml` for the first operator, who enrols the second factor at the console's first sign-in and runs `uv run acme-ops token --env <env> --identity operator` in their own terminal; grant the smoke identity and set `SMOKE_EMAIL`, so the next deploy runs the smoke test
- Put <admin_profile> away; every later skill runs under acme-<env>-investigate.
```
