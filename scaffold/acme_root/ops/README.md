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

The operator tokens live in `~/.config/acme/ops/<env>.env`, owner-only.
[The operator runbook](../docs/runbooks/operator.md) says how a person
gets onto the plane.

## Commands

`uv run acme-ops <command>`, each against one environment:

- `traffic` drives sessions at a profile: `light`, `regular`, `heavy`, or `stress`.
- `stress` runs a scenario and judges it against its target.
- `signals check` reads one request id's log lines, metric, trace, and error event.
- `size` prints the platform's size, the traffic run's tenants left out.
- `token` writes an operator token into the env file, or lists and revokes your own.
- `work requeue` sends one failed work item back to the queue.
- `workos-bootstrap` reconciles the WorkOS application with `deployment/workos/environments.yaml`.

## Skills

Under `.claude/skills/`. Each skill that reaches an environment reads
`.claude/skills/_shared/ops-preamble.md` first, and holds the one role its
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

## Stress scenarios

`smoke` runs locally and `staging` against a deployed environment; see
[stress/README.md](stress/README.md).
