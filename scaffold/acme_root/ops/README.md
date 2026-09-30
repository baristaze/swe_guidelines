# Operations

How Acme is operated once it runs. People steer and agents maintain:
each operational task is a skill a person runs with an agent. The safety
boundary is the credential the skill holds, never the prompt.

## Roles and profiles

Each environment has an AWS account of its own
([environments.json](../deployment/cloud/environments.json)). No role a
person or an agent holds writes to the cloud; a change is a pull request.

| Role | Held by | Profile |
|------|---------|---------|
| Administrator | a person, to create or destroy an environment | `acme-staging-admin`, `acme-prod-admin` |
| Deployer | the pipeline, through OIDC | the workflow's own |
| Investigator | an agent or a person: every signal, never a secret | `acme-staging-investigate`, `acme-production-investigate` |
| Supporter | the Investigator, plus the operator plane's read | the same, plus a `read` operator token |
| Provisioner | a traffic or stress run: makes and removes its own tenants | a `provisioner` operator token |

The `read` operator token lives in `~/.config/acme/ops/<env>.env`,
owner-only, which every skill that reads sources. The provisioner's
`write` token lives in a file of its own beside it,
`<env>.provisioner.env`, owner-only too, which only `traffic` and
`stress` read and no skill sources: a skill that reads never holds a
write token. `acme-ops` refuses an env file that holds the provisioner's
token, and prints the line that moves it.
[The operator runbook](../docs/runbooks/operator.md) says how a person
gets onto the plane.

## Commands

`uv run acme-ops <command>`, each against one environment:

- `traffic` drives sessions at a profile: `light`, `regular`, `heavy`, or `stress`.
- `stress` runs a scenario and judges it against its target.
- `signals check` reads one request id's log lines, metric, trace, and error event.
- `size` prints the platform's size, the traffic run's tenants left out.
- `token` writes an operator token into its file, or lists and revokes your own.
- `work requeue` sends one failed work item back to the queue.
- `workos-bootstrap` reconciles the WorkOS application with `deployment/workos/environments.yaml`.

## Skills

Under `.agents/skills/`, the folder every agent that reads the Agent
Skills standard shares; `.claude/skills` links to it for Claude Code. Each
skill that reaches an environment reads
`.agents/skills/_shared/ops-preamble.md` first, and holds the one role its
row names.

| Skill | Needs | Answers |
|-------|-------|---------|
| `ops-investigate` | Investigator | The environment's state now: the dashboard, the alarms, the recent errors. |
| `ops-watch` | Investigator | What changed since the last look. |
| `ops-root-cause` | Supporter | Why a request or an org's problem happened, across every signal and the operator plane. |
| `ops-infra-as-code` | Investigator | What a Terraform change would do: a read-only plan. |
| `ops-cloud-deployment-create` | Administrator | Bring up an environment's account, then its first deploy. |
| `ops-cloud-deployment-nuke` | Administrator | Tear an environment down, and report what is left. |
| `ops-simulate-traffic` | Provisioner | How the platform looks under traffic at a profile. |
| `stress-test-create-or-update` | none | Write or change a scenario, with its target stated first. |
| `stress-test-run` | Provisioner, Investigator | Run a scenario and say whether the target held. |

The audits read and never fix. The database ones build a database of
their own on the local stack and drop it; their tools are in
[audit/](audit/README.md).

| Skill | Needs | Answers |
|-------|-------|---------|
| `audit-retention` | Investigator | What grows without bound, and what trims it. |
| `audit-query-indexes` | none (local) | Whether the indexes fit the queries, measured on a seeded database. |
| `audit-database-calls` | none (local) | The round trips and transactions of each endpoint and worker flow. |
| `audit-credential-lifetimes` | none (local) | How long each credential works after it is revoked, on each channel. |
| `audit-provider-calls` | none (local) | Every call to a provider, flow by flow, and which to remove, fold, move, or cache. |
| `audit-deploy-time` | Investigator | Where a deploy's minutes go, and what would shorten it. |

`tickets-triage` reads the tracker and the repository, and holds none of
these roles.

`docs-compact` keeps the tree's documents to what holds at the head of
the main branch, and with `--migrations` folds each migration chain into
one revision. It edits on a branch, pushes nothing, and holds none of
these roles.

## Stress scenarios

`smoke` runs locally and `staging` against a deployed environment; see
[stress/README.md](stress/README.md).

## What an operator never does

- Never writes to the cloud. A change is a pull request, and the
  pipeline applies it.
- Never logs in to a shared database. The operator plane answers what a
  support case needs, and the role denies the connection. An audit logs
  in only to the database it made on the local stack.
- Never prints a secret. A skill verifies its identity and reads what
  the read role allows; a secret's value is outside that.
- Never reads telemetry by tenant. Telemetry carries no tenant id; a
  tenant's view is a product screen over the org's own diary.
