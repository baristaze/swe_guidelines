---
name: ops-infra-as-code
description: "Write or change the platform's Terraform in the shape the guideline prescribes: one module graph shared by every environment, a variable for everything that differs, the autoscaling switch, the destroyable switch, the budget, the alarms, and the tags; then plan it under the read-only investigate profile and open a pull request. Never applies. Use for a new resource, a new environment, a size change, or a lever flip."
allowed-tools: Read, Grep, Glob, Write, Edit, Bash(aws:*), Bash(terraform:*), Bash(gh:*), Bash(git checkout:*), Bash(git add:*), Bash(git commit:*), Bash(git push:*), Bash(git status:*)
---

# ops-infra-as-code

Every cloud resource is Terraform in this repository, and every change
to the cloud is a pull request that the pipeline applies. This skill
writes the change, proves it with a plan that cannot write, and opens
the pull request. The apply is the deployer role's, through the
workflow, after review.

Read `../_shared/ops-preamble.md`, a path from this skill's folder,
before the first step: the profiles, the account check, and the env
file are there.

## Input

`--env local|staging|production <change>`

`--env` is required; ask for it when missing. `<change>` is what to do
in one sentence: "add a bucket for exports", "raise the api max to
six", "turn autoscaling on", "make staging destroyable". `local` has
no Terraform to plan; the skill writes the change and runs
`terraform fmt` and `terraform validate`, which need no credential,
so the shape is testable with no cloud.

## Role and credential

`--env local` needs no credential.

`--env staging` and `--env production` run under the investigate
profile of that environment, `acme-<env>-investigate`, checked with
`sts get-caller-identity` before the plan as the preamble states.
Refuse any profile wider than the investigate role. The role reads its
account's state bucket, `acme-state-<account>`, under
`environments/staging/` or `environments/prod/`, and describes every
resource, which is all a plan needs. It cannot lock the state and
cannot write it, so the plan runs with `-lock=false`, and an apply
under it fails by construction.

The pull request needs `gh auth status` to name a login. No env file
is read; this skill touches no application credential.

## Procedure

1. Read `deployment/terraform/` whole before writing: `modules/`, both
   `environments/`, and both `bootstrap/` roots. The shape to keep:
   - One module graph. Every environment instantiates the same
     modules; what differs is a value in that environment's root
     module call (`environments/<staging | prod>/main.tf`) or a `-var`
     the pipeline passes, never a resource that exists in one root and
     not the other.
   - Variables, not clicks. A resource a person made in the console
     is imported or recreated here; nothing is left untracked.
   - The autoscaling switch. `autoscaling_enabled` at the root,
     default `false`; every service passes `{ max, target_cpu }` with
     `enabled = true`, so the root switch is the one flip.
   - The destroyable switch. `destroyable` at the root, default
     `false`: buckets `force_destroy`, the database
     `skip_final_snapshot`, deletion protection off, all under the one
     variable.
   - The budget in each account's bootstrap root (`modules/account`):
     `monthly_budget_usd`, the four notifications, the anomaly monitor,
     to `owner_email`.
   - The alarm topic `acme-<env>-alarms` and the seven alarms, the
     dashboard `acme-<env>`, the log retention on every group, and
     `default_tags` with `environment` on the provider.
2. Write the change in the module that owns the resource, then wire
   it in both environments with a variable, even when only one
   environment changes today. A resource the settings read gets its
   name passed to the process environment in the same change.
3. `terraform fmt -recursive deployment/terraform` and
   `terraform validate` in the environment root (`terraform init
   -backend=false` first when the providers are not fetched).
4. Cloud only. Plan under the read-only profile:

   ```bash
   # <root> is staging or prod; production's root is environments/prod.
   cd deployment/terraform/environments/<root>
   AWS_PROFILE=acme-<env>-investigate terraform init -reconfigure \
     -backend-config="bucket=acme-state-<account>" \
     -backend-config="key=environments/<root>/terraform.tfstate" \
     -backend-config="region=us-west-2" \
     -backend-config="use_lockfile=true"
   AWS_PROFILE=acme-<env>-investigate terraform plan -lock=false -out=/dev/null \
     -var "api_image=<in state>" -var "maintenance_image=<in state>" \
     -var "alarm_email=$ALARM_EMAIL" \
     -var "api_domain_name=<api host>" -var "app_domain_name=<app host>" \
     -var "site_domain_name=<site host>"
   ```

   The account and the three hosts are the environment's entry in
   `deployment/cloud/environments.json`. The `-var` values are the ones
   the deploy workflow passes
   (`.github/workflows/deploy-<staging | production>.yml`); the images
   are the ones in state, so the plan shows the change and not a roll.

   Read the plan whole. A destroy the change did not ask for stops
   the run. A plan that fails on a permission the role lacks is a
   finding about the role, reported and not worked around.
5. Branch, commit, push, and open the pull request with `gh pr
   create`: the change in one sentence, the plan summary (`n to add,
   n to change, n to destroy`) pasted, and the levers it moves named.
   The pipeline plans again on the pull request; production's apply
   waits behind the environment's approval.

## What it never does

- No `terraform apply`, no `terraform import` that writes state, no
  `-lock=true` plan under the investigate role.
- No write to the cloud outside Terraform, no console clicks.
- No secret value in the Terraform or the pull request: a secret is a
  reference to the secret store, never its value.
- No tenant data; the skill reads resource descriptions and state.
- No change to a `bootstrap/` root and an environment in the same pull
  request unless the change needs both, said in the description. A
  bootstrap root is applied by its account's administrator through
  `ops-cloud-deployment-create`, never by the pipeline.

## Output

```markdown
# Infra change: <env>, <change>

**Credential.** <profile and Arn, or none for local>
**Files.** <paths written or changed, one per line>

## Plan

<n> to add, <n> to change, <n> to destroy (`-lock=false`, read-only)
- <resource>: <add | change | destroy>, <one line>

## Levers

- autoscaling_enabled: <value>, destroyable: <value>, budget: <usd>

## Pull request

<url, or "not opened: <reason>">
```
