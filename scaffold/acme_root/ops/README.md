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

Under `.claude/skills/`. The operational ones: `ops-investigate`,
`ops-watch`, `ops-root-cause`, `ops-infra-as-code`,
`ops-cloud-deployment-create`, `ops-cloud-deployment-nuke`,
`ops-simulate-traffic`, `stress-test-create-or-update`, and
`stress-test-run`. The audits, which read and never fix, with their tools
in [audit/](audit/README.md): `audit-retention`, `audit-query-indexes`,
`audit-database-calls`, `audit-credential-lifetimes`,
`audit-provider-calls`, and `audit-deploy-time`. `tickets-triage` reads
the tracker and the repository.

## Stress scenarios

`smoke` runs locally and `staging` against a deployed environment; see
[stress/README.md](stress/README.md).
