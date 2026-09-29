---
name: ops-cloud-deployment-nuke
description: "Destroy one cloud environment of the platform as its account's administrator: empty the buckets, destroy the environment root, and report what remains (the bootstrap root whole: the zones, the registry, the roles, the state; and production's copies of what staging built). Runs scripts/cloud_nuke.sh after checking the administrator profile and the account against deployment/cloud/environments.json. Stops after the dry run until the person says go. Refuses production unless --confirm production is typed and a released change on release, applied, sets the database's deletion protection off. Applies from a clean worktree of the exact commit the environment runs, never the working tree. The one skill besides create that needs a credential that writes, so a person invokes it by name."
disable-model-invocation: true
allowed-tools: Read, Grep, Glob, Bash(aws:*), Bash(gh:*), Bash(jq:*), Bash(git fetch:*), Bash(git show:*)
---

# ops-cloud-deployment-nuke

The opposite of create, and just as rare. An environment is destroyed
by a person holding its account's administrator profile, with an agent checking
the preconditions and narrating. Production is protected twice: by a
pull request that lifts deletion protection, merged before this skill
runs, and by the name typed into the command.

Read `.claude/skills/_shared/ops-preamble.md` before the first step:
the profiles, the account check, and the env file are there.

## Input

`--env staging|production [--confirm <env>] [--dry-run]`

`--env` is required; ask for it when missing. `--confirm production`
is required for production and must be typed by the person, never
filled in by the skill. `--dry-run` runs the script in its dry mode,
which prints every command it would run and runs none. The script
destroys a cloud environment only; the compose stack is `make reset`
or `make down`, not this skill.

The account, the region, the administrator profile, and the public
names come from `deployment/cloud/environments.json`. The script also
needs `ALARM_EMAIL` (as an environment variable or as
`--alarm-email`), the value the GitHub environment holds, and refuses
without it.

## Role and credential

Refuse any profile but the environment's administrator
(`acme-staging-admin` or `acme-prod-admin`), checked with `sts
get-caller-identity` before any other command as the preamble states.
The administrator's permission set is assigned for the run; when it
has been handed back, the person asks for it again first. Every `aws`
command below carries `--profile <admin_profile>`; the script refuses
any other profile, clears keys exported in the shell, and checks the
account again before the apply and before the destroy.

No env file is read. The script leaves the environment's
`~/.config/acme/ops/<env>.env` and its profile in `~/.aws/config` in place
and lists them under what remains; removing them is the person's
call.

## Procedure

The processes are `api` and `maintenance`, the ECS services of the
cluster `acme-<env>`, as `deployment/README.md` lists them.

1. Verify the administrator profile as Role and credential states.
2. Production only, two checks, both before the script runs:
   - `--confirm production` is present and was typed by the person.
     Without it, stop and say what is missing; never suggest the
     flag as a paste.
   - The root's `database_deletion_protection` reads `false` on
     `release`, set by a merged pull request and released (the script
     reads the same line, then the applied state, and refuses
     otherwise):

     ```bash
     git fetch origin release
     git show origin/release:deployment/terraform/environments/prod/main.tf \
       | grep database_deletion_protection
     gh pr list --state merged --search "deletion protection" --limit 5
     ```

     Production applies `release`, so a change merged to `main` and
     not yet released has changed nothing there, and neither has a
     working tree. The script also reads `deletion_protection` from
     the database in the applied state and refuses while it is on.
     Stop and name the release that is still to come. The script
     applies from a clean worktree of `origin/release`, never from
     the working tree it was started in, so nothing unreleased reaches
     production on the way down. Staging's destroy applies the same
     way from the commit of staging's last deploy whose apply
     succeeded, read from its `deploy-staging` run, never from the tip
     of `main`, which may hold a merge staging never ran; the script
     refuses while a staging deploy is still going.
3. Read what the environment holds, so the report can say what is
   gone and what stays:

   ```bash
   aws ecs describe-services --cluster acme-<env> \
     --services api maintenance --profile <admin_profile>
   aws s3api list-buckets --query 'Buckets[?starts_with(Name, `acme-<env>-`)].Name' \
     --profile <admin_profile>
   aws secretsmanager list-secrets --filters Key=name,Values=acme/<env>/app/org/ \
     --query 'length(SecretList)' --profile <admin_profile>
   ```

4. Run the script dry, show the person what it printed, and stop.
   The real run waits for an explicit go the person types in this
   session after reading the dry run, in staging as in production;
   an unattended session ends at the dry run. The script is not
   among this skill's tools, so each run also asks the person before
   it starts:

   ```bash
   scripts/cloud_nuke.sh <env> --dry-run
   scripts/cloud_nuke.sh <env> [--confirm production]
   ```

   Narrate each step as the script reaches it: the clean worktree of
   staging's last deployed commit (staging) or `origin/release`
   (production) the root is applied from, so no unreleased change
   reaches the environment; the `destroyable`
   switch applied (`force_destroy` on the buckets, no recovery window
   on the secrets, and for staging only `skip_final_snapshot` and the
   lifted protection on the database) through one apply of the
   environment root; production's database keeps its final snapshot
   `acme-production-final` and its automated backups; `terraform
   destroy` of the environment root, which empties every
   `acme-<env>-*` bucket it owns, versions included, and never the
   state bucket; then the list of what remains.
5. Read what remains and write the report. The bootstrap root stays
   whole: the zones, because Cloudflare delegates to them; the
   registry and its images; the roles, so the pipeline can deploy the
   environment again; the budget; and the state bucket, whose
   environment prefix stays, empty, so a recreate finds its backend.
   The GitHub
   environment and its variables, the local env file, and the
   profiles stay too; the script touches none of them. A resource
   the destroy could not remove is listed with the reason the script
   printed.
6. Report what stays at the providers, from the script's last step.
   Nothing there is touched, by the script or by the skill: Stripe's
   webhook endpoint for the environment's API name (kept for a
   recreate, which rolls it; deleted by the person if the environment
   is not coming back), the customers its orgs made, WorkOS's
   organizations and users, and the environment's Slack app and the
   workspaces that installed it. Each org's Slack bot token, under
   `acme/<env>/app/org/`, stays in Secrets Manager, since the
   application wrote it and Terraform does not own it. The
   provider keys' values went with the secrets, so a recreate writes
   them again after its first deploy
   (`docs/runbooks/providers/`).

## What it never does

- No destroy without the preconditions: the profile, the person's
  word after the dry run, and in production the typed confirmation
  and the released change.
- No production database destroyed without its final snapshot, and
  no automated backup deleted with it.
- No touch of the bootstrap root: the state bucket, the zones, the
  registry, the roles.
- No destroy of the other environment: it lives in another account,
  the profile is the one this environment names, and the script
  refuses a session that resolves to any other account.
- No secret value printed.
- No console clicks: a resource the CLI cannot remove is reported,
  not clicked away.
- No write at Stripe, WorkOS, or Slack: what stays there is listed,
  and removing it is the person's call.

## Output

```markdown
# Environment destroyed: <env>

**Credential.** <admin_profile>, <Arn>, account <id> (expected <id>)
**Confirmation.** <the person's word after the dry run; typed --confirm production (production)>
**Deletion protection on release.** <false, PR <url>, applied | not needed (staging)>
**Applied from.** <origin/release | staging's last deployed commit> at <sha>, in a clean worktree

## Gone

- Services: <names>
- Database: <identifier>, final snapshot <skipped (staging) | acme-production-final>
- Buckets emptied and removed: <names>

## Remains

- The bootstrap root: zones <names> (delegated at Cloudflare), the registry and its images, <role names>, budget
- State prefix environments/<staging | prod>/ in acme-state-<id>, empty
- Production only: the final snapshot acme-production-final and the automated backups
- Staging only: production's copies of what staging built, in production's account
- GitHub environment and variables; ~/.config/acme/ops/<env>.env; the acme-<env>-investigate profile
- The orgs' Slack bot tokens under acme/<env>/app/org/: <count>, for the person to delete if the environment is not coming back
- <resource the destroy could not remove>: <reason>

## At the providers, untouched

- Stripe (<sandbox | live>): the webhook endpoint https://<api name>/webhooks/stripe, <kept for a recreate | for the person to delete>; the customers its orgs made; the restricted key acme-<env>-runtime, <kept for a recreate | for the person to delete>
- WorkOS (<Staging | Production>): the organizations and users its orgs made; the application's redirects
- Slack: the environment's app, whose request URLs fail until a new environment answers; the workspaces keep it installed until someone removes it in Slack; each org installs again
- Provider keys to write again on a recreate: stripe_runtime_key, workos_api_key, slack_client_secret, slack_signing_secret
```
