# Changelog

All notable changes to this repository are listed here. Releases are
tagged `vMAJOR.MINOR.PATCH`; see `CONTRIBUTING.md` for what bumps
which number.

## Unreleased

### Changed

- Scaffold skills: `scaffold-conventions.md` finds the guideline
  version before writing (`plugin.json` against the changelog's first
  release heading; a snapshot between releases is named, not pinned)
  and prints it in the output; tests are counted in cases, not files
  (one contract case per storage method, one refusal per authorization
  rule, one test per rate-limited route, one per exit code, one per
  infra capability); `arch-scaffold-new` pins only a release the two
  agree on, adds the settings check to the infra tests (every
  `InfraSettings` field in `.env.example`), passes every prefix the
  settings read and the database URL as its own secret through
  Terraform, adds the interface check to the OM unit tests (every
  `*Interface` an `ABC` with abstract methods), and ends with
  `arch-review-full` over the tree with every high finding closed; `arch-scaffold-service` adds
  `tests/test_settings.py` (every settings field in `.env.example`,
  every field without a local default set or wired in each Terraform
  environment); `docs/adopting.md` says to update the plugin before a
  scaffold or a review. From the second one-shot run of
  `arch-scaffold-new`. Patch.

### Added

- `architecture.md`: "Multiple impls per interface" says why the
  technology impl and the memory impl pair is a lever rather than a
  cost: a program writes and keeps the memory impl cheaply, the shape
  generalizes (a dict keyed by tenant and id plus the relational
  filters), and the pair is what lets an application run in-process in
  a test, a backend swap at the root, and impls compose; linked to
  "The App Container", "Storage Root", "Composition by decoration",
  and the reference implementation. No lens: a rationale. Patch.
- `architecture.md`: "Scalability by Design", a closing rationale that
  says horizontal scalability is what most of the rules add up to and
  names them by anchor (stateless services, services per namespace,
  `org_id`-first storage, database roles, the work queue and workers
  per lane, the outbox and the idempotent consumer, cache scopes,
  topics and one realtime channel per app, immutability and pure
  rules, the app container): scale out by adding processes, never by
  changing code; linked from the introduction and from "Web Services
  as Scalability Units"; `README.md` summary. No lens: a rationale.
  Patch.
- `architecture.md`: "The App Container" says why the roots are built
  whole, once per process, and never per request or on first use: a
  constructor holds references and opens nothing, so every root builds
  in microseconds, the imports are paid once at module load, and a
  wiring error surfaces at boot, where the process exits and readiness
  never reports ready, rather than at the first request that needs the
  missing piece; linked to "Storage Root", "InfraInterface Root", and
  the reference implementation. No lens: a rationale that `CON-09` and
  `CON-16` already check. Patch.

## 0.4.0 (2026-09-18)

### Added

- `architecture.md`: "Database Roles" lands a core row and its handoff
  (an event row, a work item) in one named atomic method with an outbox
  row that is relayed at once and, after a crash, by the maintenance
  sweep (the transactional outbox); "Storage Principles" derives the
  no-transactions rule and names the invariant test for a named atomic
  method, the outbox row among its cases; "Realtime at the Edge"
  states the `Event` row (`activity` role, `Identifiable` plus
  `org_id`, `seq`, `kind`, `target_id`, a typed payload, `seq`
  assigned per tenant and gapless by the atomic append) and has the
  client keep the last contiguous sequence so a gap is a replay, never
  a skip; the tree gains `events/`; lenses `STO-17`, `ASY-19`, and
  `NET-17` sharpened; lens `NET-22`; `arch-scaffold-new` scaffolds
  `events/` and `outbox/`, and `arch-scaffold-entity` and
  `arch-scaffold-namespace` write through them. Minor.
- `architecture.md`: "Shape of a Worker" makes the lease the first
  fence and completion and every record write conditional on the claim
  (a fencing token), refused with `Conflict` and handed back without
  spending an attempt, and states the liveness beat as a keyed TTL
  written and read back; "Idempotency on the Consumer Side" keeps the
  dedupe marker with its effect (the idempotent consumer); "The Work
  Queue" makes a failed item a dead letter with an audit entry and a
  metric, names competing consumers, and fixes payload shapes per kind
  with `WORK_PAYLOADS`; lenses `ASY-14`, `ASY-16`, and `ASY-17`
  sharpened; `arch-scaffold-worker` fences completion and record
  writes and dead-letters a failed item. Minor.
- `architecture.md`: "Direction of Calls" gives a reservation an expiry
  and a chain that must survive a crash a compensating step per
  reversible step with the irreversible one last (a saga); "Shape of an
  Operation" names last-writer-wins as the default and a `version`
  compare-and-set raising `Conflict` as the opt-in (optimistic
  concurrency); lenses `ASY-20` and `STO-16` sharpened. Minor.
- `architecture.md`: "Migrations" makes every migration compatible
  with the release before it (expand and contract); "Public Types"
  makes a change inside a version additive, with topic payloads and
  realtime envelopes read tolerantly so producers and consumers roll
  out in either order; lenses `STO-18` and `NET-13` sharpened; lens
  `NET-23`. Minor.
- `architecture.md`: "Intra-Service Communication" names the trust
  boundary plain traffic rests on (private subnets, security groups,
  only the gateway public, in Terraform; mutual TLS when the runtime
  gives it away) and has a service-to-service call carry a short-lived
  internal credential the callee's gateway rebuilds `OpContext` from;
  "The Gateway" stores an idempotent response per tenant and principal;
  "Exceptions" adds `NotAuthenticated` (401); lenses `NET-06` and
  `NET-12` sharpened; `arch-scaffold-service` accepts the internal
  credential and raises `NotAuthenticated`. Minor.
- `architecture.md`: "Database Roles" backs up every role on its own
  schedule with a rehearsed restore, purges a soft-deleted row after
  its retention period as the one hard delete, and keeps personal data
  in named fields; lens `STO-17` sharpened; `arch-scaffold-worker`
  purges in the sweep. Minor.
- `architecture.md`: "Tests", a subsection of "Cross-Cutting
  Conventions": unit tests over the memory roots and the pure rules,
  the storage contract cases parameterized by a fixture and run over
  memory in the fast gate and over Postgres in the integration job,
  end-to-end tests over the in-process container with every backend a
  twin, and the `integration`, `e2e`, and `slow` markers; lens
  `DEL-28`. Minor.
- `architecture.md`: "Technology Choices and How to Override Them"
  names FastAPI on uvicorn, httpx, Typer, SQS, and ECS Fargate;
  "Versions" adopts a release at the next scheduled bump, once a patch
  sits behind it; lens `DEL-26` sharpened. Minor.
- `architecture.md`: pattern names where a guarantee rides on one, so
  a reader can look it up: transactional outbox, idempotent consumer,
  fencing token, saga, expand and contract, claim check, competing
  consumers. Patch.

### Changed

- `architecture.md`: "OpContext" carries `user_id` and `org_id`, never
  `User` and `Org` entities, and gains `credential_id`; a manager that
  needs the user loads it, and `opcontext.py` declares `Role`,
  `Permission`, `CredentialKind`, and `AppType` and imports nothing
  above `base.py`, which removes the import cycle the entity fields
  hid. Adopters change the two fields and drop the `TYPE_CHECKING`
  import. Lenses `CTX-02` and `CON-08` sharpened; `arch-scaffold-new`
  follows. Minor.
- `architecture.md`: "Infrastructure Principles" states that the OM
  imports infra interfaces and infra imports nothing from the OM;
  "Topics" makes `TopicPayload` a frozen base infra declares with
  `extra="ignore"`, and names `ENTITY_CHANGED`, the realtime producer;
  the system scope is the zero UUID by value. Adopters drop the
  infra-to-OM dependency. Lenses `ASY-09` and `CON-10` sharpened;
  `arch-scaffold-new` follows. Minor.
- `architecture.md`: "Topics" is best effort (at most once to the
  processes subscribed at the time); queues stay at-least-once;
  "Overriding a Choice" follows; lenses `ASY-10`, `ASY-14`, and
  `DEL-25` sharpened. Minor.
- `architecture.md`: "Direction of Calls" has cross-service
  orchestration compose and never decide, reserving stock an operation
  of the inventory namespace, and gives every service interface an
  in-process impl and a remote impl swapped at wiring time; lenses
  `CON-14` and `CON-15` sharpened; `arch-scaffold-service` names both
  impls. Minor.
- `architecture.md`: "The Work Queue" renames the row's `queue` field
  to `lane` (the inbound `QueueInterface`, the work table, and the
  `queue` role keep their names); adopters rename the column and the
  claim argument; lens `ASY-16` sharpened; `arch-scaffold-worker`
  takes `--lane`. Minor.
- `architecture.md`: "Naming Entities" adds `updated_by` to `Trackable`
  and its mixin, set by every update; "Immutability" states the deep
  freeze (tuples, frozen models, `Mapping`) and re-validation of a copy
  that carries caller input; "Interfaces" makes an interface an `ABC`
  with abstract methods; "Identifiers" gives `EMPTY_UUID` one reading
  (the platform owns the reference) and makes an optional reference
  `None`; "Entities, Value Objects, and Read Models" makes a persisted
  projection derived and rebuildable; lenses `OM-08`, `OM-10`, `OM-13`,
  and `CON-02` sharpened; the scaffold conventions follow. Minor.
- `architecture.md`: "Realtime at the Edge" and "Push-First Apps" call
  the socket buffer a send buffer, so outbox names one thing;
  `OrderBoardView` is `OrderBoard`; a layer is not a swimlane; a
  database role is not a Postgres role; `OpContext` is the operation
  context and `AdminContext` the operator's; `platform` is named as a
  placeholder before its first use; the Push-First principle is stated
  once; "Separation of Layers" precedes "Interfaces" and "Client App
  Architecture" follows "Apps". Patch.
- `lenses/`: severity recalibrated. `high` is reserved for tenancy,
  authorization, lost or duplicated work, and a cross-role breach (30
  of 143); a business decision in a router rises to `high`; the id
  factory, the shape rules, and the boundaries that bend without
  losing work drop to `medium`; `arch-review-full` closes on three or
  more `high` findings, with the count. Patch.
- Scaffolds: an append-only entity gets no update, delete, or
  `Update...Request`; the browser client lives at `apps/<app>/src/api/`
  and the Python client at `clients/python/`, which the CLI and a
  remote service impl import; `AGENTS.md` adds the scaffold audit step.
  Patch.
- `README.md` lens count.

## 0.3.0 (2026-09-17)

### Added

- `architecture.md`: "Error Tracking", a subsection of "Cross-Cutting
  Conventions": every web service, worker, and browser app reports
  errors through the Sentry SDK, tagged with service, release, and
  request id, off until a DSN is set, the browser app reporting from
  each route's error element and the React root; "Traces and Metrics"
  bounds label values and has every process serve `/metrics`, a worker
  on its own port; "Cloud: AWS" collects logs through the log driver
  with retention and metrics and traces through a non-essential
  collector; "The Gateway" answers `/metrics` with a 404 at the load
  balancer; "Local: Docker Compose" adds GlitchTip to `devx`; lens
  `DEL-27`; lenses `DEL-02`, `DEL-04`, `DEL-20`, `NET-10`, and `CON-16`
  sharpened; `arch-scaffold-new`, `arch-scaffold-service`,
  `arch-scaffold-worker`, and `arch-scaffold-app` wire error reporting,
  the worker metrics port, and the collector; `README.md` lens count.
  Minor.
- `architecture.md`: "Cloud: AWS" ships each browser app from a private
  S3 bucket served through CloudFront, declared in Terraform in every
  environment, while the app's calls to the platform stay behind the
  gateway; production promotes browser bundles by build id as it
  promotes images by digest, each bundle reading a `config.json` its
  environment writes; every environment has a base domain with `api.`,
  `app.`, and `admin.` under it; "Stack" separates serving files from
  serving the platform; "The Gateway" accepts cross-origin requests
  only from the browser apps' origins; "Configuration" and "The
  Operator Console" follow; "Technology Choices and How to Override
  Them" names the hosting; lenses `DEL-02`, `DEL-21`, and `NET-06`
  sharpened; `arch-scaffold-app` creates the `static-site` module, the
  config loader, and a build-once deploy, and refuses to finish without
  them; `arch-scaffold-service` adds the allowed origins;
  `arch-scaffold-new` wires the base domain and `api.` and requires the
  portal's deployment. Minor.

### Changed

- `architecture.md`: "Local: Docker Compose" names the developer
  dashboard profile `devx` (pgweb, Valkey Admin, the local images'
  consoles, Jaeger for traces, the metrics view, ports from `.env`) and
  has the repository's `README.md` list the local URL of each
  dashboard, each service's API docs, and each browser app; lens
  `DEL-04` sharpened; `arch-scaffold-new` writes the profile, a
  `devx-up` target, the ports in `.env.example`, and the `Local URLs`
  table in `README.md`; `arch-scaffold-service` and `arch-scaffold-app`
  add their rows to it. Minor.
- `architecture.md`: "Technology Choices and How to Override Them" and
  "Local: Docker Compose" name Valkey as the cache; the `devx` profile
  browses it with Valkey Admin; `arch-scaffold-new` runs the Valkey
  image locally and depends on the `valkey-glide` client. Minor.
- `architecture.md`: "Local: Docker Compose" adds `make seed`, which
  bootstraps a development org and owner from `.env` (an `.example`
  address and a development password), changes nothing on a second
  run, and is listed with the seeded sign-in in `README.md`; "What a
  Process Refuses" refuses the seed against a non-local database;
  lenses `DEL-04` and `DEL-06` sharpened; `arch-scaffold-new` writes
  the target, the seed settings, and the README quick start and runs
  the seed twice; `arch-scaffold-service` adds `bootstrap --seed`.
  Minor.
- `architecture.md`: "Local: Docker Compose" adds the `make up`,
  `make down`, `make reset`, and `make urls` shortcuts; lens `DEL-04`
  looks for them; `arch-scaffold-new` writes them and leads the README
  quick start with `make up`. Minor.

## 0.2.0 (2026-09-17)

### Added

- `architecture.md`: the Software Design and Architecture Guidelines.
- `architecture.md`: "Technology Choices and How to Override Them",
  a section that says why the guideline names technologies and how a
  project records substitutions in one ADR; lens `DEL-25`;
  `docs/adopting.md` step 3; `arch-scaffold-new` writes
  `docs/adr/0002-technology-choices.md`. Minor.
- `architecture.md`: "Next: An End-to-End Reference Implementation",
  a closing pointer to Tadas (<https://github.com/baristaze/tadas>), a
  to-do app for teams that applies the guideline end to end; linked
  from the introduction. Patch.
- `architecture.md`: "Versions", a subsection of "Technology Choices
  and How to Override Them": every dependency runs on its latest
  stable release, the current active LTS line where one exists; linked
  from "Local: Docker Compose" and "Layout Conventions"; lens `DEL-26`;
  `arch-scaffold-new`, `arch-scaffold-service`, and the scaffold
  conventions pin new runtimes, images, and libraries at those
  releases; this repository's CI moves to Python 3.14, Node 24.21.0,
  and the latest action and markdownlint releases (MD060, new in that
  release, is off like MD013). Minor.
- `skills/arch-upgrade-deps`: moves every dependency of a project to
  its latest stable or LTS release from the maintainers' release data,
  runs the fast and integration gates, and holds back an upgrade that
  breaks them.
- `scripts/check_links.py` checks a link whose text wraps across
  lines; before, a line break hid the anchor from the checker.
- `architecture.md`: a generated table of contents (`make gen-toc`,
  checked by `make toc`) and named anchor links at every
  cross-reference and at the first mention of a concept defined later.
- `skills/arch-new-aspect`: incorporates a new aspect into the
  guideline and cascades it through lenses, skills, docs, README, and
  this changelog.
- `lenses/`: 139 review lenses in seven groups, each citing its
  section by title.
- Skills: `arch-review-<group>` for each group, `arch-review-full`,
  `arch-scaffold-new`, `arch-scaffold-namespace`,
  `arch-scaffold-entity`, `arch-scaffold-service`,
  `arch-scaffold-worker`, `arch-scaffold-app`, `arch-explain`,
  `arch-deviate`, `arch-upgrade-deps`.
- `agents/arch-reviewer.md`: the subagent the full review fans out to.
- Plugin and marketplace manifests under `.claude-plugin/`.
- Checkers: markdownlint, lens format and citations, vocabulary leaks,
  links, skill shape, generated-skill freshness; `make check` runs them
  and CI runs `make check`.
- `docs/adopting.md`: how a project adopts the guideline and the
  skills.

### Changed

- Scaffolded CI runs every gate the guideline's "Layout Conventions"
  and "Migrations" name: `arch-scaffold-new` writes `lint`,
  `format-check`, `typecheck`, and `migrate-roundtrip` targets, states
  that `check` runs lint, format, types, and unit tests, and adds the
  round trip to the integration job; `arch-scaffold-app` adds the
  browser app's lint, typecheck, and test scripts to `check`. Patch.
- Scaffold skills sharpened from their first end-to-end run and the
  full review of what they produced: `arch-scaffold-new` accepts a
  fresh repository as its target, writes `specs/architecture.md`, the
  `events` and `idempotency` namespaces, the migration module, the
  contract-test layout, the Terraform modules and the deploy workflow,
  and states the om/infra workspace dependency and where the role table
  lives; `arch-scaffold-service` declares service interfaces from the
  single-process start, a durable edge idempotency, a server span, a
  boot helper, rate limits from settings, and the `after_seq` replay
  route; `arch-scaffold-worker` gains a container, a `health`
  subcommand, `rules.py`, bounded lease renewal, conditional writes,
  and a per-tenant sweep; `arch-scaffold-app` moves the client package
  to `clients/`, adds the sign-in screen, the lint config, and the
  sequence-gap replay; `arch-scaffold-entity` composes the feed mixin
  variant instead of redeclaring `org_id` and reads the current entity
  before an update. Patch.
- Sections are unnumbered. Every reference in the repository names a
  section by title; `scripts/check_lenses.py` refuses a number. The
  two `Principles` subsections are `Storage Principles` and
  `Infrastructure Principles` so every anchor is unique.
- Every tree diagram in the guideline is a fenced `text` block.
- Root snippets (`StorageInterface`, `ServicesInterface`,
  `InfraInterface`, `Queues`) show two entries and a `# ...` line.
