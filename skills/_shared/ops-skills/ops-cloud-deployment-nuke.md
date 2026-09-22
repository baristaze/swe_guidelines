---
name: ops-cloud-deployment-nuke
description: "Destroy one cloud environment of the platform as its account's administrator: empty the buckets, destroy the environment root, and report what remains (the bootstrap root, the state prefix, the images, and production's copies of what staging built). Runs scripts/cloud_nuke.sh after checking the profile and the account against deployment/cloud/environments.json. Refuses production unless --confirm production is typed and a released change on release, applied, sets the database's deletion protection off; applies from the environment's exact origin commit in a clean worktree. Supports --dry-run. The one skill besides create that needs a credential that writes."
disable-model-invocation: true
allowed-tools: Read, Grep, Glob, Bash(aws:*), Bash(gh:*), Bash(jq:*), Bash(git fetch:*), Bash(git show:*)
---

# ops-cloud-deployment-nuke

The opposite of create, and just as rare. An environment is destroyed
by a person holding its account's administrator profile, with an agent checking
the preconditions and narrating. Production is protected twice: by a
pull request that lifts deletion protection, merged before this skill
runs, and by the name typed into the command.

## Input

`--env staging|production [--confirm <env>] [--dry-run]`

`--env` is required; ask for it when missing. `--confirm production`
is required for production and must be typed by the person, never
filled in by the skill. `--dry-run` runs the script in its dry mode,
which prints every command it would run and runs none. `local` is
refused: this skill acts on a cloud environment only, and the local
stack has no administrator.

## Role and credential

The environment's `account_id` and `admin_profile` come from
`deployment/cloud/environments.json`:

```bash
jq '.environments.<env>' deployment/cloud/environments.json
```

This skill needs that `admin_profile` and refuses anything else.
Before any other command, run

```bash
aws sts get-caller-identity --profile <admin_profile>
```

and check that `Account` is the environment's `account_id` and `Arn`
is the identity center's administrator permission set, never
`assumed-role/acme-investigate-*`. Every `aws` command below carries
`--profile <admin_profile>`; the script clears any keys exported in
the shell, inherits the profile, and asks the account again before
every apply.

No env file is read. The script removes the environment's
`~/.config/acme/ops/<env>.env` and its investigate profile from
`~/.aws/config` at the end, and says so.

## Procedure

The process names below are the ones `deployment/README.md` lists;
`api` and `maintenance` are a tree the scaffold built with its worker,
and a worker added since is one more name. A tree built with
`--no-worker` has `api` alone: the API process runs the sweep, so
leave `maintenance` out of every command below.

1. Verify the administrator profile as Role and credential states.
2. Production only, two checks, both before the script runs:
   - `--confirm production` is present and was typed by the person.
     Without it, stop and say what is missing; never suggest the
     flag as a paste.
   - The root's `database_deletion_protection` reads `false` on
     `release`, set by a merged and released pull request and never
     by this script, and the applied state agrees:

     ```bash
     git fetch origin release
     git show origin/release:deployment/terraform/environments/prod/terraform.tfvars
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
     way from the commit of staging's last successful deploy, read
     from its deployment record, never from the tip of `main`, which
     may hold a merge staging never ran.
3. Read what the environment holds, so the report can say what is
   gone and what stays:

   ```bash
   aws ecs describe-services --cluster acme-<env> \
     --services acme-<env>-api acme-<env>-maintenance --profile <admin_profile>
   aws s3api list-buckets --query 'Buckets[?starts_with(Name, `acme-<env>-`)].Name' \
     --profile <admin_profile>
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

   Narrate each step as the script reaches it: the `destroyable`
   switch applied (`force_destroy` on the buckets, and in staging
   `skip_final_snapshot` on the database; production's database always
   leaves its final snapshot and keeps its automated backups) through
   one plan and apply of the environment root; every `acme-<env>-*` data bucket emptied,
   versions included, and never the state bucket; `terraform destroy`
   of the environment root; the GitHub environment and its variables
   removed; the local profile and env file removed.
5. Read what remains and write the report. Production's final
   snapshot stays, with its name. The bootstrap root stays:
   the zones the domain delegates to, the state bucket with its empty
   prefix, the registry and its images, the roles, the budget. It is
   what the environment's next life starts from. Destroying staging
   leaves production's copies of what staging built in production's
   account. A resource the destroy could not remove is listed with the
   reason the script printed.

## What it never does

- No destroy without the preconditions: the profile, the person's
  word after the dry run, and in production the typed confirmation
  and the released change.
- No production database destroyed without its final snapshot, and
  no automated backup deleted with it.
- No touch of the bootstrap root, the state bucket, or the zones.
- No destroy of the other environment: it lives in another account,
  which this profile cannot reach, and every provider pins the
  environment's own.
- No secret value printed.
- No console clicks: a resource the CLI cannot remove is reported,
  not clicked away.

## Output

```markdown
# Environment destroyed: <env>

**Credential.** <admin_profile>, <Arn>, account <id> (expected <id>)
**Confirmation.** <the person's word after the dry run; typed --confirm production (production)>
**Deletion protection on release.** <false, PR <url>, applied | not needed (staging)>
**Applied from.** <origin/release | staging's last successful deploy> at <sha>, a clean worktree

## Gone

- Services: <names>
- Database: <identifier>, final snapshot <skipped (staging) | taken (production)>
- Buckets emptied and removed: <names>
- GitHub environment and variables: <names>
- Local profile and env file: acme-<env>-investigate, ~/.config/acme/ops/<env>.env

## Remains

- Final snapshot <name> and the automated backups (production)
- Bootstrap root: zones <names> (delegated at the domain's DNS host), roles <names>, budget
- State prefix environments/<env>/ in <bucket>, empty
- Images: <repositories>
- <resource the destroy could not remove>: <reason>
```
