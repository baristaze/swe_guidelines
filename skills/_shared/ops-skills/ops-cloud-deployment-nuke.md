---
name: ops-cloud-deployment-nuke
description: "Destroy one cloud environment of the platform as the administrator: empty the buckets, destroy the environment root, and report what remains (the zone, the state prefix, the shared resources). Runs scripts/cloud_nuke.sh after checking the administrator profile. Refuses production unless --confirm production is typed and a merged change on main sets the database's deletion protection off. Supports --dry-run. The one skill besides create that needs a credential that writes."
allowed-tools: Read, Grep, Glob, Bash(aws:*), Bash(gh:*), Bash(git fetch:*), Bash(git show:*), Bash(scripts/cloud_nuke.sh:*)
---

# ops-cloud-deployment-nuke

The opposite of create, and just as rare. An environment is destroyed
by a person holding the administrator profile, with an agent checking
the preconditions and narrating. Production is protected twice: by a
pull request that lifts deletion protection, merged before this skill
runs, and by the name typed into the command.

## Input

`--env local|staging|production [--confirm <env>] [--dry-run]`

`--env` is required; ask for it when missing. `--confirm production`
is required for production and must be typed by the person, never
filled in by the skill. `--dry-run` runs the script in its dry mode,
which prints every command it would run and runs none. `local` is the
compose stack: the script's local branch stops every container and
removes the volumes, and needs no cloud, so the skill is testable
with no account.

## Role and credential

This skill needs the administrator profile `acme-admin` and refuses
anything else. Before any other command, run

```bash
aws sts get-caller-identity --profile acme-admin
```

and check that `Arn` is the administrator's own identity in the
account the environment belongs to, never
`assumed-role/acme-investigate-*`. Every `aws` command below carries
`--profile acme-admin`; the script inherits it.

No env file is read. The script removes the environment's
`~/.config/acme/ops/<env>.env` and its investigate profile from
`~/.aws/config` at the end, and says so.

## Procedure

1. Verify the administrator profile as Role and credential states.
2. Production only, two checks, both before the script runs:
   - `--confirm production` is present and was typed by the person.
     Without it, stop and say what is missing; never suggest the
     flag as a paste.
   - The root's `database_deletion_protection` reads `false` on
     `main`, set by a merged pull request:

     ```bash
     git fetch origin main
     git show origin/main:deployment/terraform/environments/production/terraform.tfvars
     gh pr list --state merged --search "deletion protection" --limit 5
     ```

     A working tree that reads `false` while `main` reads `true` is
     not enough: the pipeline applies `main`, so the protection is
     still on. Stop and name the pull request to merge first.
3. Read what the environment holds, so the report can say what is
   gone and what stays:

   ```bash
   aws ecs describe-services --cluster acme-<env> \
     --services acme-<env>-api acme-<env>-maintenance --profile acme-admin
   aws s3api list-buckets --query 'Buckets[?starts_with(Name, `acme-<env>-`)].Name' \
     --profile acme-admin
   ```

4. Run the script, dry first:

   ```bash
   scripts/cloud_nuke.sh <env> --dry-run
   scripts/cloud_nuke.sh <env> [--confirm production]
   ```

   Narrate each step as the script reaches it: the `destroyable`
   switch applied (`force_destroy` on the buckets,
   `skip_final_snapshot` on the database) through one plan and apply
   of the environment root; every `acme-<env>-*` data bucket emptied,
   versions included, and never the state bucket; `terraform destroy`
   of the environment root; the GitHub environment and its variables
   removed; the local profile and env file removed.
5. Read what remains and write the report. The zone stays, because
   the registrar delegates to it. The state prefix stays, empty, so
   a recreate finds its backend. `shared` stays: the roles, the
   budget, the operators user serve the other environment. A resource
   the destroy could not remove is listed with the reason the script
   printed.

## What it never does

- No destroy without the preconditions: the profile, the typed
  confirmation, the merged change.
- No touch of `shared`, the state bucket, or the zone.
- No destroy of the other environment: every command names `<env>`,
  and the role fences deny the other environment's tags to the
  investigate roles; the administrator has no fence, which is why
  every command is narrated before it runs.
- No secret value printed.
- No console clicks: a resource the CLI cannot remove is reported,
  not clicked away.

## Output

```markdown
# Environment destroyed: <env>

**Credential.** acme-admin, <Arn>, account <id>
**Confirmation.** <typed | not needed (staging)>
**Deletion protection on main.** <false, PR <url> | not needed (staging)>

## Gone

- Services: <names>
- Database: <identifier>, final snapshot <skipped>
- Buckets emptied and removed: <names>
- GitHub environment and variables: <names>
- Local profile and env file: acme-<env>-investigate, ~/.config/acme/ops/<env>.env

## Remains

- Zone <name> (delegated at the registrar)
- State prefix environments/<env>/ in <bucket>, empty
- shared: <role>, <user>, budget
- <resource the destroy could not remove>: <reason>
```
