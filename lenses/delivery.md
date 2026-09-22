# Delivery

Group id: `delivery`. Covers Apps (Apps as Products, Apps Are Dumb),
Deployment, Monorepo Folder Structure, Client App Architecture (except
the realtime channel rule), Telemetry (except Correlation Across a
Handoff), Cross-Cutting Conventions (except The App Container), and
Technology Choices and How to Override Them of `architecture.md`.

This group judges how the system reaches people and machines: the
apps at the edge, the repository they are built from, the environments
they run in, and the conventions every process shares for errors,
logs, telemetry, and configuration. It leaves the realtime channel
rules (one channel per app, envelopes, degraded mode) to `network`,
the app container boot order to `contracts`, and the operator
console's gating (the allowlist, the refusal of a tenant role or a
portal flag) with the credential rules behind it to `context`
(CTX-20). The tenant isolation suite in Tests, and the negative
control that proves it catches a breach, it leaves to `context`
(CTX-30, CTX-31), which owns the tenancy evidence end to
end. What a handoff carries across a process boundary, the
causing request and the link from a run's span to the causing trace,
it leaves to `context` (CTX-29) and `async` (ASY-29).

## DEL-01 Apps are dumb, and logic lands in the layer it belongs to

**Principle.** Only UI rendering, local input handling, and browser- or
terminal-specific behavior live in an app. Logic meaningful to one app
moves into that app's app-specific service; logic meaningful to more
than one app moves into a domain service or the OM. Business logic and
cross-service orchestration never live in the app.

**Source.** Apps, Apps Are Dumb.

**Look for.** App source under `apps/`: component files, hooks, and
CLI command bodies that compute a business outcome, validate a domain
rule, or sequence calls to more than one service; the same rule
implemented in two apps; a domain rule living in an app-specific
service that another app would need.

**Violation.** A component or command that decides eligibility,
totals, state transitions, or permissions; an app that calls two
backend operations and combines their results to reach a business
conclusion; domain constants duplicated into the app; the portal and
the CLI each implementing the same aggregation. (Where a rule two apps
share belongs is CON-13.)

**Severity.** medium

## DEL-02 One cloud, environments that differ by variables

**Principle.** Cloud deployments target AWS. Services and workers run
on the container runtime; each browser app ships from a private S3
bucket served through CloudFront. Every environment root has the same
module graph, and everything that differs between two is a variable,
the base domain included, under which `api.` is the gateway, `app.`
the portal, and `admin.` the operator console.

**Source.** Deployment, Cloud: AWS.

**Look for.** `deployment/terraform/environments/*`: the set of modules
each environment instantiates and the variables it passes, the base
domain among them; resources that exist in one environment and not
another. A bucket and a distribution for each browser app under
`apps/`, the bucket's public-access block and read policy; the
`api.`, `app.`, and `admin.` records.

**Violation.** A module, resource, or wiring present only in
production's environment root (the bootstrap roots differ by design,
DEL-42); environment-specific branches in module code instead of
variables; a hostname hard-coded in a module instead of derived from
the base domain. A browser app with no bucket and distribution in some
environment; a public bucket or website endpoint; a bundle served from
a container.

**Severity.** medium

## DEL-03 Every cloud resource is declared in Terraform, in the monorepo

**Principle.** Networks, services, databases, topics, buckets, and IAM
are all defined in Terraform that lives in the same monorepo. An
environment change is a pull request. Formatting and validation of
every root, bootstrap and environment alike, run in CI.

**Source.** Deployment, Infrastructure as Code.

**Look for.** `deployment/terraform/` coverage of every resource the
application names in its settings; the CI workflow's Terraform
formatting and validation jobs across every root.

**Violation.** A resource referenced by settings or runbooks that no
Terraform file declares; a runbook step that says "create in the
console"; a CI pipeline that validates one root and not the others.
(Which root holds what is DEL-42.)

**Severity.** medium

## DEL-04 Dependencies in local containers, the application on the host

**Principle.** Every technology dependency runs as a local container
through one compose stack, using cloud images or wire-compatible
stand-ins at the version Versions sets. `make up` starts the
dependencies and the `devx` profile in containers and the application
on the host through the start script, migrates, seeds, and prints the
local URLs; `make down` stops both and keeps the data.

**Source.** Deployment, Local: Docker Compose.

**Look for.** `deployment/local/docker-compose.yml` and the second
compose file that runs the application in containers for the case that
asks for it, and the image tag of each dependency; the start script; the
`up`, `down`, `reset`, and `urls` targets and what each does; the step
targets they wrap (`setup`, `infra-up`, `infra-down`, `infra-reset`,
`migrate`, `seed`); CI jobs that reference compose services.

**Violation.** A dependency the application needs that the compose stack
does not run; application services baked into the default compose file
so a code change needs an image rebuild; an `up` that starts the
application in containers, or that skips the migration or the seed; a
`reset` that keeps a volume; a CI job that runs a shortcut such as `make
up` instead of its steps, or a shortcut with no step behind it.

**Severity.** medium

## DEL-05 External services have a twin behind the same interface

**Principle.** An external service has one interface and at least two
impls: the real client and a deterministic twin with the same wire
shapes. Tests, the local stack, and CI run against the twin; the real
client is proven against fixtures and a non-gating sandbox workflow. A
twin refuses to run outside `local` and names its provenance on every
record.

**Source.** Deployment, Twins for External Services.

**Look for.** `integrations/` and provider selection in settings: one
interface per external service, the impls behind it, provenance fields
on records the provider produces, the guard that refuses a twin
outside a local environment, the workflow that exercises real
clients. The short list of services that cannot be twinned faithfully
and use a shared development tenant instead.

**Violation.** A test suite that needs a live account or network to
pass; a twin that can be selected in a production-named environment;
records from a twin indistinguishable from real ones; a gating CI job
that calls a sandbox; a shared development tenant where a faithful
twin is possible. (The boot check that makes the selection impossible
is DEL-06.)

**Severity.** medium

## DEL-06 Unsafe settings are refused at boot

**Principle.** A settings combination that is only safe locally is
refused by the process at boot with a message naming the setting, not
by a deployment checklist. A boot that succeeds logs one line naming
every backend it chose.

**Source.** Deployment, What a Process Refuses.

**Look for.** Boot code and settings validation: checks pairing the
environment name with the secrets backend, twin selection with the
environment, the development seed with a local database; the start-up
inventory log line.

**Violation.** A staging or production environment that can start on
the file secrets backend; a twin selectable in any environment but `local`; a
development seed that runs against a non-local database; a boot
with no line saying which backends are in use; a runbook that carries
a check the process could make itself.

**Severity.** high

## DEL-07 The monorepo is grouped by role, with one OM distribution

**Principle.** The repository root groups code by role: `om/`, `infra/`,
`integrations/`, `gateway/`, `services/`, `workers/`, `apps/`,
`clients/`, `ops/`, `deployment/`, `scripts/`, `specs/`, `docs/`. A
system that starts as one API process has one entry under `services/`
and grows the rest. The OM is a single distribution covering every
namespace, and namespaces are folders inside it.

**Source.** Monorepo Folder Structure; Layout Conventions.

**Look for.** The top-level tree and where a new package was placed; a
service or worker outside its role folder; domain code outside `om/`;
the number of distributions under `om/`. (Where migrations live is
STO-18.)

**Violation.** A worker under `services/`; a second object model
package next to `om/`; a `utils/` or `common/` package at the root
that holds domain types; application code under `deployment/` or
`scripts/`; a per-namespace OM package.

**Severity.** medium

**Check.** `arch-check` decides the one OM distribution, distributions
under `deployment/` and `scripts/`, and a worker or service in the
other's folder; the rest is judged.

## DEL-08 Src layout, tests as a sibling, a product-specific root package

**Principle.** Every Python distribution uses the `src/<root>/...`
layout, and tests live in a `tests/` sibling, so the test runner
exercises the installed package. The root package is named for the
product; the layout is what matters, not the word.

**Source.** Monorepo Folder Structure, Layout Conventions.

**Look for.** Each distribution's `pyproject.toml`, `src/` and
`tests/` directories; test imports that resolve to the source tree
instead of the installed package; the top-level package under each
`src/`.

**Violation.** A package at the distribution root without `src/`;
tests inside the package; a `conftest.py` that inserts the source tree
on `sys.path`; a root package whose name shadows a standard-library
module; distributions in the same workspace using different root
package names.

**Severity.** low

**Check.** `arch-check` decides the src layout, the `tests/` sibling,
the root package, and a `conftest.py` that edits `sys.path`; the rest
is judged.

## DEL-09 Workers and services share one project shape

**Principle.** Workers and services share `pyproject.toml`, `src/`,
`tests/`, and a `main.py` behind a console entry point. Workers have no
`routers/` or `types/`; services do. A service binary is also its own
operations CLI (`serve`, `migrate`, `bootstrap`, `openapi`).

**Source.** Monorepo Folder Structure, Layout Conventions.

**Look for.** The layout of each entry under `services/` and
`workers/`; the `[project.scripts]` entry point; the subcommands its
`main.py` exposes.

**Violation.** A worker with a routers module; a service whose
migration or bootstrap logic lives in a separate script rather than a
subcommand of its own binary; a service or worker distribution with no
console entry point.

**Severity.** low

**Check.** `arch-check` decides the entry point, the `main.py`, and the
`routers/` and `types/` of each service and worker; the rest is judged.

## DEL-10 Dockerfiles are central, two-stage, non-root, with a healthcheck

**Principle.** Dockerfiles live together under `deployment/docker/`,
one per image, sharing an entrypoint. An image builds in two stages,
installs one workspace package with locked dependencies, runs as a
non-root user, and declares a healthcheck against `/healthz`, which
the local stack reads; a task definition declares its own (DEL-44).

**Source.** Monorepo Folder Structure, Layout Conventions.

**Look for.** One Dockerfile per image under `deployment/docker/`:
stages, the install command and lock file, the `USER` instruction, the
`HEALTHCHECK` instruction, the shared entrypoint.

**Violation.** A Dockerfile inside a service folder; a single-stage
image carrying build tooling; a process running as root; an image
without a healthcheck; an install that ignores the lock file.

**Severity.** medium

**Check.** `arch-check` decides where each Dockerfile lives, its stages,
its user, its healthcheck, and its locked install; the rest is judged.

## DEL-11 Workspace tooling at the root, `make check` as the fast gate

**Principle.** One `pyproject.toml` declares the uv workspace and one
`package.json` with `pnpm-workspace.yaml` declares the TypeScript
members. Lint, format, and type-check config live at the root.
`make check` runs lint, format, types, and unit tests; CI runs it plus
the integration, migration, image, and infrastructure jobs.

**Source.** Monorepo Folder Structure, Layout Conventions.

**Look for.** Root config files and the `check` target; per-package
lint or type configs that diverge from the root; CI jobs beyond the
fast gate.

**Violation.** A package with its own lint rules that contradict the
root's; a `check` target that skips types or format; CI that runs only
the fast gate and never the integration or migration jobs.

**Severity.** low

**Check.** `arch-check` decides the workspace roots, lint and type
config below the root, and the `check` target; the rest is judged.

## DEL-12 React + TypeScript on Vite, rendered in the client; Python for the CLI

**Principle.** Every browser app is React + TypeScript built with
Vite into a static bundle that renders in the client only and talks
to the gateway, the realtime channel, the object store through a
presigned URL it was handed, and the error tracker when one is
configured, and nothing else. The CLI is Python.

**Source.** Client App Architecture, Stack; Client Rendering.

**Look for.** `apps/*/package.json` and build config; a browser app
introduced on a different framework or toolchain; the build output and
how it is served; any server runtime deployed alongside the bundle;
network calls to hosts other than the gateway, the channel, the error
tracker, and a presigned object-store URL; the CLI's language.

**Violation.** A second frontend framework or bundler in the
workspace; a rendering server or API routes in the app's toolchain or
deployment; a bundle that calls a third-party API directly for domain
data; two data-fetching paths (server and client) for the same screen;
a CLI rewritten outside Python.

**Severity.** medium

**Check.** `arch-check` decides the dependencies of each browser app and
the CLI's language; the rest is judged.

## DEL-13 TanStack Query for server state, Zustand for client state

**Principle.** Server state lives in TanStack Query with one query-key
factory per domain. Client state lives in Zustand. Realtime envelopes
write into the query cache or the client store, never directly into
components.

**Source.** Client App Architecture, State and Data.

**Look for.** `src/queries/` and the key factory; store modules;
realtime handlers and what they write to; server data held in stores
or component state.

**Violation.** Server data copied into a store and kept in sync by
hand; query keys spelled inline in several places; a realtime handler
that sets component state or calls a component callback; a third state
library.

**Severity.** medium

**Check.** `arch-check` decides a third state library in a browser app;
the rest is judged.

## DEL-14 Views render, view-model hooks decide, model modules compute

**Principle.** Each screen has a pure Model module (row builders,
codecs, formatting, predicates; no React), a View-Model hook that
combines queries, mutations, stores, and the model, and a View that
renders. Components contain no fetches, mutations, or business
decisions. The model module is the unit of testability.

**Source.** Client App Architecture, Views, View-Models, Models.

**Look for.** Per-screen folders under `src/features/`: the presence
of a model module with tests, a view-model hook, and components that
only consume the hook.

**Violation.** A component that calls a query or mutation hook
directly; a predicate or formatter defined inside a component file; a
screen with no model module and untested decision logic in the hook.

**Severity.** medium

## DEL-15 Generated types behind a facade; one transport client

**Principle.** Types are generated from the committed OpenAPI document
into one file and re-exported through a curated facade. One small
hand-written client owns transport: bearer and app header, error
envelope parsed into a typed error with the request id, sign-out on
401. Feature code never calls `fetch`.

**Source.** Client App Architecture, API Access.

**Look for.** `src/api/`: the generated file, the facade, the client;
imports of the generated path from feature code; direct `fetch` calls.

**Violation.** A feature module importing from the generated schema
file; a second transport client inside the app (a second client for a
service across the repository is NET-15); hand-maintained request or
response types that duplicate generated ones; `fetch` outside the
client.

**Severity.** medium

## DEL-16 The operator console shares the stack, never the security context

**Principle.** The operator console is a separate application sharing
the portal's stack, design tokens, component kit, sign-in flow, and API
client, and never its security context. It has its own origin, bundle,
and routes under `/v1/admin/*`, and holds no realtime socket.

**Source.** Client App Architecture, The Operator Console.

**Look for.** `apps/admin/`: its origin configuration, route prefix,
and the absence of a socket provider, a membership picker, and an org
chip; operator screens inside the portal's bundle.

**Violation.** Operator screens built into the portal's bundle; the
console opening the realtime channel; the console served from the
portal's origin; a design kit forked instead of shared; a picker or an
org chip in the console, which has no tenant.

**Severity.** high

## DEL-17 The CLI is a thin REST client with exit codes

**Principle.** The CLI talks REST with an API key, attaches an
idempotency key to every creating call, turns the outcome of a
followed operation into an exit code, and trusts the operating
system's certificate store.

**Source.** Client App Architecture, The CLI Is Different.

**Look for.** The CLI's HTTP client setup, creating commands and their
headers, the follow loop and its exit code, TLS configuration.

**Violation.** A creating command without an idempotency key; a
follow that exits zero on failure; a bundled certificate store that
ignores the OS's.

**Severity.** medium

## DEL-18 Exceptions carry status and code; one boundary translates

**Principle.** Every platform exception is rooted at
`PlatformException`, which carries `http_status` and a stable `code`.
Shape exceptions (`NotFound`, `Conflict`, `ValidationFailed`,
`NotAuthenticated`, `NotAuthorized`, `Unavailable`) cover most cases;
a namespace family multiply-inherits a shape. Translation to HTTP
happens once, at the boundary. Managers never format HTTP.

**Source.** Cross-Cutting Conventions, Exceptions.

**Look for.** `om/src/<root>/om/exceptions.py`; exception classes
defined elsewhere;
`raise` sites in managers; the boundary handler; status codes set in
routers.

**Violation.** An exception raised inside the platform and rooted at
neither `PlatformException` nor, under infra, `InfraException` (DEL-29);
a manager raising a framework HTTP exception; a router mapping exception
types to status codes (the handler and its envelope are NET-07); a
raised leaf exception that inherits no shape and so surfaces as 500; an
open breaker, a refused admission, or a backend that is down presented
as a 500 instead of the unavailable shape.

**Severity.** medium

**Check.** `arch-check` decides the root of every exception class and a
web framework imported by the OM; the rest is judged.

## DEL-19 Standard logging, configured once, correlated by filter

**Principle.** Every module logs through `logging.getLogger(__name__)`.
Formatting, level, and sink are configured once at boot, JSON in cloud
and readable locally. Correlation fields reach every line through a
filter reading a context variable set where the context is built.

**Source.** Telemetry, Logs.

**Look for.** Logging setup in the boot path; per-module logger
creation; any second logging library; how the request id reaches log
records.

**Violation.** A module configuring handlers or levels; a third-party
logging library; the request id passed by hand into log calls or
absent from them.

**Severity.** medium

**Check.** `arch-check` decides the logger names, where logging is
configured, and a second logging library; the rest is judged.

## DEL-20 OpenTelemetry traces and Prometheus metrics, used directly

**Principle.** Traces use OpenTelemetry directly; the tracer provider
is configured only when an endpoint is set, otherwise the no-op tracer
runs and the code paths stay identical. Metrics are exposed on
`/metrics` in Prometheus format through the client library directly.
Observability is used through its vendor API, as a feature flag SDK
is (DEL-22); the backend is a config detail.

**Source.** Telemetry, Traces and Metrics; Infrastructure,
Infrastructure Principles.

**Look for.** Tracing and metrics setup; a platform module that
re-exposes spans, counters, or histograms under its own names; code
paths that branch on whether tracing is configured; the exporter
configuration that selects the backend.

**Violation.** A `PlatformTracer` or `MetricsInterface` wrapper; a
second metrics system; code that skips instrumentation when no
exporter is set instead of relying on the no-op tracer; a backend
swap that touches code beyond the exporter config.

**Severity.** low

**Check.** `arch-check` decides a second metrics system and a wrapper
interface around telemetry; the rest is judged.

## DEL-21 One settings object per process; nothing below reads the environment

**Principle.** Configuration is read once at boot into one settings
object from environment variables under one product prefix, with an
optional `.env` and a committed `.env.example` documenting every knob.
A browser app reads its settings at start from the `config.json` next
to its bundle. Backends are selected there and nowhere else; managers
and service impls receive handles and options through constructors.

**Source.** Cross-Cutting Conventions, Configuration.

**Look for.** The settings class and its prefix; `.env.example`
coverage; `os.environ` or `getenv` reads outside the settings and boot
modules, excepting the local secrets impl, whose backend is the
environment.

**Violation.** An environment read inside a manager, storage, or
router; a build-time variable in a browser app carrying a value that
differs between environments (the bundle that carries it is DEL-31); a
knob missing from `.env.example`; a second prefix; backend selection
performed outside the settings and boot path.

**Severity.** medium

## DEL-22 Product variation is a modelled entity, not a flag

**Principle.** Runtime variation that belongs to the product (what a
tenant may do, what a plan allows) is a modelled entity with a manager
and storage. A feature flag, when needed, is a vendor SDK used
directly with its client injected at boot.

**Source.** Cross-Cutting Conventions, Configuration.

**Look for.** Where per-tenant or per-plan behavior is decided; flag
checks in managers; a home-grown flag abstraction.

**Violation.** A tenant entitlement expressed as a flag or an
environment variable; a platform wrapper around a flag SDK; a flag
client constructed inside a manager.

**Severity.** medium

## DEL-23 Constraining decisions are ADRs, cited by number

**Principle.** A decision that constrains future work is recorded under
`docs/adr/` with context, decision, and consequences, dated and
numbered. Code and comments cite the ADR by number.
`docs/architecture.md` describes the system as built.

**Source.** Cross-Cutting Conventions, Records of Decisions.

**Look for.** A diff that removes or skips a conformance test, adds a
lint or type suppression on one, or takes an exception to a rule the
guideline states, and whether an ADR number appears in the same diff;
whether the code that embodies a decision cites it. An addition to an
enumerated-exceptions list is documented by a docstring and the
checker's enumeration instead (CTX-12).

**Violation.** An exception to a guideline rule introduced with no
ADR; code that embodies an ADR's decision without citing its number;
`docs/architecture.md` left describing a shape the change removed.

**Severity.** medium

**Check.** `arch-check` decides the number, date, and sections of each
ADR and every ADR number code cites; the rest is judged.

## DEL-24 Checkable rules fail the build

**Principle.** A rule that a program can check is checked. A rule that
reads only the source is decided by `arch-check`, run in the gate. A
rule that needs the built system or a migrated database is a test: the
tenancy scope, every root built whole, and the list of request-stage
methods. A rule that fails the build holds.

**Source.** Cross-Cutting Conventions, Records of Decisions.

**Look for.** The `arch-check` run in the gate and the tag it pins;
the tests for the rules the checker cannot decide; a rule stated in
docs that a program could enforce and nothing does.

**Violation.** A gate with no `arch-check` run, or a run at a tag
other than the one the project pins; no test of the tenancy scope
against a migrated database; a gate that runs neither the build-once
test (CON-20) nor the request-stage list (CTX-16). (The enumerated
tenant-less methods are CTX-12; the scope map and its policies are
STO-28.)

**Severity.** medium

## DEL-25 Technology substitutions are recorded, shapes are kept

**Principle.** The technologies the guideline names are defaults. A
project that substitutes an equivalent keeps every rule that does not
name the technology and records each substitution in one ADR under
`docs/adr/`: the choice as named, the substitute, the reason, the
rules it must still satisfy. A substitution that changes a shape is a
deviation, recorded as one.

**Source.** Technology Choices and How to Override Them, Overriding a
Choice.

**Look for.** Dependencies and providers that differ from the named
stack (`pyproject.toml`, `package.json`, compose images, Terraform
providers, the ORM and migration tool); whether `docs/adr/` holds a
record naming each substitution; whether the project's pointer to the
guideline links it; whether the substitute still satisfies the rules
the record lists (queue claim, atomic increment, a bus that reaches
every subscribed process).

**Violation.** A substitute technology in the tree with no ADR naming
it; an ADR that swaps a technology and silently drops a rule it cannot
satisfy; a substitution recorded as a deviation or a deviation recorded
as a substitution.

**Severity.** medium

## DEL-26 Dependencies run on their latest stable or LTS release

**Principle.** Every dependency runs on its latest stable release: the
current active LTS line where the technology publishes one, the newest
stable release its maintainers recommend otherwise. Pre-releases,
release candidates, and lines past their end of life are not used. A
release is adopted at the next scheduled bump, once a patch release
sits behind it, never the day it ships.

**Source.** Technology Choices and How to Override Them, Versions.

**Look for.** The version declared where the tool reads it:
`.python-version` and `requires-python`, `.nvmrc`, the
`packageManager` field of `package.json`, Dockerfile base images,
image tags in the local compose files, runtime steps in CI workflows,
engine versions in Terraform. The lock files, resolving to what those
declarations name. A dated upgrade record under `docs/`, when the
project keeps one, as the evidence of the scheduled bump.

**Violation.** A declaration naming a pre-release or a release
candidate (`rc`, `beta`, `alpha`, a `-dev` tag), or a `.0` release with
no patch behind it in the same declaration. A lock file that resolves
below what the declarations name, or a declaration moved without the
lock following in the same diff. Two declarations of one dependency
that disagree, such as `.nvmrc` and the CI runtime step.

**Severity.** low

**Check.** `arch-check` decides a pre-release in a declared version and
runtime pins that disagree; the rest is judged.

## DEL-27 Every process reports errors, off until a DSN is set

**Principle.** Errors are reported through the Sentry SDK, used
directly, initialized at boot in every web service, worker, and
browser app. Unhandled exceptions and `ERROR` log records become
events tagged with service, release, and request id. Reporting is off
until a DSN is set (empty or `off` means unset), and a missing tracker
never stops a boot.

**Source.** Telemetry, Error Tracking.

**Look for.** SDK initialization in each process's boot path; the tags
set on events; how the DSN setting is read and what an empty or `off`
value does; the browser app's route error elements and the React
root's error callbacks; the `devx` profile's GlitchTip and the fixed
project key it is seeded with.

**Violation.** A worker or browser app with no error reporting; a boot
that fails when the DSN is unset or the tracker is unreachable; events
without the release or the request id; a browser app that reports only
from one top-level boundary; a local tracker whose project key must be
created by hand.

**Severity.** medium

## DEL-28 Unit tests run over memory, the contract cases over both

**Principle.** Unit tests run over the memory roots and the pure rules
with no infrastructure. The storage contract cases are plain modules
parameterized by a storage fixture: the fast gate runs them over
memory, and the integration job runs the same cases over Postgres on
the compose stack.

**Source.** Cross-Cutting Conventions, Tests.

**Look for.** The storage fixture and the modules parameterized by it;
which suites the fast gate and the integration job run; a unit test
that opens a connection.

**Violation.** A storage case written twice, once per impl; a unit
test that needs a running database; the memory impl tested and the
Postgres impl assumed.

**Severity.** medium

## DEL-29 Infra has its own exception root, presented alike

**Principle.** Infra imports nothing from the OM, so it has its own
root, `InfraException`, with the same two fields as
`PlatformException`, a status and a stable code; the gateway and the
worker loop present both alike, and a boundary that must translate
one into the other does it by those fields, never by catching a name
from the other side.

**Source.** Cross-Cutting Conventions, Exceptions; Infrastructure,
Infrastructure Principles.

**Look for.** The infra distribution's exception module; what the
cloud impls raise on a driver error; the gateway's handlers and the
worker loop's catch; any `except` that names a class from the other
distribution.

**Violation.** An infra impl raising `PlatformException` or letting a
driver exception escape; an `InfraException` without a status or a
code; a gateway that presents `PlatformException` in the envelope and
lets `InfraException` fall to the catch-all; a manager or a boundary
that catches an infra subclass by name instead of translating the root
by its status and code.

**Severity.** medium

**Check.** `arch-check` decides the fields of `InfraException` and an
infra exception caught by name; the rest is judged.

## DEL-30 The bearer lives in session storage; the distribution sends a CSP

**Principle.** The bearer lives in memory and in the tab's session
storage, so a reload survives and a closed tab forgets, never in local
storage. The distribution sends a `Content-Security-Policy` naming the
app's origin, the API, the error tracker's origin when configured, and
the object store's when the app moves bytes through presigned URLs,
and nothing else, set in Terraform.

**Source.** Client App Architecture, API Access.

**Look for.** Where the transport client and the session store read
and write the bearer; every `localStorage` reference in the app; the
response headers policy of the static-site module in Terraform.

**Violation.** A token written to `localStorage`, which every tab and
every later visit reads; a bearer kept only in memory, so every reload
signs out; a distribution with no `Content-Security-Policy`; a policy
that allows a script origin outside that list; the header set in
`index.html` as a meta tag instead of in Terraform beside the
distribution.

**Severity.** high

## DEL-31 Production promotes, it never rebuilds

**Principle.** Production does not rebuild: it promotes the copies of
what staging deployed, replicated into its own account, images by digest
and bundles by commit, and never reads staging's. Tags are immutable,
the bundle prefix refuses overwrites, and a copy that differs from the
digest record kept outside staging's account is refused. A bundle reads
what differs from a `config.json`.

**Source.** Deployment, Cloud: AWS.

**Look for.** The production workflow: how it obtains its images and
bundles (a lookup by the release commit in its own account, never a
build, that waits a bounded time for the copy), the plan it writes,
and the approval between the plan and the apply. The replication of
the registry and of the kept bundles into production's artifacts
bucket, and the one write production grants each; the `config.json`
each deploy writes next to the bundle; the tag mutability of every
repository, the overwrite refusal on production's bundle prefix, and
the record the staging deploy writes on the repository host that
production compares with the copy; the credential the build steps
hold.

**Violation.** A production job that builds an image or a bundle; a
task definition pinned to a tag rather than a digest; a release commit
with no digest from staging that is deployed instead of refused; a
production job or task that reads staging's registry or bucket, or a
replication that may create a repository; a bundle kept in a state
bucket, or replicated into one; a lookup that refuses at once while the
copy is still in flight; a repository with mutable tags, or a bundle
prefix a second write can replace; a production lookup that accepts a
copy with no record to compare against, or accepts a commit staging
built and failed to deploy; a build step that holds the credential
that applies; an API origin or DSN compiled
into a bundle (the approval on the plan is DEL-38).

**Severity.** medium

## DEL-32 Every environment collects what its processes emit

**Principle.** Every environment collects what its processes emit; an
endpoint nothing reads is not observability. Logs leave through the
container runtime's log driver into one log group per process, with
retention set. Metrics and traces leave through a non-essential
OpenTelemetry collector beside each task that adds only the service
and the environment as dimensions.

**Source.** Deployment, Cloud: AWS.

**Look for.** The log configuration of each task definition and the
log group it names, with its retention; the collector container
beside each task, what it scrapes and receives, the dimensions it
adds, and whether it is marked essential.

**Violation.** A log group with no retention; a task definition with
no collector container; a collector marked essential; a task
identifier copied into metric dimensions.

**Severity.** medium

## DEL-33 Developer dashboards live in the devx profile

**Principle.** Developer dashboards live in an optional compose profile
named `devx`, started by the developer's own commands and never by CI:
one browser per backing service the stack runs (pgweb for Postgres,
Valkey Admin for the cache, the consoles the local images ship, Jaeger
for traces, GlitchTip for errors) and the metrics view, each on a host
port read from `.env`.

**Source.** Deployment, Local: Docker Compose.

**Look for.** The `devx` profile in the compose file and the
dashboards it holds against the backing services the default profile
runs; the host port of each dashboard and where it is read from; CI
jobs that reference a dashboard container.

**Violation.** A CI job that depends on a dashboard container; a
dashboard in the default profile; a backing service whose local image
ships a console and has no dashboard in `devx`; a dashboard port fixed
in the compose file.

**Severity.** medium

## DEL-34 The README lists every local URL and the seeded sign-in

**Principle.** The repository's `README.md` lists every local URL a
developer opens: each dashboard, each service's interactive API docs,
each browser app. `make seed` runs `bootstrap` with a development org
and owner read from `.env` (an `.example` address, a development
password), changes nothing when run again, and the `README.md` lists
the command and the seeded sign-in next to the URLs.

**Source.** Deployment, Local: Docker Compose.

**Look for.** The local URLs in `README.md` against the dashboards,
services, and apps the tree holds; the `seed` target and the
`bootstrap` subcommand it runs; the seed settings in `.env.example`;
the seeded sign-in in `README.md`.

**Violation.** A dashboard, a service's API docs, or a browser app
whose local URL the `README.md` does not list; no `seed` target, so a
developer creates the first org and user by hand; a seed that fails or
duplicates on a second run; a seed owner on a routable domain; seeded
credentials the `README.md` does not show.

**Severity.** medium

## DEL-35 Requests, queues, caches, and limits are counted with bounded labels

**Principle.** Every request counts once with its route template and
status; every queue, cache, and rate limit has a counter with an
outcome label. Label values are bounded: a template, a status, an
outcome, never an id. Every process serves `/metrics`, workers
included; a worker has no API, so it serves `/metrics` and `/healthz`
alone on its own small port.

**Source.** Telemetry, Traces and Metrics.

**Look for.** The request counter and its labels; the counters
declared next to each queue, cache, and rate limit; the labels each
metric takes and the set of values each can hold; each worker's
small port and the `/metrics` and `/healthz` it serves there.

**Violation.** A request counter missing the route template or status
label; a queue, cache, or rate limit with no outcome counter; an id as
a label value; a worker that records metrics nothing can read, or
whose image has no `/healthz` to check.

**Severity.** low

**Check.** `arch-check` decides an id as a label name; the rest is
judged.

## DEL-36 End to end runs in-process; a deployed run is a smoke test

**Principle.** End-to-end tests build the container over the memory
storage root and the local infra root, every backend a twin, and drive
the app in-process. Markers `integration`, `e2e`, and `slow` decide
which gate runs what. A run against a deployed environment is a small
smoke test of the deployment, in addition to the in-process suite and
never in its place.

**Source.** Cross-Cutting Conventions, Tests.

**Look for.** How end-to-end tests build the container and which
backends they select; the markers on each test module and which gate
runs each marker; the deployed run and what it covers (a sign-in, a
write, a push).

**Violation.** An end-to-end suite that runs only against a deployed
environment, so nothing drives the app in-process; a slow or
integration test with no marker, so the fast gate runs it; a deployed
run that grows into the suite it was meant to complement.

**Severity.** medium

## DEL-37 The named atomic methods are raced, not only called

**Principle.** The named atomic methods are raced, not only called: a
contract case runs two callers at once against a claim, a take-over, a
ticket redemption, and asserts that exactly one wins, over memory and
over the engine, because a statement whose whole purpose is a race is
not proven by a sequence.

**Source.** Cross-Cutting Conventions, Tests.

**Look for.** The contract case behind every named atomic method and
whether it runs two callers at once; which storage fixtures the case
is parameterized by.

**Violation.** A named atomic method proven by a sequence of calls and
never by a race; a race run over memory only, so the engine's locking
is assumed; a race that asserts both callers succeed.

**Severity.** medium

## DEL-38 Staging is main, production is release, moved by a fast-forward

**Principle.** Staging is `main`: every merge deploys it with no
approval. Production is `release`, fast-forwarded to the last commit
staging deployed by the repository host's app, the one actor its ruleset
admits. A push to `release` plans production, waits for a person's
approval, and applies, after checking that `release` is an ancestor of
`main`.

**Source.** Deployment, Cloud: AWS.

**Look for.** The staging workflow's trigger (a push to `main`) and the
absence of an approval on it; the production workflow's trigger (a
push to `release`), its ancestor check before the plan, and the
environment protection that holds the apply until a person approves;
the `release` workflow, the commit it fast-forwards to, the app token
it pushes with and the environment that holds the app's key, and the
ruleset that lets nothing else push to `release`; the concurrency group
on each deploy workflow; where the saved production plan is kept; the
guarded redeploy of a previous release commit.

**Violation.** A staging deploy behind an approval, or one triggered by
anything but `main`; a commit made on `release` or a merge into it; a
production apply with no plan approved first; a deploy of production
that plans without checking that `release` is an ancestor of `main`;
a person or a job that can push to `release` other than the
fast-forward; a fast-forward to the tip of `main` rather than to the
last successful staging deploy; a push to `release` made with a deploy
key or a repository-wide secret, or an app key held outside an
environment that admits `main` alone; a deploy workflow with no
concurrency group, or one that cancels a run in progress; a saved plan
uploaded as a workflow artifact; a redeploy that accepts a commit
production never ran, skips the approval, or moves `release` back.

**Severity.** medium

## DEL-39 Every log line names its service, its environment, and its request

**Principle.** Every log line carries the service and the environment
it came from, which is what lets one query read across processes, the
request id, and the request that caused it where a handoff supplied
one.

**Source.** Telemetry, Logs.

**Look for.** The fields the boot's formatter and filter put on every
record; a worker's log lines beside the request that enqueued its
work; any call site that passes one of these fields by hand.

**Violation.** Lines with no service or environment, so a query cannot
read across processes; a worker's lines that name no causing request,
so nothing joins them to the request behind the work; a request id on
the gateway's lines and absent from the worker's. (The filter that
attaches them is DEL-19.)

**Severity.** medium

## DEL-40 The app works in one tenant at a time

**Principle.** The portal signs in once and reads the memberships:
one goes straight in, several get a picker first, none get a plain
message. The pick is an exchange for one session. An org chip switches
by a second exchange; the app then drops the old tenant's caches and
reopens its socket. It never holds two sessions.

**Source.** Client App Architecture, One Tenant at a Time.

**Look for.** The sign-in and sign-up screens and what follows each
answer; the org chip and its switch; what the switch clears in the
query cache and the stores, and when the socket reopens; every place
the app keeps a bearer.

**Violation.** A dashboard rendered before a tenant is chosen; a
switch that keeps the old tenant's query cache or store entries, so
one tenant's rows show under another; a socket left open across a
switch; two sessions held at once, or the sign-in credential kept
after the exchange. (A picker in the console is DEL-16.)

**Severity.** high

## DEL-41 One cloud account per environment, and nothing spans two

**Principle.** Every environment has a cloud account of its own, and
the account is the boundary between environments. No root spans two:
each account holds its own state, artifacts, registry, trust, and
budget. Production trusts staging for two scoped writes, the
replication into its registry and its artifacts bucket, and nothing
else. The environment tag stays as a second fence.

**Source.** Deployment, Cloud: AWS.

**Look for.** The account each root applies in, and whether any two
environments share one; a root, a bucket, a registry, or a trust that
serves two environments; any policy in one account that names a
principal of the other, and what it grants.

**Violation.** Two environments in one account, fenced by tags alone;
a shared root for both environments; production trusting a staging
principal for anything but the replication writes (DEL-31), or for
those writes beyond their repositories and their prefix; a staging
write into production's state bucket; staging reading production at
all. (The environment tag on every resource is OPS-18.)

**Severity.** high

## DEL-42 A bootstrap root and an environment root; one file names the accounts

**Principle.** Each environment has two roots. The bootstrap root,
applied by the administrator, holds what the pipeline needs first: the
state and artifacts buckets, the federation trust, the deploy and
investigator roles, the budget, the registry, the zones. The pipeline
applies the environment root. One file names each account, and every
provider pins its own.

**Source.** Deployment, Infrastructure as Code.

**Look for.** The roots under `deployment/terraform/` and who applies
each; the environments' file and every script and root that reads it;
the account pin on every provider block. (What the deploy role may
change of its own trust and the bootstrap's state is DEL-47.)

**Violation.** A resource the pipeline needs declared in no bootstrap
root, or made by hand; an account id or region typed into a script or
workflow instead of read from the file; a provider with no account
pin.

**Severity.** medium

## DEL-43 Each public name is delegated to a zone in its environment's account

**Principle.** Each public name an environment serves has a hosted
zone of its own in that environment's account, with the name's records
at its apex. The domain's zone stays where the domain is hosted and
delegates each name with NS records, written once by the create run. A
deploy writes only its own account's zones.

**Source.** Deployment, Cloud: AWS.

**Look for.** The zones each bootstrap root declares and the names they
carry; where the delegation records are written, and by what; the
record-name condition on each deploy role's DNS writes.

**Violation.** One zone both environments write into; a delegation
added by hand in a console; a deploy role that can write the domain's
zone, another environment's name, or any record outside its own
public names and their validation records.

**Severity.** medium

## DEL-44 The target group and the task probe `/healthz`, never `/readyz`

**Principle.** The load balancer's target group probes `/healthz`, and
each task definition declares its own health check against it, since
the runtime never reads an image's. `/readyz` is read by the deploy
after a rollout and by an operator, never by a check that replaces a
task, so a dependency's blip cannot replace every task at once.

**Source.** Deployment, Cloud: AWS.

**Look for.** The target group's health check path; the health check
in each task definition; what reads `/readyz`.

**Violation.** A target group or a task health check on `/readyz`; a
task definition with no health check of its own, relying on the
image's `HEALTHCHECK`; a check that replaces tasks and waits on a
dependency.

**Severity.** medium

## DEL-45 The deploy migrates, as a one-off task before the rollout

**Principle.** A deployed database is migrated by the deploy: a one-off
task on the new image, inside the apply and before the rollout, in
production after the approval. A revert never removes an applied
migration. A deployed environment's first operator is granted by the
same kind of task, run by the pipeline.

**Source.** Deployment, Migrating a Deployed Database.

**Look for.** Where each environment root runs the migration, what it
runs on and under, and what the rollout depends on; how production
orders it against the approval; how the first allowlist entry of a
deployed environment is made.

**Violation.** A migration run by hand against a deployed database; a
migration after the rollout, or outside the apply, so new tasks serve
an old schema; a production migration before the approval; a first
operator inserted by a database login from a laptop, or granted by the
administrator outside the pipeline; a revert that deletes a migration
a deployed version table names.

**Severity.** medium

## DEL-46 No secret value lands in state or in a plan

**Principle.** No secret value is in Terraform state or a plan: a
generated password comes from an ephemeral generator through a
write-only attribute, or the database service manages it, and a
secret's value is written write-only. A reader of state or of a plan,
the investigator included, then sees no secret.

**Source.** Deployment, Infrastructure as Code.

**Look for.** Every generated password and how it reaches the database;
every secret version and how its value is written; every output that
builds a connection string; what a plan of each root prints.

**Violation.** A secret value in state or in a plan: a generated
password as a plain resource attribute, a secret written through a
readable value, a connection URL with its password computed as an
output or a module input.

**Severity.** high

## DEL-47 The deployer cannot widen itself

**Principle.** Every role the deployer creates carries a named
permissions boundary. The deployer is denied changes to that boundary,
to the deploy and bootstrap roles and trust, and to the bootstrap's
state key, so the widest role a deploy run can mint is the boundary.

**Source.** Deployment, Infrastructure as Code.

**Look for.** The deploy role's fences: the condition that refuses a
role created without the boundary, and the denies on the boundary, on
the deploy and bootstrap roles, on the identity federation's trust,
and on the bootstrap's state key; the boundary every task role carries.

**Violation.** A deploy role that may create a role with no boundary,
or remove a role's boundary; a deploy role that may edit its own
policies, another deploy role, the investigator's trust, or the
identity federation; a deploy role that may write the bootstrap's
state.

**Severity.** high

## DEL-48 Every environment takes the security defaults

**Principle.** Every environment takes the security defaults: private
subnets with a named egress, encryption at rest and TLS to every store,
a trail per account, a stated web-firewall position, a production
database that survives a zone and restores to a point in time, a
protected `main`, a scan on push, and pinned Terraform.

**Source.** Deployment, Security Defaults; Technology Choices and How
to Override Them, Versions.

**Look for.** The network's subnets and egress; the encryption and TLS
settings of the database, the cache, and the buckets; the trail in each
bootstrap root; the web firewall, or the record of why there is none;
production's database: its zones, deletion protection, recovery window,
and final snapshot; the branch protection and the code owners; the
registry's scan setting; `required_version`, the provider constraints,
and the committed `.terraform.lock.hcl` of every root.

**Violation.** A task or a database with a public address; a store
unencrypted at rest, or a database that accepts a connection without
TLS; an account with no trail; a production edge with no firewall and
no record of the choice; a production database in one zone with
customers on it, or with no point-in-time recovery; a `main` that
merges without review; a root with no lock file or an unpinned
provider. (The second factor at sign-in is OPS-07.)

**Severity.** medium

## DEL-49 An error event carries no secret

**Principle.** An event carries no secret. The SDK is initialized with
local variables off and default personal data off, and a scrubber
removes the authorization header, cookies, and every field a request
names as a credential before an event leaves the process.

**Source.** Telemetry, Error Tracking.

**Look for.** The SDK's initialization in every process: the local
variables and personal data options; the scrubber it is handed, and
the headers, cookies, and credential fields it removes.

**Violation.** An SDK initialized with local variables or default
personal data on; a process that sends events with no scrubber; a
scrubber that leaves the authorization header, a cookie, or a field a
request names as a credential in the event.

**Severity.** high
