# Operations

How Acme is operated once it runs. People steer, agents maintain:
every operational task is a skill a person runs with an agent, and the
safety boundary is the credential the skill holds, never the prompt. A
credential that can only read cannot break anything, so an agent
holding one may look at everything. A credential that writes is held
by a pipeline, or by a person for one named step.

## Roles and profiles

Each environment has an AWS account of its own
([environments.json](../deployment/cloud/environments.json)). One cloud
role per role per environment, and one named profile for each. No role a person or an agent holds can write to the cloud; a
change is a pull request.

| Role | Held by | May | Profile |
|------|---------|-----|---------|
| Administrator | a person | create and destroy an environment; nothing else | `acme-staging-admin`, `acme-prod-admin` |
| Deployer | the pipeline, through OIDC | apply staging; plan and apply production | none: the workflow's own |
| Investigator | an agent, or a person | read every signal and every resource description, plan Terraform; never a secret's value, a data bucket's object, or a database login | `acme-staging-investigate`, `acme-production-investigate` |
| Supporter | an agent, or a person | Investigator, plus the operator plane's read of one named org | the investigate profile, plus a `read` operator token |

There is no IAM user and no access key. A person signs in through IAM
Identity Center (`acme-staging` with PowerUserAccess, `acme-prod`
with ReadOnlyAccess, which writes nothing), and the investigate
profiles chain from that sign-in, so an agent works
inside a session a person opened. No skill runs under the sign-in
itself: it is wider than the investigate role. Everything the investigate roles are
denied is a fence in the role itself, not a rule in a skill.

`local` is an environment too. Its signals are the compose stack's
Prometheus, Jaeger, and GlitchTip, so every skill is testable with no
cloud.

## Credentials

- The cloud profiles live in the operator's own AWS configuration,
  never in the repository.
- The application credentials live in one owner-only file per
  environment outside the repository, `~/.config/acme/ops/<env>.env`:
  the API URL, two operator tokens, and the error tracker's URL, token,
  org, and project. The org and the project name the product's one
  project and hold the same value in every environment, because there
  is one project for the product and the environment is a property of
  each event; a read names the project and filters on
  `environment:<env>`. `ACME_OPERATOR_TOKEN` carries `read`, and every read runs as
  it. `ACME_PROVISIONER_TOKEN` carries `write`, and the traffic
  generator alone uses it, to create and remove the tenants a run
  needs. Each expires within the hour. The file never holds a sign-in
  or a TOTP secret: an agent never signs in to the operator plane. A
  command refuses a file its group or anyone else can read.
- A person gets onto the plane once, by the walk-through in
  `docs/runbooks/operator.md`: the first sign-in, the grant, the second
  factor, then the token below.
- `acme-ops token --env <env> --identity operator` is run by a person
  in their own terminal. It starts a sign-in through the identity
  provider and prints a code and an address; the person confirms the
  code in any browser. Then it asks for the TOTP code, verifies it
  through `POST /v1/auth/second-factor`, which ends the first sign-in,
  mints a `read` token through `POST /v1/admin/me/tokens`, which ends
  the second, and writes the token into the file without printing it.
  It prints the token's id, which is no secret. On the local stack,
  `--dev-email <address>` signs a person in by the local sign-in;
  without it, the token is the local read operator's (below).
  `--identity provisioner` copies the token the
  `grant-operator.yml` workflow wrote into the secret
  `acme-<env>-provisioner-token`, under the person's own sign-in
  (`--profile`; staging's sign-in profile by default, and in production
  `acme-prod-power` or the administrator, since production's everyday
  sign-in reads no secret). Never under an investigate profile: that
  role is denied every secret value, so no agent fills this line. A
  command whose token was refused names this one.
- `make seed` puts two local operators on the local allowlist, the
  platform's own identities `operator@platform.acme.invalid` (`read`)
  and `provisioner@platform.acme.invalid` (`write`), and writes their
  tokens into `~/.config/acme/ops/local.env` when that file is absent,
  owner-only in a folder only its owner opens, beside the local stack's
  API, GlitchTip, Prometheus, and Jaeger. So every skill runs against
  the local stack with nothing set by hand. A token lasts an hour:
  `uv run acme-ops token --env local --identity operator|provisioner`
  mints a fresh one into the file through `acme-api grant-operator
  --mint-token`, which prints a token only against a local database.
- `acme-ops token --env <env> --list` lists the person's own live
  operator tokens under the file's token: the id, the permission, and
  when each was made and ends. `--revoke <id>` ends one of them now,
  the file's own included; its next request is refused. Another
  operator's token is not found: taking an operator off the plane is the
  grant job's disable, which ends every token they hold
  (`docs/runbooks/operator.md`).
- A skill names the profile it needs, verifies which identity it holds
  before it runs, and refuses to run under a wider one.
- The platform's own secrets live in the secret store. No secret is in
  the repository, in a skill's text, or in a document.

## The signals

Both twins expose the same four signals through a read API, so one
skill reads either.

| Signal | Locally | In the cloud |
|--------|---------|--------------|
| Logs | the process's own output | CloudWatch Logs Insights, one log group per process per environment |
| Metrics | Prometheus | CloudWatch metrics, namespace `Acme` |
| Traces | Jaeger | X-Ray |
| Errors | GlitchTip | Sentry |

Errors are the one signal whose store is not per environment: there is
one project for the product, every environment reports into it, and the
environment each process sends on every event is what separates them.
A read asks for `environment:<env>`, and an issue that also holds
another environment's events answers with this environment's only.

What the local twin shows after thirty seconds of light traffic, the
wiring check CI runs. The five panels of the Grafana dashboard are the
five the CloudWatch dashboard carries, by title:

![Grafana's Acme overview after a light traffic run](../docs/media/ops/grafana-overview-after-traffic.jpg)

Jaeger with the traces of the same run, one server span per request,
found by the `acme.request_id` attribute:

![Jaeger's search after a light traffic run](../docs/media/ops/jaeger-traces-after-traffic.jpg)

Every line, span, and error event carries the request id, so one id
is enough to follow a request through all four.

**The dashboard.** One per environment, `acme-<env>` in CloudWatch,
with the same panels as Grafana's overview locally: targets up, HTTP
requests per second by route, HTTP responses per second by status,
HTTP latency p95 by route, outcomes per second; plus one row for the
database, the cache, and the queue, and one for the reads with a latency
alarm of their own and the age of each queue's oldest message; one for
the sweep's pass duration; and, on both dashboards, one for the work
queue and the outbox: the oldest ready item's age, the items failed in
the last fifteen minutes, and the oldest pending row's age. A test holds
the shared titles equal between the two definitions.

**The alarms.** Seventeen per environment, to one topic, `acme-<env>-alarms`,
with an email subscription: the load balancer's 5xx ratio, unhealthy
targets, database CPU, database free storage, running tasks below
desired for the API and the worker, the load balancer's p95 latency,
the p95 of `GET /v1/billing` on its own, a sweep pass longer than its
interval, for each inbound queue (`webhooks`, `slack`) a backlog and a
message in its dead-letter queue, and for the work queue and the outbox
in Postgres a backlog, a work item failed for good, the relay's lag, and
an outbox row failed for good.
The numbers are the team's; the shape is not.

**Cost.** A budget per account with alerts at 50, 80, and 100 percent
actual and 100 percent forecast, and an anomaly monitor, both to the
owner.

## The binary

`acme-ops` is the operators' one command. It rides the Python client
and the operator plane, and it presents an operator token, never a
person's sign-in.

| Subcommand | Does |
|------------|------|
| `traffic --env <e> --profile light\|regular\|heavy\|stress [--duration S] [--orgs N] [--report path]` | Drives realistic sessions at the edge and reports requests by route and status, p50, p95, p99, and the error ratio, the working requests and the sign-ins totalled apart. It signs each person in once at the start and out once at the end, and every session that person drives reuses the token; a sign-in the login rate limit refuses waits the window out and asks again, twice at most, and a run that signs nobody in exits 1 and names the limit. The people of an org share its tasks, so a write can meet a task another session changed: a 412 makes the session read the task afresh and write once more, and a task found gone (404) is dropped. The report counts these as conflicts, apart from errors and failed sessions. Its tenants are made under the provisioner's token, named `ops-<run id>-<n>`, and removed when the run ends, a failure included; the report names any it could not remove. Each person a run makes comes with a personal org, which is never deleted, so those stay behind with the people. `--orgs 0` drives the seeded people. Its people sign in by the local sign-in, which only the local stack serves, so a deployed environment refuses the run before it provisions anything. |
| `stress --scenario ops/stress/<name>.yaml [--duration S]` | The same generator at a profile with a duration, a ramp, and a target the working requests' p95 is held to; reads the signals back after the run. `--duration` shortens the run and moves no target. `.github/workflows/stress.yml` keeps the wiring for a run against staging, which staging refuses while its people have no sign-in without a browser. |
| `signals check --env <e> --request-id <id>` | Reads the log lines, the metric, the trace, and the error event for one request id. |
| `size --env <e>` | The platform's size: orgs, users, and the tasks and events of twenty-four hours, with the traffic generator's own tenants left out. It is the maintenance worker's latest count, made every five minutes, and the command prints how long ago it counted. |
| `token --env <e> --identity operator\|provisioner [--dev-email a]` | Writes an operator token into the env file, never printing it; prints the id of an operator's. On `local` with no `--dev-email` it is the local operator's that `make seed` made, minted by `acme-api grant-operator`, and a missing `local.env` is made owner-only with the local stack's addresses. |
| `token --env <e> --list` / `--revoke <id>` | Lists your own live operator tokens, or ends one by its id, under the env file's operator token. Exits 1 for an id that is none of your live tokens. |
| `work requeue --env <e> --org <org id> <item id> [--dev-email a]` | Sends one failed work item back to the queue, available now, with its attempts reset; the org's diary names who did. It is a write, so it is a person's step: it signs the person in with the second factor, as `token` does, mints a `write` token for this one call, keeps it nowhere, and signs it out once the call is made. A mint the plane refuses signs the sign-in out too. An item that is not failed is refused, and the command exits 1 with the reason. [The operate runbook](../docs/runbooks/operate.md) says how to find one. |
| `workos-bootstrap --environment staging\|production [--apply]` | Proves the key is the Acme App application's, then reconciles that application with `deployment/workos/environments.yaml`; see below. [The runbook](../docs/runbooks/providers/workos.md) has the dashboard steps around it. |
| `stripe-bootstrap --env <e> [--dry-run] [--secret-store aws\|none] [--profile p]` | Makes the payment processor's account match `deployment/stripe/desired-state.json`: the three products, the prices by lookup key, the Billing Portal configuration, and the environment's webhook endpoint. It reads its own key from `ACME_STRIPE_BOOTSTRAP_KEY`, a restricted key the person holds that may touch products, prices, webhook endpoints, and the portal configuration and nothing else; it never reads the runtime key. It refuses an organization key, a secret key, and a key whose mode is not the environment's. A rerun against a matching account changes nothing and says so. The endpoint's signing secret goes to `acme/<env>/stripe_webhook_secret` under the person's own sign-in (`--profile`; the environment's sign-in profile by default, and in production `acme-prod-power`, since `acme-prod` only reads) and is never printed; an investigate profile is refused. [The runbook](../docs/runbooks/providers/stripe.md) has the steps. |

## The WorkOS bootstrap

`deployment/workos/environments.yaml` is the desired state of the Acme
App, the WorkOS application Acme signs people in through, in each WorkOS
environment: its client id, its redirect URIs and which one is the
default, its login initiation URI, its App homepage URL, its sign-out
URIs and which one is the default, and its webhooks, which are none. `staging` serves the local stack and staging;
`production` serves production alone.

```bash
uv run acme-ops workos-bootstrap --environment staging          # a dry run
uv run acme-ops workos-bootstrap --environment staging --apply  # writes
```

The key is the Acme App application's own API key, from the variable
the file names for the environment (`WORKOS_API_KEY` for staging,
`WORKOS_PRODUCTION_API_KEY` for production), and the command never
prints it. Without the variable the command refuses.

The command proves the key first. It exchanges a code WorkOS never
issued, with the key as the client secret: WorkOS answers
`invalid_grant` for the application's own key and `invalid_client` for
any other. A key from the environment's API Keys page, or another
application's, stops the run with exit 2 before anything is read.

A redirect is present when AuthKit accepts it for the application: the
command asks `GET /user_management/authorize` with the client id and the
URI and reads where it is sent. That probe is the truth. The key reads
and writes the application's own redirect list, so under `--apply` a
missing redirect is added there and probed again. One the probe still
refuses is named as a dashboard step, and the command exits 1. So is a
default redirect that is not the one the file names: the API reports the
default and does not set it. The Redirects tab's other fields have no
API, so the command prints what each should hold as a check to make on
the tab. The sign-out URIs are among them. A second run against unchanged config says `nothing to
change`.

## The skills

Fourteen, under `.claude/skills/`, in two kinds.

The operational skills act on an environment. Every one that reads or
drives one takes `--env local|staging|production` (create and nuke
take `staging` or `production`; the scenario writer takes none), and
every one names the role it needs, what it reads, what it never does,
and its report.

The eight that hold a credential open by naming
`.claude/skills/_shared/ops-preamble.md`, which they read first: the
profiles, the account check, the env file's fields, and the way back
when a token expires, written once. What a skill must never miss stays
in the skill itself, one line each, because a referenced file is a
promise and an inlined line is a guarantee.

| Skill | Needs | Answers |
|-------|-------|---------|
| `ops-investigate` | Investigator | What is the state of this environment right now: the dashboard's panels, the alarms, the recent errors. |
| `ops-watch` | Investigator | Has anything changed since the last look; a periodic read of the same signals. |
| `ops-root-cause` | Supporter | Why did this request, or this org's problem, happen: the request id followed through every signal, and the org's records on the operator plane. |
| `ops-infra-as-code` | Investigator | What would this Terraform change do: a plan, read-only, against the live environment. |
| `ops-cloud-deployment-create` | Administrator | Bring up an environment's account: the state backend, the bootstrap root, the delegation of its names, the GitHub environments' variables, then the first deploy through the pipeline. |
| `ops-cloud-deployment-nuke` | Administrator | Tear an environment down. Refuses production unless deletion protection was lifted in a prior pull request and the name is typed; reports what is left. |
| `ops-simulate-traffic` | Provisioner | What does the platform look like under realistic traffic at a profile. |
| `stress-test-create-or-update` | none | Write or change a scenario file, with a target stated before any run. |
| `stress-test-run` | Provisioner, Investigator | Run a scenario, read the signals back, and say whether the target held. |

The audits answer a question about the system that repeats: after a
big change, before a release, when the product grows. An audit is
read-only. The ones about the database build their own: a database
named `audit_<slug>` on the local stack, seeded at a stated scale and
dropped when the run ends, so a run never logs in to a shared one. An
audit writes a report to `~/Downloads/acme_<audit>_<date>.md`, with
the answer first, a table with a verdict per row, the findings by
impact with a fix and an effort each, and what it could not verify; it
proposes tickets and never fixes. Its tools are under
[ops/audit/](audit/README.md). The two that read staging hold the
investigate profile and read the preamble like the operational skills.

| Skill | Needs | Answers |
|-------|-------|---------|
| `audit-retention` | Investigator | Which tables and stores grow without bound, what trims each, and whether that purge holds up when the table is large. |
| `audit-query-indexes` | none (local) | Do the indexes fit the queries: every statement mapped to its index and measured on a seeded database, the hot paths, and what breaks first under load. |
| `audit-database-calls` | none (local) | How many round trips and transactions each endpoint and worker flow makes, at its least and its most, and why; what an open socket costs an hour, and what one change costs across every open tab. |
| `audit-credential-lifetimes` | none (local) | How long each credential keeps working after it is revoked, on each channel it travels, what each check costs, and which trust rests on a push alone. |
| `audit-provider-calls` | none (local) | Every call to a provider, flow by flow: the order, the request path, the timeouts and retries, the deadline, and which call to remove, fold, move, or cache. |
| `audit-deploy-time` | Investigator | Where a deploy's minutes go, phase by phase, and which setting or step would shorten it. |

`tickets-triage` is beside them, not one of them: it reads the tracker
and the repository, not the system, and holds none of the platform's
roles. It gives every open ticket of this repository a verdict with its
evidence, and with `--apply` and the person's word it closes the done
and the moot.

## The first responder

The first responder is an agent. An alarm is read against the
platform's size first: the orgs, the users, and the traffic of the
last day. A 5xx ratio over ten requests an hour and one over ten
thousand a minute are different findings. Only then is it escalated,
with the size, the signals for one request id, and what changed.

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

The support runbook says how one investigation runs:
[docs/runbooks/support.md](../docs/runbooks/support.md). Stress tests
are described in [ops/stress/README.md](stress/README.md).
